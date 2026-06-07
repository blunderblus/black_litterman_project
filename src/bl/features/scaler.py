"""고정 스케일러 — train 구간 fit 파라미터 저장/재사용(추론배치 정규화 누수 금지, ADR-0004).

추론 시 현재 배치 통계로 재정규화하면 누수·실행간 비교불가가 발생한다(토이 결함). train에서
적합한 (mean,std)를 JSON으로 저장하고, 추론에는 그 파라미터만 적용한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import pandas as pd


def fit_scaler(df: pd.DataFrame, cols: list[str], path: str | Path | None = None) -> dict:
    """train df의 cols에 대해 z-score 파라미터 {col:[mean,std]} 적합. path 주면 JSON 저장."""
    params: dict[str, list[float]] = {}
    for c in cols:
        x = df[c].to_numpy(dtype="float64")
        if x.size == 0 or bool(np.all(np.isnan(x))):     # 전부-NaN/빈 컬럼 → 중립(0,1)
            params[c] = [0.0, 1.0]
            continue
        mu, sd = float(np.nanmean(x)), float(np.nanstd(x))
        params[c] = [mu, sd if sd > 1e-12 else 1.0]
    if path is not None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    return params


def load_scaler(path: str | Path) -> dict:
    """저장된 스케일러 파라미터를 로딩한다."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def apply_scaler(df: pd.DataFrame, scaler: dict | str | Path) -> pd.DataFrame:
    """저장된 train 기준 파라미터로만 표준화한다(현재 배치 통계 사용 금지)."""
    params = scaler if isinstance(scaler, dict) else load_scaler(scaler)
    out = df.copy()
    for c, (mu, sd) in params.items():
        if c in out.columns:
            out[c] = (out[c].to_numpy(dtype="float64") - mu) / (sd if sd > 1e-12 else 1.0)
    return out
