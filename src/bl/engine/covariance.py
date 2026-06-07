"""공분산 Σ — 잔액 log-return 패널의 FULL 공분산 + Ledoit-Wolf 수축.

설계: docs/design/03-bl-model-design.md §3. 과거 토이의 치명 결함(대각만 사용 → 분산효과
소실, reg=1e-6 하드바닥 → 사후수익 폭주)을 교정한다. 본 모듈은:
- 표본 공분산을 FULL 행렬로 계산(대각 사용 0).
- Ledoit-Wolf(2004) 단일파라미터(scaled identity 타깃) 수축으로 T<N에서도 안정화.
- 대칭화 → 고유값 바닥(λ_floor=1e-8·trΣ/N) → 조건수 상한(κ≤1e6) → Cholesky 검증.
- NumPy/SciPy ↔ CuPy 동일 코드(xp 디스패치). 모든 산출은 numpy로 반환.

참고: Ledoit & Wolf, "Honey, I Shrunk the Sample Covariance Matrix" (2004).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from bl.common.compute import asnumpy, get_array_module
from bl.common.logging import get_logger

if TYPE_CHECKING:
    import pandas as pd  # noqa: F401

log = get_logger(__name__)

EIG_FLOOR_REL = 1e-8        # 고유값 바닥 = EIG_FLOOR_REL * tr(Σ)/N
COND_MAX = 1e6             # 조건수 상한(초과 시 하한 상향)
_DEGENERATE_TR = 1e-18     # trace 가 이 값 이하이면 퇴화 공분산으로 간주


def _as_panel(returns) -> np.ndarray:
    """returns(T×N: 행=시점, 열=자산)을 2D float64 ndarray로 변환."""
    if hasattr(returns, "to_numpy"):  # pandas DataFrame/Series
        arr = returns.to_numpy(dtype="float64")
    else:
        arr = np.asarray(returns, dtype="float64")
    if arr.ndim != 2:
        raise ValueError(f"returns 는 2D(T×N)이어야 합니다. 받은 shape={arr.shape}")
    if arr.shape[0] < 2:
        raise ValueError("공분산 추정에는 최소 2개 시점(T≥2)이 필요합니다.")
    if not np.isfinite(arr).all():
        raise ValueError("returns 에 NaN/Inf 가 있습니다(시점정합·결측처리 후 입력).")
    return arr


def ledoit_wolf_shrinkage(returns, preference: str | None = None) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf 수축 공분산과 수축강도 δ를 반환한다(PSD 보정 전 단계).

    타깃 F = μ·I (μ = 평균분산 = tr(S)/N). δ는 LW2004 closed-form. S는 LW 관례상 1/T.
    """
    xp = get_array_module(preference)
    X = xp.asarray(_as_panel(returns))
    t, n = X.shape
    Xc = X - X.mean(axis=0, keepdims=True)         # 시점 평균 제거
    s = (Xc.T @ Xc) / t                             # 표본 공분산(LW 관례: 1/T)

    mu = xp.trace(s) / n                             # 평균분산(scaled identity 타깃)
    f = mu * xp.eye(n, dtype=s.dtype)
    d2 = float(asnumpy(xp.sum((s - f) ** 2)))       # ||S - F||_F^2

    # b2 = (1/T^2) Σ_t ||x_t x_t^T - S||_F^2,  ||x_t x_t^T - S||_F^2
    #    = (x_t·x_t)^2 - 2 x_t^T S x_t + ||S||_F^2
    xnorm2 = xp.sum(Xc * Xc, axis=1)                # (T,) = x_t·x_t
    quad = xp.einsum("ti,ij,tj->t", Xc, s, Xc)      # (T,) = x_t^T S x_t
    s_fro2 = xp.sum(s * s)
    phi = xnorm2 ** 2 - 2.0 * quad + s_fro2
    b2 = float(asnumpy(xp.sum(phi))) / (t ** 2)
    b2 = min(b2, d2)                                 # b2 ≤ d2

    delta = (b2 / d2) if d2 > 0 else 1.0            # d2≈0(S=μI)면 결과 동일, δ=1로 안전
    delta = min(max(delta, 0.0), 1.0)
    sigma = delta * f + (1.0 - delta) * s
    if t <= n:
        log.warning(
            f"표본 시점 T={t} ≤ 자산수 N={n}: 표본공분산 rank-deficient — 수축(δ={delta:.3f})"
            "+PSD 게이트로 안정화",
            extra={"stage": "engine.ledoit_wolf", "t": t, "n": n, "delta": delta},
        )
    return asnumpy(sigma), delta


def ensure_psd(sigma: np.ndarray, preference: str | None = None) -> np.ndarray:
    """대칭화 → 고유값 바닥/조건수 상한 → Cholesky 성공 보장. 퇴화 입력은 거부.

    과거의 reg=1e-6 하드바닥을 폐기하고 데이터 스케일 기반 고유값 바닥을 적용한다.
    Raises: ValueError (NaN/Inf, 또는 trace≈0 인 퇴화/음정치 공분산).
    """
    xp = get_array_module(preference)
    s = xp.asarray(sigma, dtype="float64")
    if not bool(xp.isfinite(s).all()):
        raise ValueError("공분산에 NaN/Inf 가 있습니다.")
    n = s.shape[0]
    s = 0.5 * (s + s.T)                              # 대칭화
    tr = float(asnumpy(xp.trace(s)))
    if tr <= _DEGENERATE_TR:
        raise ValueError(f"퇴화 공분산: trace={tr:.3e} ≈ 0 (전부-0/음정치 입력) — 입력 점검 필요.")

    w, v = xp.linalg.eigh(s)
    wmax = float(asnumpy(w.max()))
    wmin = float(asnumpy(w.min()))
    if wmin < -1e-8 * max(abs(wmax), 1e-300):
        log.warning(
            f"공분산에 유의미한 음의 고유값(λ_min={wmin:.3e}) → 바닥 클립",
            extra={"stage": "engine.ensure_psd", "lambda_min": wmin},
        )
    lo = max(EIG_FLOOR_REL * tr / max(n, 1), wmax / COND_MAX)  # 고유값 바닥 ∧ 조건수 상한
    w = xp.clip(w, lo, None)
    s = (v * w) @ v.T
    s = 0.5 * (s + s.T)
    try:
        xp.linalg.cholesky(s)
    except Exception:
        s = s + lo * xp.eye(n, dtype=s.dtype)
    return asnumpy(s)


def shrunk_covariance(returns, preference: str | None = None) -> np.ndarray:
    """returns(T×N) → 수축 FULL 공분산 Σ(N×N, PSD·조건수 보장). numpy 반환.

    CPU(NumPy)·GPU(CuPy) 동일 알고리즘·동일 수치(상대오차 < 1e-8).
    """
    sigma, _delta = ledoit_wolf_shrinkage(returns, preference)
    return ensure_psd(sigma, preference)


def condition_number(sigma: np.ndarray) -> float:
    """대칭 PSD 행렬의 조건수(λ_max/λ_min)."""
    w = np.linalg.eigvalsh(np.asarray(sigma, dtype="float64"))
    wmin = float(w.min())
    return float("inf") if wmin <= 0 else float(w.max() / wmin)
