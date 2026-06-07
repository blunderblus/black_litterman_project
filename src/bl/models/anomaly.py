"""이상탐지 — IsolationForest. 자금흐름 방향과 결합해 anomaly view 생성. (과거: 08)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def train(train_set: "pd.DataFrame", settings: "Settings") -> object:
    """IsolationForest 적합(재무 유무 그룹)."""
    raise NotImplementedError("P3에서 구현 — 설계 문서 참조")


def score(model: object, inference_set: "pd.DataFrame") -> "pd.DataFrame":
    """anomaly_score_raw 산출(부호=trx_in-trx_out 방향)."""
    raise NotImplementedError("P3에서 구현 — 설계 문서 참조")
