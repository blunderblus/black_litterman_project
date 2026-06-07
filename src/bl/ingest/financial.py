"""재무 수집 — OpenDART REST(fnlttSinglAcntAll) → financial_wide 프레임.

설계 02 §1. 키 게이팅(BL_DART_API_KEY). status='000' & list 비어있지 않을 때만 적재
(빈 응답을 0으로 오해 금지). 파싱(parse_fnlttSinglAcntAll)을 분리해 단위 테스트한다.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pandas as pd

from bl.common.logging import get_logger

if TYPE_CHECKING:
    from bl.common.config import Settings

log = get_logger(__name__)

DART_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
# IFRS account_id → FINANCIAL_WIDE 컬럼
ACCOUNT_MAP = {
    "ifrs-full_Revenue": "revenue",
    "ifrs-full_OperatingIncomeLoss": "operating_profit",
    "ifrs-full_ProfitLoss": "net_income",
    "ifrs-full_Assets": "total_assets",
    "ifrs-full_Liabilities": "total_liabilities",
    "ifrs-full_Equity": "total_equity",
    "ifrs-full_CashAndCashEquivalents": "cash_amount",
}


def _to_num(s: object) -> float | None:
    t = str(s).strip().replace(",", "")
    neg = t.startswith("(") and t.endswith(")")     # 괄호 음수 표기 (1,234) → -1234
    if neg:
        t = t[1:-1]
    try:
        v = float(t)
    except (TypeError, ValueError):
        return None
    return -v if neg else v


def parse_fnlttSinglAcntAll(payload: dict, corp_code: str, base_ym: int) -> dict | None:
    """DART 응답 → financial_wide 1행 dict. status!='000'/빈 list면 None(순수·테스트 가능)."""
    if (payload or {}).get("status") != "000":
        return None
    items = payload.get("list") or []
    if not items:
        return None
    row: dict = {"corp_code": corp_code, "base_ym": base_ym}
    for it in items:
        col = ACCOUNT_MAP.get(it.get("account_id"))
        if col and col not in row:
            v = _to_num(it.get("thstrm_amount"))
            if v is not None:
                row[col] = v
    # 핵심 컬럼이 하나도 없으면 무효
    return row if len(row) > 2 else None


def collect_financial(
    settings: "Settings", corp_codes: list[str], years: list[int], fs_div: str = "CFS"
) -> pd.DataFrame:
    """corp_code별 최신 사업보고서(reprt_code=11011) 재무를 수집해 financial_wide 반환(라이브, 키 필요)."""
    from bl.common.http import get_json

    key = settings.dart_api_key.get_secret_value() if settings.dart_api_key else None
    if not key:
        raise ValueError("BL_DART_API_KEY 미설정 — 재무 수집 불가(데모는 sample 사용).")
    rows: list[dict] = []
    for cc in corp_codes:
        for yr in sorted(years, reverse=True):
            payload = get_json(DART_URL, params={
                "crtfc_key": key, "corp_code": cc, "bsns_year": str(yr),
                "reprt_code": "11011", "fs_div": fs_div,
            })
            parsed = parse_fnlttSinglAcntAll(payload, cc, yr * 100 + 12)
            if parsed:
                rows.append(parsed)
                break                                # 최신 연도 성공 시 종료
            time.sleep(0.05)                          # DART 레이트리밋 완화
    log.info("DART 재무 수집", extra={"stage": "ingest.financial", "corps": len(corp_codes), "rows": len(rows)})
    return pd.DataFrame(rows)
