"""성장/이탈 — XGBoost. scale_pos_weight 동적, **walk-forward 검증으로 confidence 산출**.

설계 §6, ADR-0004. 과거 토이 결함 교정: confidence 하드코딩(0.85/0.65) 폐기 →
walk-forward out-of-sample AUC 를 신뢰도로 사용(미검증 수치 단정 금지). in-sample 평가 금지.
XGBoost는 스케일 불변이라 별도 스케일러 불필요(누수 차단은 시점분리로 보장).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from bl.common.logging import get_logger
from bl.features.builder import FEATURE_COLS, LABEL_HORIZON
from bl.models.validation import evaluate, walk_forward_splits

if TYPE_CHECKING:
    import pandas as pd

    from bl.common.config import Settings

log = get_logger(__name__)
LABELS = {"growth": "label_growth", "churn": "label_churn"}


def _xgb(seed: int, scale_pos_weight: float):
    from xgboost import XGBClassifier

    return XGBClassifier(
        n_estimators=120, max_depth=4, learning_rate=0.1, subsample=0.9,
        colsample_bytree=0.9, scale_pos_weight=scale_pos_weight, random_state=seed,
        eval_metric="logloss", n_jobs=2, tree_method="hist",
    )


def _spw(y: np.ndarray) -> float:
    pos = float((y == 1).sum())
    neg = float((y == 0).sum())
    return (neg / pos) if pos > 0 else 1.0


def _cv_auc(df: "pd.DataFrame", label_col: str, feats: list[str], seed: int) -> float | None:
    """walk-forward out-of-sample 평균 AUC(없으면 None). confidence 의 경험적 근거."""
    aucs = []
    for tr, te in walk_forward_splits(df, "base_ym", n_splits=3, embargo=LABEL_HORIZON):
        ytr = df.iloc[tr][label_col].to_numpy("float64")
        if len(np.unique(ytr)) < 2:
            continue
        m = _xgb(seed, _spw(ytr))
        m.fit(df.iloc[tr][feats], ytr)
        ys = m.predict_proba(df.iloc[te][feats])[:, 1]
        r = evaluate(df.iloc[te][label_col].to_numpy("float64"), ys)
        if r["auc"] is not None:
            aucs.append(r["auc"])
    return float(np.mean(aucs)) if aucs else None


def train(train_set: "pd.DataFrame", settings: "Settings | None" = None, seed: int = 42) -> dict:
    """성장·이탈 분류기를 학습한다. 반환: {target: {model, confidence, features}}.

    confidence 는 walk-forward AUC(out-of-sample). 검증 불가(소표본/단일클래스) 시 0.5(미지).
    """
    feats = [c for c in FEATURE_COLS if c in train_set.columns]
    models: dict = {}
    for target, label_col in LABELS.items():
        y = train_set[label_col].to_numpy("float64")
        mask = ~np.isnan(y)
        dfm = train_set[mask]
        ym = y[mask]
        if len(np.unique(ym)) < 2:
            log.warning(f"{target}: 라벨 단일 클래스 → 학습 생략", extra={"stage": "models.growth_churn"})
            models[target] = {"model": None, "confidence": 0.5, "features": feats}
            continue
        cv = _cv_auc(dfm, label_col, feats, seed)
        m = _xgb(seed, _spw(ym))
        m.fit(dfm[feats], ym)                         # 최종 적합은 전체 train(검증은 위 walk-forward)
        models[target] = {
            "model": m,
            "confidence": float(cv) if cv is not None else 0.5,
            "features": feats,
        }
    return models


def predict(models: dict, inference_set: "pd.DataFrame") -> "pd.DataFrame":
    """prob_growth_raw/prob_churn_raw + confidence_* 산출(corp_code 단위)."""
    out = inference_set[["corp_code"]].copy()
    for target, label_col in LABELS.items():
        spec = models.get(target, {})
        m = spec.get("model")
        feats = spec.get("features", [c for c in FEATURE_COLS if c in inference_set.columns])
        col = "prob_growth_raw" if target == "growth" else "prob_churn_raw"
        if m is None:
            out[col] = 0.5
        else:
            out[col] = m.predict_proba(inference_set[feats])[:, 1]
        out[f"confidence_{target}"] = float(spec.get("confidence", 0.5))
    return out
