"""eval.calibrate — BL 하이퍼파라미터의 실현지표 기반 역산 테스트(소형 그리드·step 크게).

설계 §5.2/§5.4/§11, ADR-0004 §5. '가설값 → 검증된 추정값' 전환의 회귀 가드.
"""

from __future__ import annotations

import numpy as np

from bl.eval import calibrate as cal
from bl.pipeline import _load_sample


def test_sweep_tau_structure() -> None:
    frames = _load_sample("data/sample")
    df, best = cal.sweep_tau(frames, taus=(0.05, 0.1), step=6)
    assert len(df) == 2
    assert {"tau", "mean_ic", "lift_bl_vs_market"} <= set(df.columns)
    assert best.get("tau") in (0.05, 0.1)


def test_calibrate_axis_weights_structure() -> None:
    frames = _load_sample("data/sample")
    grid = [dict(cal.AXIS_WEIGHTS),
            {"news": 0.25, "pattern": 0.45, "anomaly": 0.15, "relationship": 0.15}]
    df, best = cal.calibrate_axis_weights(frames, grid=grid, step=6)
    assert len(df) == 2
    assert {"news", "pattern", "anomaly", "relationship", "mean_ic"} <= set(df.columns)
    assert np.isfinite(best["mean_ic"])
    s = best["news"] + best["pattern"] + best["anomaly"] + best["relationship"]
    assert np.isclose(s, 1.0)                              # 최적 축가중도 합=1


def test_calibrate_omega_scale_structure() -> None:
    frames = _load_sample("data/sample")
    df, best = cal.calibrate_omega_scale(frames, scales=(0.5, 2.0), step=6)
    assert len(df) == 2 and "omega_scale" in df.columns
    assert best.get("omega_scale") in (0.5, 2.0)
    assert np.isfinite(best.get("lift_bl_vs_market", float("nan")))
