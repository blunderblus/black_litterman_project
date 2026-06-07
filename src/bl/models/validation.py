"""검증 — walk-forward 시점분리 split·평가(누수 차단, ADR-0004). in-sample 평가 금지.

설계 §9. 과거 토이 결함(학습=평가, train/test 분리 없음 → AUC 0.99 누수) 교정.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import pandas as pd


def walk_forward_splits(
    df: "pd.DataFrame", time_col: str = "base_ym", n_splits: int = 3, min_train_periods: int = 6
) -> list[tuple[np.ndarray, np.ndarray]]:
    """전진(expanding-window) train/test 인덱스 쌍 목록을 만든다.

    시간순 unique 기간을 나눠, 각 분할에서 과거(train)로 학습하고 다음 기간(test)을 평가한다.
    미래 정보가 train에 새지 않는다.
    """
    periods = np.sort(df[time_col].unique())
    if len(periods) <= min_train_periods:
        return []
    test_periods = periods[min_train_periods:]
    if len(test_periods) == 0:
        return []
    # test 구간을 n_splits 개로 분배
    chunks = np.array_split(test_periods, min(n_splits, len(test_periods)))
    idx = df[time_col].to_numpy()
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    for ch in chunks:
        if len(ch) == 0:
            continue
        test_start = ch[0]
        train_mask = idx < test_start
        test_mask = np.isin(idx, ch)
        if train_mask.sum() > 0 and test_mask.sum() > 0:
            splits.append((np.where(train_mask)[0], np.where(test_mask)[0]))
    return splits


def evaluate(y_true, y_score) -> dict:
    """out-of-sample 지표(AUC, Precision@K). 단일 클래스면 auc=None."""
    from sklearn.metrics import roc_auc_score

    yt = np.asarray(y_true, dtype="float64")
    ys = np.asarray(y_score, dtype="float64")
    out: dict = {"n": int(len(yt)), "positives": int(np.nansum(yt))}
    if len(np.unique(yt[~np.isnan(yt)])) < 2:
        out["auc"] = None
    else:
        out["auc"] = float(roc_auc_score(yt, ys))
    k = max(1, int(0.2 * len(yt)))
    topk = np.argsort(ys)[-k:]
    out["precision_at_k"] = float(np.mean(yt[topk])) if k else None
    return out
