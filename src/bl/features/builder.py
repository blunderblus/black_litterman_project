"""피처/라벨 — 시점 분리 시계열·재무·매크로·감성 결합 → train_set/inference_set. look-ahead 차단(ADR-0004). (과거: 07)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def build_features(con: "duckdb.DuckDBPyConnection", settings: "Settings") -> "pd.DataFrame":
    """crosswalk 경유 결합 + 시점분리 피처/라벨. train/inference parquet 산출."""
    raise NotImplementedError("P3에서 구현 — 설계 문서 참조")
