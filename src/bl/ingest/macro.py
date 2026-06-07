"""매크로 수집(Track A) — 한국은행 ECOS → macro 프레임(metric_code, base_ym, value).

설계 02 §1. 키 게이팅(BL_ECOS_API_KEY). 라이브 호출은 본 환경에서 미검증이라 파싱(parse_ecos)을
분리해 단위 테스트한다. 키 없으면 collect_macro 가 명확히 거부한다(데모는 sample 사용).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from bl.common.logging import get_logger

if TYPE_CHECKING:
    from bl.common.config import Settings

log = get_logger(__name__)

ECOS_URL = "https://ecos.bok.or.kr/api/StatisticSearch"
# 수집 통계표(코드, 산출 metric_code). 실제 코드는 ECOS 통계표 기준으로 조정.
ECOS_STATS = [
    ("722Y001", "0101000", "BASE_RATE"),   # 한국은행 기준금리
    ("817Y002", "010200000", "KTB3Y"),     # 국고채 3년
]


def parse_ecos(payload: dict, metric_code: str) -> list[dict]:
    """ECOS StatisticSearch 응답 → [{metric_code, base_ym, value}] (순수·테스트 가능).

    TIME 'YYYYMM'(월) 또는 'YYYYMMDD'(일)을 base_ym(YYYYMM)으로 정규화.
    """
    rows = (payload or {}).get("StatisticSearch", {}).get("row", [])
    out: list[dict] = []
    for r in rows:
        t = str(r.get("TIME", ""))
        if len(t) >= 6 and t[:6].isdigit():
            base_ym = int(t[:6])
        else:
            continue
        try:
            value = float(r["DATA_VALUE"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"metric_code": metric_code, "base_ym": base_ym, "value": value})
    return out


def collect_macro(settings: "Settings", start_ym: int, end_ym: int) -> pd.DataFrame:
    """ECOS에서 매크로 시계열을 수집해 macro 프레임 반환(라이브, 키 필요)."""
    from bl.common.http import get_json

    key = settings.ecos_api_key.get_secret_value() if settings.ecos_api_key else None
    if not key:
        raise ValueError("BL_ECOS_API_KEY 미설정 — 매크로 수집 불가(데모는 sample 사용).")
    rows: list[dict] = []
    for stat_code, item_code, metric in ECOS_STATS:
        url = f"{ECOS_URL}/{key}/json/kr/1/1000/{stat_code}/M/{start_ym}/{end_ym}/{item_code}"
        rows.extend(parse_ecos(get_json(url), metric))
        log.info("ECOS 수집", extra={"stage": "ingest.macro", "metric": metric, "rows": len(rows)})
    return pd.DataFrame(rows, columns=["metric_code", "base_ym", "value"])
