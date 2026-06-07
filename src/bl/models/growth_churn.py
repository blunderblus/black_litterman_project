"""성장/이탈 — XGBoost. 재무 유무 2그룹, scale_pos_weight 동적. 시점분리 검증(in-sample 금지). (과거: 08)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def train(train_set: "pd.DataFrame", settings: "Settings") -> object:
    """XGBoost 성장/이탈 학습(시점분리). 모델·고정스케일러 저장."""
    raise NotImplementedError("P3에서 구현 — 설계 문서 참조")


def predict(model: object, inference_set: "pd.DataFrame") -> "pd.DataFrame":
    """prob_growth_raw/prob_churn_raw + 캘리브레이션 confidence 산출."""
    raise NotImplementedError("P3에서 구현 — 설계 문서 참조")
