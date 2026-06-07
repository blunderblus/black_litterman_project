"""BL 입력 빌더 테스트 — Π 앵커(Q 스케일 정규화·λ 고정상수), 뷰 레지스트리 블록스택 P/Q/Ω(off-diag),
Ω∝1/DRI²·anomaly·하한, 단위정합, 동작동등(결합 단일뷰 등가), 플러그인성(직교뷰 K=3)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from bl.engine import inputs as bi
from bl.engine import optimize as opt


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
    # E3a 블록스택: K=2 뷰(news,pattern) → P=(KN,N), Q=(KN,), Ω=(KN,KN). 결합 등가 q_eff/omega_eff=(N,).
    a = _assets(6)
    out = bi.assemble_bl_inputs(a, _panel(6))
    k = len(bi.VIEW_REGISTRY)
    assert k == 2
    assert out["Sigma"].shape == (6, 6)
    assert out["pi"].shape == (6,)
    assert out["P"].shape == (k * 6, 6)               # 블록스택 KN×N
    assert out["Q"].shape == (k * 6,)
    assert out["Omega"].shape == (k * 6, k * 6)
    assert out["q_eff"].shape == (6,) and out["omega_eff"].shape == (6,)  # 결합 단일뷰 등가(법인당)
    assert out["view_names"] == ["news", "pattern"]
    assert len(out["tickers"]) == 6
    assert abs(out["w_mkt"].sum() - 1.0) < 1e-9       # 정규화
    assert out["lambda"] == bi.LAMBDA_FIXED            # λ는 고정 정규화 상수(캘리브레이션 아님, C3)


def test_P_is_block_stack_of_identity() -> None:
    # P = [I;I;…] (KN×N): 각 뷰 블록이 절대뷰 항등(법인 i 의 그 뷰값)
    out = bi.assemble_bl_inputs(_assets(5), _panel(5))
    k, n = len(bi.VIEW_REGISTRY), 5
    p = out["P"]
    assert p.shape == (k * n, n)
    for a in range(k):
        assert np.allclose(p[a * n:(a + 1) * n, :], np.eye(n))


def test_offdiag_at_equal_omega_is_average_direction_invariant() -> None:
    # off-diag 효과의 *특수케이스 불변*(엄밀): per-view ω가 같을 때(ω_news==ω_pattern)만 결합 뷰값
    # q_eff = per-view 평균이라 off-diag(view_corr)와 무관하고, off-diag 는 결합 정밀도(omega_eff)만 조절.
    # (G_i=ω[[1,ρ],[ρ,1]] → q_eff=(q1+q2)/2, omega_eff=ω(1+ρ)/2.) ※일반(ω 상이) 경로는 아래 별도 테스트.
    a = _assets(8)
    a["gemini_confidence"] = 0.7
    a["confidence_growth"] = 0.7                      # 두 뷰 conf 동일 → ω_news==ω_pattern (특수케이스 강제)
    panel = _panel(8)
    q0 = bi.assemble_bl_inputs(a, panel, view_corr=0.0)
    q6 = bi.assemble_bl_inputs(a, panel, view_corr=0.6)
    assert np.allclose(q0["q_eff"], q6["q_eff"], atol=1e-9)      # 등-ω: 방향 불변
    assert not np.allclose(q0["omega_eff"], q6["omega_eff"])     # off-diag 는 정밀도(보수성)만 변경
    qblock = q0["Q"].reshape(2, 8)                                # view-major [news; pattern]
    assert np.allclose(q0["q_eff"], qblock.mean(axis=0), atol=1e-9)   # 결합 = per-view 평균(등 Ω)
    assert (q6["omega_eff"] > q0["omega_eff"]).all()             # off-diag↑ → 결합 정밀도↓(보수↑)


def test_offdiag_shifts_direction_at_unequal_omega() -> None:
    # 정직성(적대적 리뷰 반영): 운영 경로는 뷰별 conf가 *상이*(news=gemini_confidence, pattern=
    # confidence_growth, E4)하여 ω_news≠ω_pattern 이 일반적이다. 이때 q_eff 는 정밀도가중 혼합이라
    # off-diag(view_corr)가 *방향(q_eff)도* 바꾼다 — 즉 '동작 동등(랭킹 보존)'은 무조건 불변량이 아니라
    # 기본 경로(경험 프록시 ρ가 작음)에서 성립하는 *경험적* 속성이다(REPORT §3/§4 측정).
    a = _assets(8)
    a["gemini_confidence"] = np.linspace(0.5, 0.9, 8)    # 뷰별 conf 상이 → ω_news≠ω_pattern
    a["confidence_growth"] = np.linspace(0.9, 0.5, 8)
    panel = _panel(8)
    q0 = bi.assemble_bl_inputs(a, panel, view_corr=0.0)
    q9 = bi.assemble_bl_inputs(a, panel, view_corr=0.9)
    assert not np.allclose(q0["q_eff"], q9["q_eff"], atol=1e-6)  # ω 상이 → off-diag 가 방향도 조절


def test_combined_stats_reproduce_blockstack_posterior() -> None:
    # ★동작 동등의 수학적 backbone: (q_eff,omega_eff)를 가진 단일뷰(P=I)의 BL 사후가 블록스택과 *정확히* 일치.
    # 이것이 결합 로깅(q/omega)의 정당성이자, 비계가 (어떤) 단일뷰로 환원 가능(로깅 환원)함의 증명.
    # ★비-자명 off-diag(view_corr>0)에서도 환원이 정확함을 검증(off-diag 결합로직 G⁻¹ 배치역행렬 가드).
    a, panel = _assets(8), _panel(8)
    n = len(a)
    for vc in (None, 0.6, 0.9):
        out = bi.assemble_bl_inputs(a, panel, view_corr=vc)
        er_block = opt.posterior_expected_return(out)
        single = {"Sigma": out["Sigma"], "pi": out["pi"], "P": np.eye(n),
                  "Q": out["q_eff"], "Omega": np.diag(out["omega_eff"]), "tau": out["tau"]}
        er_single = opt.posterior_expected_return(single)
        assert np.allclose(er_block, er_single, atol=1e-10)


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
    k = len(bi.VIEW_REGISTRY)
    base = out["tau"] * np.diag(out["Sigma"])
    base_tiled = np.tile(base, k)                     # per-view 블록 각각 하한 적용(KN,)
    omega = np.diag(out["Omega"])
    assert (omega >= bi.OMEGA_FLOOR_ETA * base_tiled - 1e-18).all()  # §5.4 하한(뷰별)
    assert (omega > 0).all()


def test_omega_psd_and_offdiagonal_present() -> None:
    # Ω 는 대칭 PSD(off-diag 뷰상관 포함)이고, 번영뷰 상관이 0이 아니면 off-diag 블록이 비어있지 않음.
    a = _assets(8)
    out = bi.assemble_bl_inputs(a, _panel(8))
    om = out["Omega"]
    assert np.allclose(om, om.T)
    assert np.linalg.eigvalsh(om).min() > 0          # 강한 양정치(특이 Ω 회피)
    # view_corr override 가 1 근처여도 VIEW_CORR_MAX 로 클립되어 PSD 유지(정지조건 PSD 게이트)
    out2 = bi.assemble_bl_inputs(a, _panel(8), view_corr=0.999)
    assert np.linalg.eigvalsh(out2["Omega"]).min() > 0
    rho_eff = np.array(out2["metadata"]["view_corr"])[0, 1]
    assert abs(rho_eff) <= bi.VIEW_CORR_MAX + 1e-9    # |ρ|<1 보장


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
        oinv = np.linalg.inv(out["Omega"])                 # 블록스택 일반형(P 임의)
        p = out["P"]
        m = np.linalg.inv(tsinv + p.T @ oinv @ p)
        anchor_term = m @ (tsinv @ out["pi"])
        view_term = m @ (p.T @ oinv @ out["Q"])
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
    k = len(bi.VIEW_REGISTRY)
    target = out["tau"] * float(np.mean(np.diag(out["Sigma"])))
    assert np.isclose(float(np.var(out["Q"])), target, rtol=0.25)
    # ★블록별 개별 단위정합(설계 §5.2): 각 뷰 블록 Var(Q_v)≈target. 전체 var만 보면 한 블록이 잘못
    # 스케일돼도 우연히 상쇄될 수 있으므로 블록별로 단언한다(적대적 리뷰 반영).
    q_block = out["Q"].reshape(k, len(a))                 # view-major [news; pattern]
    for v in range(k):
        assert np.isclose(float(np.var(q_block[v])), target, rtol=0.25)
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


def test_view_registry_is_news_pattern_only() -> None:
    # 뷰 레지스트리(canonical) = [news, pattern]. relationship 은 Σ 이동성 슬롯 예약(뷰 아님),
    # anomaly 는 Ω 신뢰도 요인(E2)이므로 뷰가 아니다. AXIS_WEIGHTS(손가중)는 폐기됨.
    names = [v.name for v in bi.VIEW_REGISTRY]
    assert names == ["news", "pattern"]
    assert "relationship" not in names and "anomaly" not in names
    assert not hasattr(bi, "AXIS_WEIGHTS")              # 손가중 완전 제거(BL Ω가 융합)
    assert bi.RELATIONSHIP_RESERVED == "relationship_score"  # Σ 예약 슬롯 문서화


def test_anomaly_not_a_view_signal() -> None:
    # anomaly 만 있는 자산은 뷰 신호가 없으므로 Z=0(과거: 4번째 뷰로 신호 보존했음 — 폐기)
    n = 6
    df = pd.DataFrame({
        "corp_code": [f"{i:08d}" for i in range(n)],
        "anomaly_score_raw": np.linspace(0.1, 0.9, n),
    })  # news/pattern 신호 컬럼 없음
    names, z = bi.build_view_signals(df)
    assert names == ["news", "pattern"]
    assert float(np.std(z)) == 0.0          # anomaly 는 Q(방향)에 기여하지 않음
    assert np.allclose(z, 0.0)


def test_plugin_view_increases_k_and_contributes() -> None:
    # ★플러그인성: 레지스트리에 뷰를 한 줄 추가하면 가중 재설계 없이 K=3 으로 동작하고, 그 뷰 신호가
    # 실제로 사후에 반영된다(no-op 아님). (이름에서 '직교' 제거 — 데모상 더미 신호는 기존 뷰와 상관이
    # 0이 아닐 수 있고, 직교성 자체는 본 테스트의 주장이 아니다.)
    a, panel = _assets(7), _panel(7)
    a = a.copy()
    a["mobility_signal"] = np.array([0.9, -0.8, 0.2, -0.5, 0.7, -0.1, 0.4])  # 가상 이동성 신호
    dummy = bi.ViewSpec(name="mobility",
                        signal=lambda df: df["mobility_signal"].to_numpy("float64"),
                        conf_col="gemini_confidence")
    reg3 = [*bi.VIEW_REGISTRY, dummy]
    n = 7
    out2 = bi.assemble_bl_inputs(a, panel)                          # K=2 (기본 레지스트리)
    out3 = bi.assemble_bl_inputs(a, panel, registry=reg3)           # K=3 (뷰 한 줄 추가)
    assert out3["P"].shape == (3 * n, n)                 # K=3 블록스택(가중 재설계 없이 K 증가)
    assert out3["Q"].shape == (3 * n,) and out3["Omega"].shape == (3 * n, 3 * n)
    assert out3["view_names"] == ["news", "pattern", "mobility"]
    er2, er3 = opt.posterior_expected_return(out2), opt.posterior_expected_return(out3)
    assert er3.shape == (n,) and np.isfinite(er3).all()  # 파이프라인 동작
    assert not np.allclose(er2, er3)                     # 추가 뷰 신호가 실제로 사후에 기여(no-op 아님)
    assert np.linalg.eigvalsh(out3["Omega"]).min() > 0  # K=3 off-diag 도 PSD


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
