"""BL 입력 — Sigma·Pi(=lambda*Sigma*w_mkt)·P·Q(4축)·Omega(prop 1/DRI^2)·w_mkt·tau → bl_input_data. 단위 정합. (과거: 09, 03 §4-5)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def build_bl_inputs(con: "duckdb.DuckDBPyConnection", settings: "Settings", base_ym: int) -> dict:
    """ml_master+ML_PREDICTIONS+COMPANY_SENTIMENT+post_data 결합 -> BL 입력 dict(parquet 저장)."""
    raise NotImplementedError("P4에서 구현 — 설계 문서 참조")
