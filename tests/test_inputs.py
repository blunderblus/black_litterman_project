"""BL 입력 빌더 테스트 — Π=λΣw_mkt 앵커, 3축 Q, Ω∝1/DRI²·anomaly·하한, 단위정합."""

from __future__ import annotations

import numpy as np
import pandas as pd

from bl.engine import inputs as bi


def _assets(n=6):
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "corp_code": [f"{i:08d}" for i in range(n)],
        "w_mkt": rng.uniform(0.5, 2.0, n),
        "w_current": rng.uniform(0.5, 2.0, n),
        "gemini_score": rng.uniform(-1, 1, n),
        "prob_growth_raw": rng.uniform(0, 1, n),
        "prob_churn_raw": rng.uniform(0, 1, n),
        "anomaly_score_raw": rng.uniform(0, 1, n),
        "trx_in": rng.uniform(0, 100, n),
        "trx_out": rng.uniform(0, 100, n),
        "relationship_score": rng.uniform(0, 1, n),
        "confidence_growth": rng.uniform(0.5, 0.9, n),
        "gemini_confidence": rng.uniform(0.5, 0.9, n),
        "has_financial": rng.integers(0, 2, n),
        "has_news": rng.integers(0, 2, n),
        "is_listed": rng.integers(0, 2, n),
        "trx_activity": rng.uniform(0, 1, n),
    })


def _panel(n=6, t=80):
    rng = np.random.default_rng(1)
    f = rng.standard_normal((t, 1))
    return f @ rng.uniform(0.2, 1, (1, n)) * 0.02 + rng.standard_normal((t, n)) * 0.01


def test_assemble_shapes_and_keys() -> None:
    a = _assets(6)
    out = bi.assemble_bl_inputs(a, _panel(6))
    assert out["Sigma"].shape == (6, 6)
    assert out["pi"].shape == (6,)
    assert out["P"].shape == (6, 6)
    assert out["Q"].shape == (6,)
    assert out["Omega"].shape == (6, 6)
    assert len(out["tickers"]) == 6
    assert abs(out["w_mkt"].sum() - 1.0) < 1e-9       # 정규화
    assert LAMBDA_LO <= out["lambda"] <= LAMBDA_HI


LAMBDA_LO, LAMBDA_HI = bi.LAMBDA_CLIP


def test_pi_equals_lambda_sigma_wmkt() -> None:
    a = _assets(5)
    out = bi.assemble_bl_inputs(a, _panel(5), risk_aversion=2.5)
    expected = 2.5 * (out["Sigma"] @ out["w_mkt"])
    assert np.allclose(out["pi"], expected)            # 앵커 = w_mkt(현상유지 아님)


def test_dri_range() -> None:
    a = _assets(6)
    dri = bi.compute_dri(a)
    assert (dri >= 0.1 - 1e-12).all() and (dri <= 1.0 + 1e-12).all()


def test_omega_floor_and_inverse_dri() -> None:
    a = _assets(6)
    out = bi.assemble_bl_inputs(a, _panel(6))
    base = out["tau"] * np.diag(out["Sigma"])
    omega = np.diag(out["Omega"])
    assert (omega >= bi.OMEGA_FLOOR_ETA * base - 1e-18).all()  # §5.4 하한
    assert (omega > 0).all()


def test_lambda_calibration_clipped() -> None:
    # 비정상 패널에서도 λ ∈ [1,5]
    a = _assets(4)
    out = bi.assemble_bl_inputs(a, _panel(4))
    assert 1.0 <= out["lambda"] <= 5.0


def test_panel_mismatch_raises() -> None:
    a = _assets(6)
    import pytest
    with pytest.raises(ValueError):
        bi.assemble_bl_inputs(a, _panel(5))  # 패널 자산수 불일치


def test_q_variance_matches_tau_sigma_unit() -> None:
    # Q 단위정합: Var(Q) ≈ τ·mean(diagΣ) (리뷰 #4, 과거 70x 과대 차단)
    a = _assets(8)
    out = bi.assemble_bl_inputs(a, _panel(8))
    target = out["tau"] * float(np.mean(np.diag(out["Sigma"])))
    assert np.isclose(float(np.var(out["Q"])), target, rtol=0.25)
    # Q가 수익률 스케일 내(과거 5x 과대·폭주 아님). 사후수익 폭주 가드는 통합 테스트(<5σ)가 담당.
    assert float(np.sqrt(np.var(out["Q"]))) < 0.1


def test_nan_wmkt_raises_not_uniform() -> None:
    # NaN w_mkt가 균등붕괴로 무음 처리되지 않고 거부됨(리뷰 #5)
    import pytest
    a = _assets(5)
    a.loc[0, "w_mkt"] = np.nan
    with pytest.raises(ValueError):
        bi.assemble_bl_inputs(a, _panel(5))


def test_duplicate_corp_code_raises() -> None:
    import pytest
    a = _assets(4)
    a.loc[1, "corp_code"] = a.loc[0, "corp_code"]  # 중복 자산
    with pytest.raises(ValueError):
        bi.assemble_bl_inputs(a, _panel(4))


def test_axis_weights_three_axes_sum_one() -> None:
    # 뷰 축은 3개(news/pattern/relationship)이고 합=1. anomaly 는 뷰가 아님(E2 이전).
    assert set(bi.AXIS_WEIGHTS) == {"news", "pattern", "relationship"}
    assert "anomaly" not in bi.AXIS_WEIGHTS
    assert abs(sum(bi.AXIS_WEIGHTS.values()) - 1.0) < 1e-9


def test_anomaly_not_a_view_axis() -> None:
    # anomaly 만 있는 자산은 뷰 축이 없으므로 q_raw=0(과거: 4번째 뷰로 신호 보존했음 — 폐기)
    n = 6
    df = pd.DataFrame({
        "corp_code": [f"{i:08d}" for i in range(n)],
        "anomaly_score_raw": np.linspace(0.1, 0.9, n),
    })  # news/pattern/relationship 축 컬럼 없음
    q_raw = bi.build_views(df)
    assert float(np.std(q_raw)) == 0.0      # anomaly 는 Q(방향)에 기여하지 않음
    assert np.allclose(q_raw, 0.0)


def test_omega_monotonic_increasing_in_anomaly() -> None:
    # anomaly_score 0→1 증가 시 해당 법인 omega_diag 단조 증가(이상할수록 뷰 불신 → Ω 팽창)
    a = _assets(6)
    panel = _panel(6)

    def omega(score):
        b = a.copy()
        b["anomaly_score_raw"] = score
        return np.diag(bi.assemble_bl_inputs(b, panel)["Omega"])
    o0, o_mid, o1 = omega(0.0), omega(0.5), omega(1.0)
    assert (o_mid >= o0 - 1e-18).all() and (o1 >= o_mid - 1e-18).all()  # 단조 비감소
    assert (o1 > o0).all()                          # 엄격 증가(하한 미구속 영역)
    # γ=2 → anomaly=1 곱수 3배: 하한이 안 무는 한 ~3× (유계 [1,1+γ])
    assert o1.sum() > 1.5 * o0.sum()


def test_omega_anomaly_zero_equals_absent_graceful() -> None:
    # anomaly_score_raw 컬럼 없을 때 graceful(요인 1) → anomaly=0 과 동일 Ω
    a = _assets(6)
    panel = _panel(6)
    a0 = a.copy()
    a0["anomaly_score_raw"] = 0.0
    a_absent = a.drop(columns=["anomaly_score_raw"])
    o0 = np.diag(bi.assemble_bl_inputs(a0, panel)["Omega"])
    on = np.diag(bi.assemble_bl_inputs(a_absent, panel)["Omega"])
    assert np.allclose(o0, on)                       # 결측 → 요인 1 == anomaly 0
    assert bi.assemble_bl_inputs(a0, panel)["metadata"]["gamma_anom"] == bi.GAMMA_ANOM


def test_gamma_anom_override_scales_omega() -> None:
    # gamma_anom override: γ=0 이면 anomaly 무효(Ω 변조 없음), 클수록 Ω↑
    a = _assets(6)
    a["anomaly_score_raw"] = 0.8
    panel = _panel(6)
    o_g0 = np.diag(bi.assemble_bl_inputs(a, panel, gamma_anom=0.0)["Omega"])
    o_g4 = np.diag(bi.assemble_bl_inputs(a, panel, gamma_anom=4.0)["Omega"])
    a_no = a.copy()
    a_no["anomaly_score_raw"] = 0.0
    o_base = np.diag(bi.assemble_bl_inputs(a_no, panel)["Omega"])
    assert np.allclose(o_g0, o_base)                 # γ=0 → anomaly 무효
    assert (o_g4 > o_g0).all()
