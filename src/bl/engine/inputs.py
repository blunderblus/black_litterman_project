"""BL 입력 빌더 — Σ · Π(앵커, ∝Σw_mkt) · P · Q(4축) · Ω(∝1/DRI²) · τ 구성.

설계: docs/design/03-bl-model-design.md §4~§5. 과거 노트북 09 대응.
과거 토이 결함 교정: 앵커를 w_mkt로(현상유지 w_hybrid 아님), Q·Ω·τΣ **단위 정합**(분산정합),
Ω∝1/DRI²(+§5.4 하한·M_DRI 캡) 보존, confidence 기반 가중(c_cal 데이터기반).

앵커 λ(C3 해소): Π=λΣw_mkt 의 λ는 **위험회피계수가 아니라 Π 스케일 정규화 상수**다(추정 대상 아님).
과거의 역최적화 캘리브레이션(calibrate_lambda)은 폐기했다 — 앵커를 '균형(equilibrium)'이 아니라
'무위 기본값(do-nothing default)'으로 재정의했으므로 역산할 이론 근거가 없고, 실측에서도 합성/실데이터
시장수익 부호 때문에 역산 λ가 음수→[1,5] 하한으로 항상 클립되어 사실상 상수였다(앵커 사후기여 ~3%로
증발). 대신 Π를 뷰 Q 와 **동일 스케일로 명시 정규화**(LAMBDA_FIXED)하여 앵커가 사후에 의미 있는
카운터웨이트(기본 τ에서 ~30%)가 되게 하고, 앵커↔뷰 균형(=영업 공격성)은 **τ 하나로 일원화**한다
(λ·τ 이중 손잡이의 식별 불가 문제 제거). 단위정합으로 Q∝√τ·Ω∝τ 라 W=τΣ(τΣ+Ω)⁻¹ 는 τ 무관이고
Π를 τ 무관 스케일로 고정하므로, τ↑→뷰 지배(공격적)·τ↓→앵커 지배(보수적)가 단조 성립한다.

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

AXIS_WEIGHTS = {"news": 0.35, "pattern": 0.35, "anomaly": 0.15, "relationship": 0.15}
DRI_WEIGHTS = {"has_financial": 0.3, "has_news": 0.25, "is_listed": 0.15, "trx_activity": 0.2}
DRI_BASE = 0.1
DEFAULT_TAU = 0.05
TAU_REF = DEFAULT_TAU       # Π 스케일 정규화 기준 τ(런타임 τ와 분리 → τ가 앵커↔뷰 손잡이로 작동)
OMEGA_FLOOR_ETA = 0.05      # Ω 하한 = η·(PτΣPᵀ)kk (§5.4, 과신 폭주 방지)
M_DRI = 100.0             # 1/DRI² 증폭 상한(§5.4)
# Π 스케일 정규화 상수(무차원). Π=λΣw_mkt 의 λ 자리이나 **위험회피계수가 아니다**(추정 대상 아님).
# 정의: 기준 τ_ref 에서 ‖Π‖/‖Q‖. 앵커-뷰 균형(=영업 공격성)은 λ가 아니라 τ로 조절한다(C3).
# 값 동결 근거: demo 데이터에서 τ=0.05 기준 앵커 사후기여(precision-form §9.2)가 ~30%(20–50% 권고대)가
# 되도록 스윕 선정(REPORT.md 측정표). 과거 calibrate_lambda 는 시장수익 부호 탓에 항상 1.0(앵커 ~3%).
LAMBDA_FIXED = 0.25
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


def build_views(
    assets: pd.DataFrame, scaler: dict | None = None, axis_weights: dict | None = None
) -> np.ndarray:
    """4축 신호 → (고정 스케일러 또는 배치 z-score) 표준화 → 가중합 q_raw(단위 없음) 반환.

    anomaly 축: 거래흐름(trx_in/out)이 있으면 방향 부호 적용, 없으면 부호 미적용(신호 보존).
    axis_weights 미지정 시 모듈 기본 AXIS_WEIGHTS 사용 — 백테스트 역산(eval.calibrate)이
    이 가중을 override 해 실현 IC 로 추정값을 찾는다.
    """
    n = len(assets)
    have_flow = "trx_in" in assets.columns and "trx_out" in assets.columns

    def col(name: str, default: float = 0.0) -> np.ndarray:
        if name not in assets.columns:
            return np.full(n, default)
        return assets[name].fillna(default).to_numpy(dtype="float64")

    def axis_raw(name: str) -> np.ndarray:
        if name == "news":
            return col("gemini_score")
        if name == "pattern":
            return col("prob_growth_raw") - col("prob_churn_raw")
        if name == "anomaly":
            a = col("anomaly_score_raw")
            return a * np.sign(col("trx_in") - col("trx_out")) if have_flow else a
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
    scaler: dict | None = None,
    conf_cal: float | None = None,
    axis_weights: dict | None = None,
    omega_scale: float = 1.0,
    preference: str | None = None,
) -> dict:
    """자산 메타(assets)와 수익률 패널(T×N)로 BL 입력 dict를 구성한다(절대뷰 P=I).

    Π=앵커(∝Σw_mkt), 스케일은 LAMBDA_FIXED 로 뷰 Q(τ_ref)에 정규화(C3: λ는 정규화 상수, 위험회피 아님).
    risk_aversion 은 그 λ override(테스트용); None 이면 LAMBDA_FIXED. 앵커↔뷰 균형은 τ로 일원화.
    Q는 분산정합(Var(Q)=τ·mean(diagΣ))으로 τΣ·Ω와 단위를 맞추고 |Q|≤3σ로 클립한다.
    Ω = base·min(1/DRI²,M_DRI)·((1−conf)/c_cal), base=τΣkk, 하한 η·base. c_cal=mean(1−conf).
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

    # 앵커 Π(do-nothing default): 방향은 시장균형 Σw_mkt(현상유지 아님), 스케일은 뷰 Q 와 정합(C3).
    # λ는 위험회피계수가 아니라 'Π를 Q 스케일로 맞추는 무차원 정규화 상수'(추정 대상 아님).
    # q_ref=√(τ_ref·meanΣ)=Q 가 기준 τ_ref 에서 갖는 원소 스케일 → ‖Π‖=λ·‖Q(τ_ref)‖ 로 정규화.
    # q_ref 는 런타임 τ가 아닌 TAU_REF 고정 → Π는 τ 무관 → 앵커↔뷰 균형은 오직 τ가 조절(단조).
    mean_var = float(np.mean(np.diag(sigma)))
    lam = LAMBDA_FIXED if risk_aversion is None else float(risk_aversion)  # risk_aversion = λ override(테스트용)
    anchor = sigma @ w_mkt                                 # 시장균형 방향(shape 보존)
    rms_anchor = math.sqrt(float(np.mean(anchor**2)))
    pi_scale = lam * math.sqrt(TAU_REF * mean_var) / rms_anchor if rms_anchor > 1e-18 else 0.0
    pi = pi_scale * anchor                                 # Π = pi_scale·Σw_mkt, ‖Π‖ = λ·‖Q(τ_ref)‖
    dri = compute_dri(assets)

    # Q 단위정합: c = sqrt(τ·mean(diagΣ)/Var(q_raw)) → Var(Q)=τ·mean(diagΣ) (§5.2 method 2)
    q_raw = build_views(assets, scaler, axis_weights)
    var_qraw = float(np.var(q_raw))
    c = math.sqrt(tau * mean_var / var_qraw) if var_qraw > 1e-18 else 0.0
    q = q_raw * c
    q_clip = Q_CLIP_SIGMA * math.sqrt(max(mean_var, 1e-18))
    q = np.clip(q, -q_clip, q_clip)                       # |Q| ≤ 3σ_asset

    # Ω: base=(PτΣPᵀ)kk=τΣkk, ∝1/DRI²(캡 M_DRI), confidence 보정(c_cal=mean(1−conf)), 하한 η·base
    base = tau * np.diag(sigma)
    cg = assets["confidence_growth"].fillna(0.5).to_numpy("float64") \
        if "confidence_growth" in assets.columns else np.full(n, 0.5)
    gc = assets["gemini_confidence"].fillna(0.5).to_numpy("float64") \
        if "gemini_confidence" in assets.columns else np.full(n, 0.5)
    conf = np.clip((cg + gc) / 2.0, 0.0, 1.0)
    ccal = conf_cal if conf_cal is not None else float(np.clip(np.mean(1.0 - conf), 0.05, 1.0))
    inv_dri2 = np.minimum(1.0 / dri**2, M_DRI)
    omega_diag = base * inv_dri2 * ((1.0 - conf) / ccal) * float(omega_scale)
    omega_diag = np.maximum(omega_diag, OMEGA_FLOOR_ETA * base)  # 하한은 scale 무관 안전장치

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
                     "lambda_effective": float(pi_scale)},
    }
