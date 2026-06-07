"""BL 엔진 테스트 — 공분산 수축/PSD, 사후수익(역행렬-free), 제약 최적화.

토이의 치명 결함(대각근사·사후 폭주·방향-액션 불일치)이 재발하지 않음을 검증한다.
"""

from __future__ import annotations

import numpy as np
import pytest

from bl.engine import covariance as cov
from bl.engine import optimize as opt


# ---------------- 공분산 ----------------
def _panel(seed=0, t=120, n=8):
    rng = np.random.default_rng(seed)
    # 공통 팩터 + 개별 잡음 → off-diagonal 상관 존재
    f = rng.standard_normal((t, 1))
    loadings = rng.uniform(0.2, 1.0, size=(1, n))
    return f @ loadings * 0.02 + rng.standard_normal((t, n)) * 0.01


def test_shrunk_covariance_shape_symmetry_psd() -> None:
    s = cov.shrunk_covariance(_panel())
    assert s.shape == (8, 8)
    assert np.allclose(s, s.T)                      # 대칭
    assert np.linalg.eigvalsh(s).min() > 0          # PD (Cholesky 가능)
    np.linalg.cholesky(s)                           # 예외 없어야


def test_covariance_is_full_not_diagonal() -> None:
    # 핵심 교정: off-diagonal 공분산이 0이 아니어야(분산효과 복원)
    s = cov.shrunk_covariance(_panel())
    off = s - np.diag(np.diag(s))
    assert np.abs(off).max() > 0


def test_shrinkage_handles_t_less_than_n() -> None:
    # T<N: 표본공분산 특이 → 수축으로 PD 보장
    s = cov.shrunk_covariance(_panel(t=5, n=20))
    assert s.shape == (20, 20)
    assert np.linalg.eigvalsh(s).min() > 0
    assert cov.condition_number(s) <= cov.COND_MAX * 1.0001


def test_shrinkage_delta_in_unit_interval() -> None:
    _s, delta = cov.ledoit_wolf_shrinkage(_panel())
    assert 0.0 <= delta <= 1.0


# ---------------- 사후 기대수익 ----------------
def _inputs(n=4, tau=0.05):
    rng = np.random.default_rng(1)
    a = rng.standard_normal((n, n)) * 0.01
    sigma = a @ a.T + np.eye(n) * 0.01
    pi = np.full(n, 0.01)
    return sigma, pi, tau


def test_posterior_no_views_returns_prior() -> None:
    # 뷰가 prior와 정확히 같으면 사후=prior(항등 점검)
    sigma, pi, tau = _inputs()
    n = len(pi)
    inp = {"Sigma": sigma, "pi": pi, "P": np.eye(n), "Q": pi.copy(),
           "Omega": np.eye(n) * 1.0, "tau": tau}
    er = opt.posterior_expected_return(inp)
    assert np.allclose(er, pi, atol=1e-10)


def test_posterior_tilts_toward_view() -> None:
    # 자산0에 강한 상승 뷰(낮은 Ω=고신뢰) → 사후수익 자산0이 prior보다 상승
    sigma, pi, tau = _inputs()
    n = len(pi)
    P = np.zeros((1, n)); P[0, 0] = 1.0
    Q = np.array([0.05])                            # prior 0.01 대비 강한 상승
    Omega = np.array([[1e-6]])                      # 고신뢰
    er = opt.posterior_expected_return({"Sigma": sigma, "pi": pi, "P": P, "Q": Q,
                                        "Omega": Omega, "tau": tau})
    assert er[0] > pi[0] + 1e-4


def test_posterior_no_blowup() -> None:
    # 폭주 방지: 합리적 입력에서 |E[R]| 가 입력 스케일의 수배 내
    sigma, pi, tau = _inputs()
    n = len(pi)
    rng = np.random.default_rng(2)
    Q = pi + rng.standard_normal(n) * 0.01
    er = opt.posterior_expected_return({"Sigma": sigma, "pi": pi, "P": np.eye(n), "Q": Q,
                                        "Omega": np.eye(n) * 0.05, "tau": tau})
    assert np.abs(er).max() < 0.2                   # 1.29 류 폭주 없음


def test_posterior_covariance_psd() -> None:
    sigma, pi, tau = _inputs()
    n = len(pi)
    sp = opt.posterior_covariance({"Sigma": sigma, "pi": pi, "P": np.eye(n), "Q": pi,
                                   "Omega": np.eye(n), "tau": tau})
    assert sp.shape == (n, n)
    assert np.linalg.eigvalsh(sp).min() > 0


# ---------------- 최적화 ----------------
def _opt_setup(n=10):
    rng = np.random.default_rng(3)
    a = rng.standard_normal((n, n)) * 0.02
    s = a @ a.T + np.eye(n) * 0.02
    er = np.linspace(0.01, 0.05, n)                 # 자산별 상이 기대수익
    return er, s


def test_optimize_constraints_satisfied() -> None:
    er, s = _opt_setup(10)
    w = opt.optimize_weights(er, s, w_max=0.30, objective="sharpe")
    assert abs(w.sum() - 1.0) < 1e-6                # Σw=1
    assert (w >= -1e-9).all() and (w <= 0.30 + 1e-6).all()  # 0≤w≤w_max
    assert w.max() > 1e-3                           # 퇴화(전부 ~0) 아님


def test_optimize_exclude_forces_zero() -> None:
    er, s = _opt_setup(10)
    w = opt.optimize_weights(er, s, w_max=0.30, exclude=[0, 1])
    assert w[0] == 0.0 and w[1] == 0.0
    assert abs(w.sum() - 1.0) < 1e-6


def test_optimize_min_variance_prefers_low_risk() -> None:
    # 한 자산만 분산 매우 큼 → 최소분산해는 그 자산 비중이 작아야
    n = 5
    s = np.eye(n) * 0.01
    s[0, 0] = 1.0
    er = np.ones(n) * 0.02
    w = opt.optimize_weights(er, s, w_max=1.0, objective="min_variance")
    assert w[0] < 1.0 / n


def test_optimize_infeasible_wmax_raises() -> None:
    er, s = _opt_setup(10)
    with pytest.raises(ValueError):
        opt.optimize_weights(er, s, w_max=0.05)     # 0.05*10=0.5 < 1 → 불가능


def test_optimize_rejects_nonfinite() -> None:
    er, s = _opt_setup(5)
    er2 = er.copy(); er2[0] = np.nan
    with pytest.raises(ValueError):
        opt.optimize_weights(er2, s, w_max=0.5)     # NaN 마스킹 금지(리뷰 #4)
    s2 = s.copy(); s2[0, 0] = np.inf
    with pytest.raises(ValueError):
        opt.optimize_weights(er, s2, w_max=0.5)


def test_optimize_exclude_out_of_range_raises() -> None:
    er, s = _opt_setup(5)
    with pytest.raises(ValueError):
        opt.optimize_weights(er, s, w_max=0.5, exclude=[99])  # 범위 밖(리뷰 #3)


def test_optimize_exclude_negative_index() -> None:
    er, s = _opt_setup(5)
    w = opt.optimize_weights(er, s, w_max=0.5, exclude=[-1])  # 파이썬 음수 인덱스 정규화
    assert w[-1] == 0.0
    assert abs(w.sum() - 1.0) < 1e-6


def test_cap_normalize_respects_cap() -> None:
    # 클립+나눗셈의 상한 재위반을 capped-simplex 사영이 방지(리뷰 #1)
    w = opt._cap_normalize(np.array([0.30, 0.30, 0.30, 0.05, 0.0]), cap=0.30)
    assert w.max() <= 0.30 + 1e-12
    assert abs(w.sum() - 1.0) < 1e-12


def test_posterior_covariance_gate_rejects_degenerate() -> None:
    # 퇴화(전부-0) 사후공분산 입력은 ensure_psd 게이트가 거부
    with pytest.raises(ValueError):
        cov.ensure_psd(np.zeros((4, 4)))
