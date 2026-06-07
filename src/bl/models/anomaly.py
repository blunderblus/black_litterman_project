"""이상탐지 — IsolationForest. anomaly_score_raw ∈ [0,1] (train-fit 고정 경계로 정규화).

설계 §6, ADR-0004. 과거 토이 결함 교정: 추론 배치 min/max 정규화 누수 폐기 →
train 적합 시점의 score 분포 경계를 저장해 추론에 동일 적용. 방향 부호는 BL 입력 단계에서
trx_in−trx_out 과 결합한다(여기서는 크기[0,1]만 산출).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from bl.features.builder import FEATURE_COLS
from bl.features.scaler import apply_scaler, fit_scaler

if TYPE_CHECKING:
    import pandas as pd

    from bl.common.config import Settings


def train(train_set: pd.DataFrame, settings: Settings | None = None, seed: int = 42) -> dict:
    """IsolationForest 적합 + **train-fit 고정 스케일러**(IForest는 스케일 민감) + score 경계 저장."""
    from sklearn.ensemble import IsolationForest

    feats = [c for c in FEATURE_COLS if c in train_set.columns]
    scaler = fit_scaler(train_set, feats)             # train 통계로만 적합(배치통계 누수 금지)
    x = apply_scaler(train_set[feats].fillna(0.0), scaler)[feats].to_numpy("float64")
    iso = IsolationForest(n_estimators=200, contamination="auto", random_state=seed, n_jobs=2)
    iso.fit(x)
    raw = -iso.score_samples(x)                       # 클수록 이상(양수 방향 통일)
    lo, hi = float(np.min(raw)), float(np.max(raw))
    return {"model": iso, "features": feats, "bounds": [lo, hi], "scaler": scaler}


def score(model: dict, inference_set: pd.DataFrame) -> pd.DataFrame:
    """anomaly_score_raw ∈ [0,1] 산출(train 고정 스케일러·경계, 배치통계 미사용)."""
    iso = model["model"]
    feats = model["features"]
    lo, hi = model["bounds"]
    x = apply_scaler(inference_set[feats].fillna(0.0), model["scaler"])[feats].to_numpy("float64")
    raw = -iso.score_samples(x)
    span = (hi - lo) if (hi - lo) > 1e-12 else 1.0
    norm = np.clip((raw - lo) / span, 0.0, 1.0)       # train 경계 기준 정규화
    out = inference_set[["corp_code"]].copy()
    out["anomaly_score_raw"] = norm
    return out
