"""검증 — walk-forward 시점분리 split·평가(누수 차단, ADR-0004). in-sample 평가 금지."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def walk_forward_splits(df: "pd.DataFrame", time_col: str, n_splits: int = 4) -> list:
    """전진(walk-forward) train/valid/test 윈도우 생성."""
    raise NotImplementedError("P3에서 구현 — 설계 문서 참조")


def evaluate(y_true, y_score) -> dict:
    """AUC/Precision@K/calibration 등 out-of-sample 지표 산출."""
    raise NotImplementedError("P3에서 구현 — 설계 문서 참조")
