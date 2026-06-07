"""대시보드 마트 — bl_dashboard_mart 구성(컬럼 권위 스키마: 02 §3.2.3). 자산 dedup, 라벨 단일소스. (과거: 11)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def build_mart(con: "duckdb.DuckDBPyConnection", settings: "Settings", base_ym: int) -> "pd.DataFrame":
    """BL 결과+메타 결합 -> bl_dashboard_mart(parquet/CSV). pickle 금지."""
    raise NotImplementedError("P4에서 구현 — 설계 문서 참조")
