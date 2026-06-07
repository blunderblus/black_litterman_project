"""고정 스케일러 — train 구간 fit 파라미터 저장/재사용(추론배치 정규화 누수 금지, ADR-0004)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def fit_scaler(df: "pd.DataFrame", cols: list[str], path: str) -> dict:
    """train 통계로 스케일러 적합·artifacts 저장."""
    raise NotImplementedError("P3에서 구현 — 설계 문서 참조")


def apply_scaler(df: "pd.DataFrame", path: str) -> "pd.DataFrame":
    """저장된 train 기준 파라미터로만 변환(현재 배치 통계 사용 금지)."""
    raise NotImplementedError("P3에서 구현 — 설계 문서 참조")
