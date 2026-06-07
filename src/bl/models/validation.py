"""검증 — walk-forward 시점분리 split·평가(누수 차단, ADR-0004). in-sample 평가 금지.

설계 §9. 과거 토이 결함(학습=평가, train/test 분리 없음 → AUC 0.99 누수) 교정.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from bl.common.dates import ym_add

if TYPE_CHECKING:
    import pandas as pd


def walk_forward_splits(
    df: pd.DataFrame,
    time_col: str = "base_ym",
    n_splits: int = 3,
    min_train_periods: int = 6,
    embargo: int = 0,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """전진(expanding-window) train/test 인덱스 쌍 목록을 만든다.

    각 분할에서 과거(train)로 학습하고 다음 기간(test)을 평가한다. ``embargo``(개월)는
    라벨 호라이즌만큼 test 직전 train 기간을 제외해 **호라이즌 겹침 누수**를 차단한다
    (라벨이 base_ym+horizon 미래잔액에서 나오므로, test_start−horizon 이후 train은 test를 들여다봄).
    """
    periods = np.sort(df[time_col].unique())
    if len(periods) <= min_train_periods:
        return []
    test_periods = periods[min_train_periods:]
    if len(test_periods) == 0:
        return []
    chunks = np.array_split(test_periods, min(n_splits, len(test_periods)))
    idx = df[time_col].to_numpy()
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    for ch in chunks:
        if len(ch) == 0:
            continue
        test_start = int(ch[0])
        cutoff = ym_add(test_start, -embargo) if embargo else test_start  # 호라이즌 embargo
        train_mask = idx < cutoff
        test_mask = np.isin(idx, ch)
        if train_mask.sum() > 0 and test_mask.sum() > 0:
            splits.append((np.where(train_mask)[0], np.where(test_mask)[0]))
    return splits


def evaluate(y_true, y_score) -> dict:
    """out-of-sample 지표(AUC, Precision@K). 단일 클래스면 auc=None."""
    from sklearn.metrics import roc_auc_score

    yt = np.asarray(y_true, dtype="float64")
    ys = np.asarray(y_score, dtype="float64")
    finite = np.isfinite(yt) & np.isfinite(ys)        # 단일 유한 마스크로 일관 필터
    yt, ys = yt[finite], ys[finite]
    out: dict = {"n": int(len(yt)), "positives": int(yt.sum())}
    out["auc"] = None if len(np.unique(yt)) < 2 else float(roc_auc_score(yt, ys))
    k = max(1, int(0.2 * len(yt))) if len(yt) else 0
    out["precision_at_k"] = float(np.mean(yt[np.argsort(ys)[-k:]])) if k else None
    return out
