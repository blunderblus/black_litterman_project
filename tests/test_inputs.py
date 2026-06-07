"""BL 입력 빌더 테스트 — Π 앵커(Q 스케일 정규화·λ 고정상수), 4축 Q, Ω∝1/DRI²·하한, 단위정합."""

from __future__ import annotations

import math

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
    assert out["lambda"] == bi.LAMBDA_FIXED            # λ는 고정 정규화 상수(캘리브레이션 아님, C3)


def test_pi_proportional_to_sigma_wmkt_and_normalized() -> None:
    # C3: Π는 여전히 Σw_mkt 에 비례(시장균형 shape 보존), 스케일만 Q(τ_ref)에 정규화.
    a = _assets(5)
    out = bi.assemble_bl_inputs(a, _panel(5), risk_aversion=0.25)
    anchor = out["Sigma"] @ out["w_mkt"]
    ratio = out["pi"] / anchor
    assert np.allclose(ratio, ratio[0])                # Π ∝ Σw_mkt (단일 스칼라 배수 = shape 보존)
    # 정규화: ‖Π‖ = λ·√(τ_ref·meanΣ)·√N  (= λ·‖Q(τ_ref)‖, 앵커를 뷰 스케일에 맞춤)
    mean_var = float(np.mean(np.diag(out["Sigma"])))
    expected_norm = 0.25 * math.sqrt(bi.TAU_REF * mean_var) * math.sqrt(len(out["w_mkt"]))
    assert np.isclose(float(np.linalg.norm(out["pi"])), expected_norm, rtol=1e-9)
    assert out["metadata"]["lambda_effective"] > 0     # Σw_mkt 실효 배수 기록


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


def test_lambda_is_fixed_constant_not_calibrated() -> None:
    # C3: λ는 데이터에서 역산하지 않고 LAMBDA_FIXED 로 고정 — 패널이 달라도 동일.
    out_a = bi.assemble_bl_inputs(_assets(4), _panel(4))
    out_b = bi.assemble_bl_inputs(_assets(4), _panel(4, t=140))
    assert out_a["lambda"] == bi.LAMBDA_FIXED
    assert out_b["lambda"] == bi.LAMBDA_FIXED


def test_risk_aversion_overrides_lambda() -> None:
    # risk_aversion 은 λ override(테스트용). 지정 시 그 값이 λ가 되고 Π 스케일에 선형 반영.
    out1 = bi.assemble_bl_inputs(_assets(5), _panel(5), risk_aversion=0.4)
    out2 = bi.assemble_bl_inputs(_assets(5), _panel(5), risk_aversion=0.2)
    assert out1["lambda"] == 0.4 and out2["lambda"] == 0.2
    # 동일 데이터(고정 시드) → ‖Π‖ 비율 = λ 비율(2배)
    assert np.isclose(np.linalg.norm(out1["pi"]) / np.linalg.norm(out2["pi"]), 2.0, rtol=1e-9)


def test_tau_is_sole_anchor_view_knob() -> None:
    # C3 핵심: τ↑ → 앵커 사후기여↓(뷰 지배=공격적), τ↓ → 앵커기여↑(보수적). 단조여야 τ가
    # 유일한 앵커↔뷰 손잡이로 작동(λ 이중손잡이 제거). 분해는 설계 §9.2 precision-form.
    a, panel = _assets(8), _panel(8)
    contribs = []
    for tau in (0.025, 0.05, 0.1):
        out = bi.assemble_bl_inputs(a, panel, tau=tau)
        tsinv = np.linalg.inv(tau * out["Sigma"])
        oinv = np.linalg.inv(out["Omega"])                 # P=I
        m = np.linalg.inv(tsinv + oinv)
        anchor_term = m @ (tsinv @ out["pi"])
        view_term = m @ (oinv @ out["Q"])
        na, nv = np.linalg.norm(anchor_term), np.linalg.norm(view_term)
        contribs.append(na / (na + nv))
    assert contribs[0] > contribs[1] > contribs[2]         # 보수(0.025) > 균형(0.05) > 공격(0.1)
    assert all(0.05 < c < 0.7 for c in contribs)           # 앵커가 증발(~0)도 지배(~1)도 아님(C3)


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


def test_anomaly_unsigned_without_flow() -> None:
    # 거래흐름 컬럼 부재 시 anomaly 신호가 sign(0)=0 으로 죽지 않음(리뷰 #9)
    n = 6
    df = pd.DataFrame({
        "corp_code": [f"{i:08d}" for i in range(n)],
        "anomaly_score_raw": np.linspace(0.1, 0.9, n),  # 변동 있는 anomaly
    })  # trx_in/out 없음, 다른 축 없음
    q_raw = bi.build_views(df)
    assert float(np.std(q_raw)) > 0  # anomaly 신호가 보존됨
