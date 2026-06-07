"""BL 최적화 — 사후 기대수익(역행렬 회피) + FULL 공분산 제약 최적화.

설계: docs/design/03-bl-model-design.md §6~§8. 과거 토이 결함(대각근사·사후수익 폭주·
방향-액션 불일치) 교정.

사후 기대수익은 §6.1 정칙형과 **대수적으로 동등한 시장균형(Idzorek) 형태**로 계산한다:

    E[R] = Π + τΣ Pᵀ (P τΣ Pᵀ + Ω)⁻¹ (Q − P Π)

이 형태는 N×N 역행렬 없이 **K×K(뷰 개수) 선형해**만 요구하므로 §6.3 '역행렬 회피·선형해'
원칙에 부합하고 수치적으로 안정적이다(폭주 방지). 사후공분산:

    Σ_post = Σ + τΣ − τΣ Pᵀ (P τΣ Pᵀ + Ω)⁻¹ P τΣ

이후 §3.3/§6.2 PSD·조건수 게이트(ensure_psd)를 상속한다. 최적화는 **FULL 공분산** w·Σ·w
로 수행한다(대각근사 폐기 → 분산효과 복원).

미구현(후속 단계, 본 모듈 범위 밖):
- §5.4 Ω 하한(η·(PτΣPᵀ)kk) 적용 → engine/inputs.py(P4 입력 구성, Ω 생성 시점).
- §8 출력 변환(weight_diff→marketing_score(부호보존)→action_guide→funding_gap) → serve/mart.py.
- §9.4 사후수익 범위 빌드-페일 가드 → models/validation.py.
  (앵커 λ는 캘리브레이션 대상이 아니라 Π 스케일 정규화 상수 LAMBDA_FIXED 로 고정됨 — engine/inputs.py, C3.)
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from bl.common.compute import asnumpy, get_array_module
from bl.engine.covariance import ensure_psd

_TOL = 1e-6


def _parse_inputs(inputs: dict, preference: str | None):
    """BL 입력 dict를 검증하고 (xp, Σ, Π, P, Q, Ω, τ, N, K)를 반환한다."""
    xp = get_array_module(preference)

    def _arr(name, allow_2d=False):
        a = xp.asarray(np.asarray(inputs[name], dtype="float64"))
        if not bool(xp.isfinite(a).all()):
            raise ValueError(f"{name} 에 NaN/Inf 가 있습니다.")
        return a

    sigma = _arr("Sigma")
    pi = _arr("pi").reshape(-1)
    q = _arr("Q").reshape(-1)
    tau = float(inputs.get("tau", 0.05))
    if not np.isfinite(tau) or tau <= 0:
        raise ValueError(f"tau 는 양의 유한값이어야 합니다(받음 {tau}).")
    n = pi.shape[0]
    if sigma.shape != (n, n):
        raise ValueError(f"Sigma shape {tuple(sigma.shape)} != ({n},{n})")

    p_in = inputs.get("P")
    if p_in is None:
        p = xp.eye(n, dtype="float64")             # 뷰 미지정 시 절대뷰(P=I)
    else:
        p = _arr("P")
        if p.ndim != 2 or p.shape[1] != n:
            raise ValueError(f"P shape {tuple(p.shape)} 는 (K,{n}) 여야 합니다.")
    k = p.shape[0]
    if q.shape[0] != k:
        raise ValueError(f"Q 길이 {q.shape[0]} != 뷰 개수 K={k}")

    omega = _arr("Omega")
    if omega.ndim == 1:
        omega = xp.diag(omega)
    if omega.shape != (k, k):
        raise ValueError(f"Omega shape {tuple(omega.shape)} != ({k},{k})")
    return xp, sigma, pi, p, q, omega, tau, n, k


def _kxk_solve(xp, p, tau_sigma, omega, rhs):
    """(P τΣ Pᵀ + Ω) x = rhs 를 푼다(K×K, 작은 계). 폭주 방지의 핵심."""
    a = p @ tau_sigma @ p.T + omega
    a = 0.5 * (a + a.T)
    return xp.linalg.solve(a, rhs)


def posterior_expected_return(inputs: dict, preference: str | None = None) -> np.ndarray:
    """BL 사후 기대수익 E[R] (N,) 반환. 역행렬 없이 K×K 선형해(numpy 반환)."""
    xp, sigma, pi, p, q, omega, tau, _n, _k = _parse_inputs(inputs, preference)
    tau_sigma = tau * sigma
    sol = _kxk_solve(xp, p, tau_sigma, omega, q - p @ pi)
    er = pi + tau_sigma @ p.T @ sol
    return asnumpy(er)


def posterior_covariance(inputs: dict, preference: str | None = None) -> np.ndarray:
    """BL 사후 공분산 Σ_post (N×N) 반환(§3.3/§6.2 PSD·조건수 게이트 상속, numpy)."""
    xp, sigma, pi, p, q, omega, tau, _n, _k = _parse_inputs(inputs, preference)
    tau_sigma = tau * sigma
    m = p @ tau_sigma                                   # (K,N)
    sol = _kxk_solve(xp, p, tau_sigma, omega, m)        # (K,N)
    sigma_post = sigma + tau_sigma - m.T @ sol
    return ensure_psd(asnumpy(sigma_post), preference)  # PSD/조건수 게이트 상속(§6.2)


def _cap_normalize(w: np.ndarray, cap: float) -> np.ndarray:
    """w(≥0)를 0≤wᵢ≤cap, Σw=1 로 만드는 capped-simplex 사영(water-filling).

    클립 후 단순 나눗셈이 상한을 재위반하는 문제를 피한다(리뷰 #1).
    """
    w = np.clip(np.asarray(w, dtype="float64"), 0.0, None).copy()
    n = len(w)
    capped = np.zeros(n, dtype=bool)
    for _ in range(n + 1):
        free = ~capped
        if not free.any():
            break
        budget = 1.0 - w[capped].sum()
        ssum = w[free].sum()
        if ssum <= 0:
            break
        scaled = w[free] * (budget / ssum)
        over = scaled > cap + 1e-15
        if not over.any():
            w[free] = scaled
            break
        idx = np.where(free)[0][over]
        w[idx] = cap
        capped[idx] = True
    return w


def optimize_weights(
    er,
    cov,
    *,
    w_max: float = 0.10,
    objective: str = "sharpe",
    risk_aversion: float = 2.5,
    exclude: Sequence[int] | None = None,
    gate_psd: bool = True,
    preference: str | None = None,  # noqa: ARG001 (최적화는 CPU; 시그니처 일관성용)
) -> np.ndarray:
    """제약 하 최적 가중치 w* (N,) 반환. **FULL 공분산** 사용(대각근사 폐기).

    제약: Σw=1, 0≤wᵢ≤w_max, exclude 인덱스는 0 고정.
    objective: 'sharpe'(최대 샤프) | 'min_variance' | 'mean_variance'(λ=risk_aversion).
    이 risk_aversion 은 mean_variance 목적함수 전용 최적화기 파라미터로, inputs.py 의 앵커 λ(Π 스케일
    정규화 상수)와 **무관한 별개 값**이다. 파이프라인 기본 목적함수 'sharpe'(스케일 불변)에서는 쓰이지 않는다.
    gate_psd: True면 cov를 §3.3 ensure_psd 게이트에 통과시킨다(설계 §6.2 정합).

    Raises: ValueError (비유한 입력 / 차원 불일치 / exclude 범위 / 실현 불가능 / 솔버 미수렴/제약 위반).
    """
    from scipy.optimize import minimize

    mu = np.asarray(er, dtype="float64").reshape(-1)
    s = np.asarray(cov, dtype="float64")
    n = mu.shape[0]
    if s.shape != (n, n):
        raise ValueError(f"cov shape {s.shape} != ({n},{n})")
    if not np.isfinite(mu).all():
        raise ValueError("er 에 NaN/Inf 가 있습니다.")
    if not np.isfinite(s).all():
        raise ValueError("cov 에 NaN/Inf 가 있습니다.")
    if gate_psd:
        s = ensure_psd(s)                            # §6.2 PSD/조건수 게이트 상속

    # exclude: 범위 검증 + 정규화(파이썬 음수 인덱스 허용, 중복 제거)
    excl: set[int] = set()
    for raw in exclude or []:
        i = int(raw)
        if not (-n <= i < n):
            raise ValueError(f"exclude 인덱스 {raw} 가 범위[-{n},{n}) 밖입니다.")
        excl.add(i % n)
    avail = n - len(excl)
    if avail <= 0:
        raise ValueError("모든 자산을 제외할 수 없습니다.")
    if w_max * avail < 1.0 - 1e-9:
        raise ValueError(f"w_max={w_max}·가용자산{avail} < 1 → 제약 불가능. w_max 상향 필요.")

    bounds = [(0.0, 0.0) if i in excl else (0.0, w_max) for i in range(n)]

    def neg_sharpe(w):
        ret = float(w @ mu)
        risk = np.sqrt(max(float(w @ s @ w), 1e-18))
        return -ret / risk

    def variance(w):
        return float(w @ s @ w)

    def neg_meanvar(w):
        return -float(w @ mu) + 0.5 * risk_aversion * float(w @ s @ w)

    obj = {"sharpe": neg_sharpe, "min_variance": variance, "mean_variance": neg_meanvar}.get(
        objective
    )
    if obj is None:
        raise ValueError(f"objective 는 sharpe|min_variance|mean_variance (받음: {objective!r})")

    x0 = np.array([0.0 if i in excl else 1.0 / avail for i in range(n)])
    x0 = _cap_normalize(x0, w_max)

    res = minimize(
        obj, x0, method="SLSQP", bounds=bounds,
        constraints=[{"type": "eq", "fun": lambda w: float(w.sum() - 1.0)}],
        options={"maxiter": 500, "ftol": 1e-9},
    )
    if not res.success:
        raise ValueError(f"SLSQP 미수렴: {res.message}")

    w = np.clip(res.x, 0.0, None)
    for i in excl:
        w[i] = 0.0
    w = _cap_normalize(w, w_max)                      # 상한 보존하며 Σw=1 사영(단순 나눗셈 금지)

    if abs(w.sum() - 1.0) > _TOL or w.max() > w_max + _TOL:
        raise ValueError(
            f"제약 위반(해 검증 실패): Σw={w.sum():.6f}, max={w.max():.6f} > w_max={w_max}"
        )
    return w
