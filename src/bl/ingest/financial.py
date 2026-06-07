"""재무 수집 — OpenDART REST(fnlttSinglAcntAll) → RAW_FINANCIAL/FINANCIAL_WIDE(이중적재). (과거: 01_collect.ipynb)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def collect_financial(con: "duckdb.DuckDBPyConnection", settings: "Settings") -> int:
    """corp_code 단위 재무 수집·멱등 적재. 쿼터/백오프 처리."""
    raise NotImplementedError("P1에서 구현 — 설계 문서 참조")
