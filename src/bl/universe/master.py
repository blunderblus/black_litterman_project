"""유니버스 마스터 — TARGET_MASTER 구성/복원, Tier 부여, crosswalk 기준.

과거 노트북: TARGET_MASTER.ipynb. 설계: docs/design/02-data-pipeline.md §2(universe).

핵심:
- canonical key = corp_code(=TARGET_ID). 모든 후보 소스를 corp_code로 모은다.
- Tier: T3=가상 섹터 노드(IS_VIRTUAL) / T1=상장(stock_code 6자리) / T2=그 외(비상장).
- 멱등 적재(io.upsert, key=TARGET_ID). DART 기업개황 enrich(이름/주소 등)는 후속(키 필요).

P1 코드리뷰 반영: stock_code 숫자형/전각 정규화로 T1 오분류 방지, TARGET_NAME(없으면
corp_code 대체) 채움, 커버리지 게이트, IS_VIRTUAL↔stock_code 모순 카운트.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from bl.common import identifiers as ids
from bl.common.io import upsert
from bl.common.logging import get_logger, log_stage

if TYPE_CHECKING:
    import pandas as pd

    from bl.common.config import Settings

log = get_logger(__name__)

TARGET_MASTER_TABLE = "TARGET_MASTER"
# 매핑 실패(UNKNOWN) 잠정 허용 임계(ADR-0003; 캘리브레이션 전 잠정값).
DEFAULT_MAX_UNKNOWN_RATIO = 0.05


def normalize_stock_code(stock_code: object) -> str | None:
    """종목코드를 표준 6자리 ASCII 숫자 문자열로 정규화한다(불가하면 None).

    - 숫자형/문자열 모두 처리. '.0' 접미사 제거. ASCII 숫자 1~6자리면 zero-pad(6).
      (KRX 코드는 6자리; 숫자 저장 시 선행 0 손실을 복원) 전각 숫자/비숫자는 None 취급(비-T1).
    """
    if stock_code is None:
        return None
    if isinstance(stock_code, float) and math.isnan(stock_code):
        return None
    s = str(stock_code).strip()
    if s.lower() in ("", "nan", "none", "<na>", "null"):
        return None
    s = re.sub(r"\.0$", "", s)
    if re.fullmatch(r"[0-9]{1,6}", s):  # ASCII 숫자 ≤6자리 → KRX 코드로 보고 zero-pad
        return s.zfill(6)
    return s  # 그 외(7자리+/비숫자/전각)는 그대로 두되 T1 판정에서 탈락


def assign_tier(stock_code: object, is_virtual: bool = False) -> str:
    """단일 자산의 Tier를 부여한다.

    T3: 가상 섹터 노드(is_virtual) / T1: 상장(정규화 후 6자리 ASCII 숫자) / T2: 비상장.
    """
    if is_virtual:
        return "T3"
    s = normalize_stock_code(stock_code)
    return "T1" if (s is not None and re.fullmatch(r"[0-9]{6}", s)) else "T2"


def build_master_frame(candidates: "Sequence[pd.DataFrame]") -> "pd.DataFrame":
    """후보 소스들로부터 TARGET_MASTER DataFrame을 조립한다(적재 전, 순수 함수).

    candidates: corp_code 를 가진 DataFrame들(선택적으로 biz_reg_no/jurir_no/stock_code/
    IS_VIRTUAL/TARGET_NAME). Returns: TARGET_ID, corp_code, TARGET_NAME, biz_reg_no,
    jurir_no, stock_code, IS_VIRTUAL, TIER.
    """
    xwalk = ids.build_crosswalk(candidates)

    # corp_code별 IS_VIRTUAL(OR 집계) / TARGET_NAME(첫 비결측) 수집
    virtual_map: dict[str, bool] = {}
    name_map: dict[str, str] = {}
    for src in candidates:
        if ids.CANONICAL_KEY not in src.columns:
            continue
        cc = ids._clean_id(src[ids.CANONICAL_KEY])
        if "IS_VIRTUAL" in src.columns:
            for k, v in zip(cc, src["IS_VIRTUAL"], strict=True):
                if isinstance(k, str) and k:
                    virtual_map[k] = bool(virtual_map.get(k, False) or bool(v))
        if "TARGET_NAME" in src.columns:
            for k, v in zip(cc, src["TARGET_NAME"], strict=True):
                if isinstance(k, str) and k and k not in name_map and isinstance(v, str) and v.strip():
                    name_map[k] = v.strip()

    m = xwalk.copy()
    m["stock_code"] = m["stock_code"].map(normalize_stock_code)
    m["IS_VIRTUAL"] = m[ids.CANONICAL_KEY].map(lambda c: virtual_map.get(c, False))
    m["TIER"] = [
        assign_tier(sc, iv) for sc, iv in zip(m["stock_code"], m["IS_VIRTUAL"], strict=True)
    ]
    # TARGET_NAME: enrich 전이므로 없으면 corp_code 로 대체(§3.1.1 규칙, NOT NULL 보장)
    m["TARGET_NAME"] = m[ids.CANONICAL_KEY].map(lambda c: name_map.get(c, c))
    m.insert(0, "TARGET_ID", m[ids.CANONICAL_KEY])
    return m[
        ["TARGET_ID", "corp_code", "TARGET_NAME", "biz_reg_no", "jurir_no",
         "stock_code", "IS_VIRTUAL", "TIER"]
    ]


def build_target_master(
    con,
    settings: "Settings | None",
    candidates: "Sequence[pd.DataFrame] | None" = None,
    max_unknown_ratio: float = DEFAULT_MAX_UNKNOWN_RATIO,
) -> int:
    """TARGET_MASTER 를 구성하고 DuckDB에 멱등 적재한다. 적재 행 수 반환.

    candidates 미지정 시 후속 단계에서 DuckDB 후보 테이블을 읽도록 확장한다(현재는 명시 주입).
    커버리지 게이트(ADR-0003)와 IS_VIRTUAL↔stock_code 모순 점검을 수행한다.
    """
    if not candidates:
        raise NotImplementedError(
            "DuckDB 후보 테이블 자동 수집은 ingest 연동 후 구현 — 현재는 candidates 주입 필요"
        )
    master = build_master_frame(candidates)

    # 커버리지 게이트: corp_code 결측은 0이어야 함(앵커 누락 회귀 가드, ADR-0003)
    ids.assert_coverage(master, max_unknown_ratio, key=ids.CANONICAL_KEY, on_fail="raise")

    # 데이터 오류 가드: 가상노드인데 유효 6자리 stock_code 보유(모순)
    conflict = int(
        (
            master["IS_VIRTUAL"]
            & master["stock_code"].map(lambda s: bool(s) and bool(re.fullmatch(r"[0-9]{6}", str(s))))
        ).sum()
    )

    n = upsert(con, TARGET_MASTER_TABLE, master, keys=["TARGET_ID"])
    tier_counts = master["TIER"].value_counts().to_dict()
    log_stage(
        log,
        "universe.master",
        output_rows=n,
        t1=int(tier_counts.get("T1", 0)),
        t2=int(tier_counts.get("T2", 0)),
        t3=int(tier_counts.get("T3", 0)),
        virtual_stock_conflict=conflict,
    )
    return n
