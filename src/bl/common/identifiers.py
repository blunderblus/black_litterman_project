"""ID crosswalk — 식별자 정규화. canonical key = ``corp_code`` (ADR-0003).

과거 토이 치명 결함: ``biz_reg_no``(사업자등록번호)를 ``jurir_no``(법인등록번호)와
직접 조인 → 추정 99.4% 데이터 소실(tier=UNKNOWN). 격상판은 **모든 결합을 crosswalk
경유로 corp_code 로 정규화한 뒤에만** 수행하며, biz_reg_no↔jurir_no 직접 조인을 금지한다.

설계: docs/design/02-data-pipeline.md §4, docs/planning/04-glossary.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

# crosswalk 표준 컬럼. corp_code 가 canonical(허브) 키.
CROSSWALK_COLUMNS: tuple[str, ...] = ("corp_code", "biz_reg_no", "jurir_no", "stock_code")
CANONICAL_KEY = "corp_code"

# 직접 조인이 금지된 위험 키 쌍(정적 점검/리뷰용).
FORBIDDEN_DIRECT_JOINS: tuple[tuple[str, str], ...] = (
    ("biz_reg_no", "jurir_no"),
    ("jurir_no", "biz_reg_no"),
)


def build_crosswalk(sources: "Sequence[pd.DataFrame]") -> "pd.DataFrame":
    """여러 소스에서 corp_code 중심의 ID crosswalk 테이블을 구성한다.

    출력 컬럼: CROSSWALK_COLUMNS. corp_code 는 NOT NULL·UNIQUE.
    TODO(P1): DART corpCode + 내부 매핑 병합, 충돌 해소 규칙.
    """
    raise NotImplementedError("P1(ID crosswalk)에서 구현 — 설계 02-data-pipeline §4")


def normalize_to_corp_code(
    df: "pd.DataFrame",
    on: str,
    crosswalk: "pd.DataFrame",
) -> "pd.DataFrame":
    """``on`` 키(biz_reg_no/jurir_no/stock_code)를 crosswalk로 corp_code 로 정규화한다.

    매칭 실패 행은 보존하되 UNKNOWN으로 표시하고 커버리지를 로깅한다(임계 초과 시 빌드 실패).
    TODO(P1): 결합·커버리지 게이트 구현.
    """
    raise NotImplementedError("P1(ID crosswalk)에서 구현 — 설계 02-data-pipeline §4.3")
