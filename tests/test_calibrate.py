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


def test_calibrate_view_corr_structure() -> None:
    # E3a: 손가중 축그리드(calibrate_axis_weights) 폐기 → 뷰 off-diag 상관 ρ_view 캘리브레이션(E3b 자리).
    frames = _load_sample("data/sample")
    grid = (None, 0.6)                                    # 경험프록시 vs 고정 ρ_view
    df, best = cal.calibrate_view_corr(frames, grid=grid, step=6)
    assert len(df) == 2
    assert {"view_corr", "mean_ic", "lift_bl_vs_market"} <= set(df.columns)
    assert set(df["view_corr"]) == {"empirical", 0.6}     # None→'empirical' 라벨
    assert np.isfinite(best["mean_ic"])
    assert best["view_corr"] in ("empirical", 0.6)


def test_calibrate_omega_scale_structure() -> None:
    frames = _load_sample("data/sample")
    df, best = cal.calibrate_omega_scale(frames, scales=(0.5, 2.0), step=6)
    assert len(df) == 2 and "omega_scale" in df.columns
    assert best.get("omega_scale") in (0.5, 2.0)
    assert np.isfinite(best.get("lift_bl_vs_market", float("nan")))


def test_calibrate_gamma_anom_structure() -> None:
    frames = _load_sample("data/sample")
    df, best = cal.calibrate_gamma_anom(frames, gammas=(0.0, 2.0), step=6)
    assert len(df) == 2 and "gamma_anom" in df.columns
    assert best.get("gamma_anom") in (0.0, 2.0)
    assert np.isfinite(best.get("lift_bl_vs_market", float("nan")))


def test_sweep_lambda_fixed_structure_and_threading() -> None:
    # λ_fix(C3 앵커 스케일)가 정책 손잡이로 노출·스레딩되는지 검증(run_backtest→run_from_frames→assemble).
    frames = _load_sample("data/sample")
    df, best = cal.sweep_lambda_fixed(frames, lambdas=(0.1, 1.0), step=6)
    assert len(df) == 2 and "lambda_fixed" in df.columns
    assert best.get("lambda_fixed") in (0.1, 1.0)
    assert np.isfinite(best.get("lift_bl_vs_market", float("nan")))
    # 손잡이가 실제로 파이프라인까지 닿는지: 두 λ_fix 의 lift 가 달라야 함(무시되면 동일).
    lifts = df.set_index("lambda_fixed")["lift_bl_vs_market"]
    assert lifts.loc[0.1] != lifts.loc[1.0]
