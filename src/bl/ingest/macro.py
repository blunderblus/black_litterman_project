"""매크로 수집(Track A) — ECOS(금리·BSI)·FinanceDataReader(지수) → RAW_MACRO. (과거: 02_track_A.ipynb)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def collect_macro(con: "duckdb.DuckDBPyConnection", settings: "Settings") -> int:
    """ECOS/FDR 매크로·지수를 (METRIC_CODE,DATE) 키로 멱등 upsert. ECOS 키는 설정 주입(로그 마스킹)."""
    raise NotImplementedError("P1에서 구현 — 설계 문서 참조")
