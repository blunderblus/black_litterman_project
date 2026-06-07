"""BL 입력 빌더 — Σ · Π(=λΣw_mkt) · P · Q(3축) · Ω(∝1/DRI²·anomaly) · τ 구성.

설계: docs/design/03-bl-model-design.md §4~§5. 과거 노트북 09 대응.
과거 토이 결함 교정: 앵커를 w_mkt로(현상유지 w_hybrid 아님), Q·Ω·τΣ **단위 정합**(분산정합),
Ω∝1/DRI²(+§5.4 하한·M_DRI 캡) 보존, confidence 기반 가중(c_cal 데이터기반).

핵심은 **순수 함수 assemble_bl_inputs(DataFrame+패널)** 이다. DB/ingest 경로도 pipeline.run()
(load_frames→프레임)을 거쳐 동일 함수로 수렴하므로 별도 DuckDB 결합 래퍼를 두지 않는다(단일 경로).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from bl.common.logging import get_logger
from bl.engine.covariance import shrunk_covariance

if TYPE_CHECKING:
    import pandas as pd

log = get_logger(__name__)

# 뷰 3축 가중(합=1). anomaly 는 방향 뷰가 아니라 Ω 신뢰도 변조 요인으로 이전(E2 해소):
# 과거 4축 (news,pattern,anomaly,relationship)=(0.35,0.35,0.15,0.15)에서 anomaly 를 빼고 남은
# 3축을 비율 보존 재정규화 → (0.35,0.35,0.15)/0.85 ≈ (0.412,0.412,0.176).
AXIS_WEIGHTS = {"news": 0.412, "pattern": 0.412, "relationship": 0.176}
DRI_WEIGHTS = {"has_financial": 0.3, "has_news": 0.25, "is_listed": 0.15, "trx_activity": 0.2}
DRI_BASE = 0.1
DEFAULT_TAU = 0.05
OMEGA_FLOOR_ETA = 0.05      # Ω 하한 = η·(PτΣPᵀ)kk (§5.4, 과신 폭주 방지)
M_DRI = 100.0             # 1/DRI² 증폭 상한(§5.4)
# anomaly_score(이상 크기, in-distribution 여부)에 의한 Ω 팽창 게인. DRI·conf 와 같은 신뢰도 요인.
# 추정 대상이 아닌 기본값이며, 추후 eval.calibrate(calibrate_gamma_anom)에서 실현 적중률로 캘리브레이션한다.
GAMMA_ANOM = 2.0
LAMBDA_CLIP = (1.0, 5.0)   # λ 클립 범위(§4.2)
Q_CLIP_SIGMA = 3.0        # |Q| ≤ Q_CLIP_SIGMA·σ_asset 클립(§5.2)


def compute_dri(assets: pd.DataFrame) -> np.ndarray:
    """데이터신뢰도지수 DRI ∈ [0.1, 1.0] (설계 §5.4). 결측 구성요소는 0 취급."""
    n = len(assets)
    dri = np.full(n, DRI_BASE)
    for col, w in DRI_WEIGHTS.items():
        if col in assets.columns:
            dri = dri + w * assets[col].fillna(0).to_numpy(dtype="float64")
    return np.clip(dri, DRI_BASE, 1.0)


def _zscore(x: np.ndarray) -> np.ndarray:
    """배치 z-score. 표준편차 0이면 0 벡터(추론 시 고정 스케일러 권장, ADR-0004)."""
    mu, sd = float(np.mean(x)), float(np.std(x))
    return (x - mu) / sd if sd > 1e-12 else np.zeros_like(x)


def calibrate_lambda(returns_panel, w_mkt: np.ndarray, sigma: np.ndarray, rf: float = 0.0) -> float:
    """위험회피 λ = (E[r_mkt] − r_f)/σ²_mkt, σ²_mkt = w_mktᵀΣw_mkt, [1,5] 클립(§4.2)."""
    r = np.asarray(returns_panel, dtype="float64")
    r_mkt = r @ w_mkt
    var = float(w_mkt @ np.asarray(sigma, dtype="float64") @ w_mkt)
    if var <= 1e-18:
        log.warning("σ²_mkt≈0 → λ 기본값 2.5", extra={"stage": "engine.inputs"})
        return 2.5
    lam = (float(np.mean(r_mkt)) - rf) / var
    if not np.isfinite(lam):
        log.warning("λ 비유한 → 2.5", extra={"stage": "engine.inputs"})
        return 2.5
    clipped = float(np.clip(lam, *LAMBDA_CLIP))
    if abs(clipped - lam) > 1e-9:
        log.warning(f"λ={lam:.3f} → [1,5] 클립 {clipped}", extra={"stage": "engine.inputs"})
    return clipped


def build_views(
    assets: pd.DataFrame, scaler: dict | None = None, axis_weights: dict | None = None
) -> np.ndarray:
    """3축 신호(news/pattern/relationship) → (고정 스케일러 또는 배치 z-score) 표준화 →
    가중합 q_raw(단위 없음) 반환.

    axis_weights 미지정 시 모듈 기본 AXIS_WEIGHTS 사용 — 백테스트 역산(eval.calibrate)이
    이 가중을 override 해 실현 IC 로 추정값을 찾는다. anomaly 는 방향 뷰가 아니라 Ω 신뢰도
    변조 요인(assemble_bl_inputs)이므로 Q 에는 포함하지 않는다.
    """
    n = len(assets)

    def col(name: str, default: float = 0.0) -> np.ndarray:
        if name not in assets.columns:
            return np.full(n, default)
        return assets[name].fillna(default).to_numpy(dtype="float64")

    def axis_raw(name: str) -> np.ndarray:
        if name == "news":
            return col("gemini_score")
        if name == "pattern":
            return col("prob_growth_raw") - col("prob_churn_raw")
        if name == "relationship":
            return col("relationship_score")
        raise ValueError(name)

    q_raw = np.zeros(n)
    warned = False
    for axis, weight in (axis_weights or AXIS_WEIGHTS).items():
        raw = axis_raw(axis)
        if scaler and axis in scaler:
            mu, sd = scaler[axis]
            z = (raw - mu) / sd if sd > 1e-12 else np.zeros_like(raw)
        else:
            if not warned:
                log.warning(
                    "고정 스케일러 미주입 → 배치 z-score 폴백(추론 누수 위험, ADR-0004)",
                    extra={"stage": "engine.inputs"},
                )
                warned = True
            z = _zscore(raw)
        q_raw = q_raw + weight * z
    return q_raw


def _norm_weights(x: np.ndarray) -> np.ndarray:
    """음수 클립 후 합=1 정규화. NaN/Inf는 앵커 손상을 막기 위해 거부(균등붕괴 금지)."""
    x = np.asarray(x, dtype="float64")
    if not np.isfinite(x).all():
        raise ValueError("가중치에 NaN/Inf 가 있습니다(앵커 무음 균등붕괴 방지).")
    x = np.clip(x, 0.0, None)
    s = x.sum()
    return x / s if s > 0 else np.full(len(x), 1.0 / len(x))


def assemble_bl_inputs(
    assets: pd.DataFrame,
    returns_panel,
    *,
    tau: float = DEFAULT_TAU,
    risk_aversion: float | None = None,
    rf: float = 0.0,
    scaler: dict | None = None,
    conf_cal: float | None = None,
    axis_weights: dict | None = None,
    omega_scale: float = 1.0,
    gamma_anom: float | None = None,
    preference: str | None = None,
) -> dict:
    """자산 메타(assets)와 수익률 패널(T×N)로 BL 입력 dict를 구성한다(절대뷰 P=I).

    Q는 분산정합(Var(Q)=τ·mean(diagΣ))으로 τΣ·Ω와 단위를 맞추고 |Q|≤3σ로 클립한다.
    Ω = base·min(1/DRI²,M_DRI)·((1−conf)/c_cal)·(1+γ_anom·anomaly), base=τΣkk, 하한 η·base.
    anomaly(이상 크기 ∈[0,1])는 in-distribution 신뢰도 신호로 Ω 를 팽창시킨다(방향 뷰 아님).
    gamma_anom 미지정 시 모듈 GAMMA_ANOM 사용(eval.calibrate 가 실현지표로 역산).
    """
    n = len(assets)
    panel = np.asarray(returns_panel, dtype="float64")
    if panel.ndim != 2 or panel.shape[1] != n:
        raise ValueError(f"returns_panel shape {panel.shape} 는 (T,{n}) 여야 합니다.")

    tickers = (
        assets["corp_code"].astype("string").tolist()
        if "corp_code" in assets.columns else list(map(str, range(n)))
    )
    if len(set(tickers)) != len(tickers):
        raise ValueError("중복 corp_code(자산)가 있습니다 — ID_CROSSWALK dedup 필요(§9.4 자산 유일성).")

    sigma = shrunk_covariance(panel, preference)          # FULL 공분산(PSD 보장)

    if "w_mkt" in assets.columns:
        w_mkt = _norm_weights(assets["w_mkt"].to_numpy(dtype="float64"))
    elif "wallet_size" in assets.columns:
        w_mkt = _norm_weights(assets["wallet_size"].to_numpy(dtype="float64"))
    else:
        raise ValueError("assets 에 'w_mkt' 또는 'wallet_size' 컬럼이 필요합니다.")
    w_current = (
        _norm_weights(assets["w_current"].to_numpy(dtype="float64"))
        if "w_current" in assets.columns else w_mkt.copy()
    )

    lam = risk_aversion if risk_aversion is not None else calibrate_lambda(panel, w_mkt, sigma, rf)
    pi = lam * (sigma @ w_mkt)                             # 내재균형수익 Π=λΣw_mkt
    dri = compute_dri(assets)

    # Q 단위정합: c = sqrt(τ·mean(diagΣ)/Var(q_raw)) → Var(Q)=τ·mean(diagΣ) (§5.2 method 2)
    q_raw = build_views(assets, scaler, axis_weights)
    mean_var = float(np.mean(np.diag(sigma)))
    var_qraw = float(np.var(q_raw))
    c = math.sqrt(tau * mean_var / var_qraw) if var_qraw > 1e-18 else 0.0
    q = q_raw * c
    q_clip = Q_CLIP_SIGMA * math.sqrt(max(mean_var, 1e-18))
    q = np.clip(q, -q_clip, q_clip)                       # |Q| ≤ 3σ_asset

    # Ω: base=(PτΣPᵀ)kk=τΣkk, ∝1/DRI²(캡 M_DRI), confidence 보정(c_cal=mean(1−conf)),
    #    anomaly 신뢰도 변조(1+γ·anomaly), 하한 η·base
    base = tau * np.diag(sigma)
    cg = assets["confidence_growth"].fillna(0.5).to_numpy("float64") \
        if "confidence_growth" in assets.columns else np.full(n, 0.5)
    gc = assets["gemini_confidence"].fillna(0.5).to_numpy("float64") \
        if "gemini_confidence" in assets.columns else np.full(n, 0.5)
    conf = np.clip((cg + gc) / 2.0, 0.0, 1.0)
    ccal = conf_cal if conf_cal is not None else float(np.clip(np.mean(1.0 - conf), 0.05, 1.0))
    inv_dri2 = np.minimum(1.0 / dri**2, M_DRI)
    # anomaly_score_raw ∈[0,1](이상 크기). 결측/부재 시 0 → 요인 1(graceful). DRI·conf 형제 신뢰도 요인:
    # 이상할수록 Ω↑ → 그 법인 뷰가 prior(앵커)로 후퇴(= T2 cold-start 후퇴와 동일 메커니즘).
    gamma = GAMMA_ANOM if gamma_anom is None else float(gamma_anom)
    anomaly = (np.clip(assets["anomaly_score_raw"].fillna(0.0).to_numpy("float64"), 0.0, 1.0)
               if "anomaly_score_raw" in assets.columns else np.zeros(n))
    anom_factor = 1.0 + gamma * anomaly                   # ∈[1, 1+γ] 유계(수치 안전)
    omega_diag = base * inv_dri2 * ((1.0 - conf) / ccal) * anom_factor * float(omega_scale)
    omega_diag = np.maximum(omega_diag, OMEGA_FLOOR_ETA * base)  # 하한은 scale·anomaly 무관 안전장치

    return {
        "tickers": tickers,
        "Sigma": sigma,
        "pi": pi,
        "P": np.eye(n),
        "Q": q,
        "Omega": np.diag(omega_diag),
        "tau": tau,
        "w_mkt": w_mkt,
        "w_current": w_current,
        "DRI": dri,
        "lambda": lam,
        "metadata": {"n": n, "q_scale": c, "c_cal": ccal,
                     "axis_weights": axis_weights or AXIS_WEIGHTS, "omega_scale": float(omega_scale),
                     "gamma_anom": gamma},
    }
