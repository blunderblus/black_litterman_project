"""BL 입력 빌더 — Σ · Π(앵커, ∝Σw_mkt) · P(블록스택) · Q(뷰별 블록) · Ω(블록 + off-diag) · τ 구성.

설계: docs/design/03-bl-model-design.md §4~§5. 과거 노트북 09 대응.
과거 토이 결함 교정: 앵커를 w_mkt로(현상유지 w_hybrid 아님), Q·Ω·τΣ **단위 정합**(분산정합),
Ω∝1/DRI²(+§5.4 하한·M_DRI 캡) 보존, confidence 기반 가중(c_cal 데이터기반).

뷰 레지스트리 비계(E3a): 과거의 '단일 병합 Q'(축가중합 → 법인당 1뷰)를 **뷰 레지스트리 기반
블록스택**으로 재구성한다. 진짜 방향성 뷰는 news·pattern 둘인데 **둘 다 번영축**(기업이 잘되나)이라
상관돼 있다. 단일 병합은 둘을 평균내 그 상관을 눌렀다. 이를 그냥 '독립 2뷰 + 대각 Ω'로 쪼개면
같은 번영신호를 두 번 세어 E1(거짓 과신)을 악화시킨다. 그래서 본 비계는 **융합 동작을 바꾸지 않는다**:
  - VIEW_REGISTRY = [news, pattern] (각 뷰 = 이름·신호추출·per-view confidence). relationship 은
    방향 예측이 아니라 현재상태=이동성이므로 뷰에서 제외하고 **Σ 이동성 슬롯으로 예약**(E1/갈래B 데이터
    대기, 아래 RELATIONSHIP_RESERVED 주석). anomaly 는 이미 E2로 Ω 신뢰도 요인.
  - P = 등록 뷰 수 K 에 대해 [I;I;…] 블록스택(KN×N). 현재 K=2.
  - Q = 뷰별 z-score 블록을 *쌓기*(병합 가중합 X). 각 블록을 Var=τ·mean(diagΣ)로 개별 단위정합.
  - Ω = KN×KN. per-view 대각(news뷰=gemini_confidence, pattern뷰=confidence_growth; E4 부분개선) ×
    공통 신뢰도 곱요인 DRI·(1+γ_anom·anomaly)[E2]. **off-diagonal**: 두 번영뷰 상관(표준화 신호상관
    프록시 R_view)을 넣어 독립확증 과신을 상쇄 → 단일병합과 동작 동등 유지. proper 버전(실현잔차 상관)은
    E1b(실데이터) 명시 보류.
동작 동등의 수학(정확히): **per-view ω 가 같을 때만**(ω_v 동일) 블록스택 결합 뷰값 q_eff 가 off-diag 무관한
**평균 (q₁+…+q_K)/K** 가 되어 방향이 off-diag 에 불변이고, 이때 off-diag R_view 는 결합 정밀도(=앵커↔뷰
균형, 보수성)만 조절한다. **일반(운영) 경로는 뷰별 confidence 가 상이해 ω_v 가 다르므로**(news=
gemini_confidence·pattern=confidence_growth, E4) q_eff 는 정밀도가중 혼합이라 off-diag 가 *방향(q_eff)도*
조절한다 — 즉 단일병합과의 '동작 동등(랭킹 보존)'은 무조건 불변량이 아니라 **기본 경로(경험 프록시 ρ가
작음)에서 성립하는 경험적 속성**이다(demo target_weight Spearman≈0.87, REPORT). R_view→I(독립)면 K배
과신, R_view→1(완전중복)이면 특이 Ω 직전(조건수 폭주, VIEW_CORR_MAX 로 차단). 미래뷰(이동성)는
R_view 가 작을수록 독립계상 → 한 줄 등록으로 꽂히는 플러그인 자리.

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
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from bl.common.logging import get_logger
from bl.engine.covariance import shrunk_covariance

if TYPE_CHECKING:
    import pandas as pd

log = get_logger(__name__)


@dataclass(frozen=True)
class ViewSpec:
    """방향성 뷰 1개의 레지스트리 명세 — 신호추출 + per-view 신뢰도 컬럼.

    name        : 뷰 식별자(스케일러 키·메타·로깅에 사용).
    signal      : assets DataFrame → 표준화 전 raw 방향신호(N,). +상승/−하락.
    conf_col    : 그 뷰의 Ω 신뢰도(=확신) 컬럼. 결측·부재 시 conf_default.
                  (E4 부분개선: 과거 cg·gc 평균 대신 뷰별 축에 confidence 배치.)
    conf_default: conf_col 결측/부재 시 기본 confidence.
    """

    name: str
    signal: Callable[[pd.DataFrame], np.ndarray]
    conf_col: str
    conf_default: float = 0.5


def _signal_news(assets: pd.DataFrame) -> np.ndarray:
    return _col(assets, "gemini_score")


def _signal_pattern(assets: pd.DataFrame) -> np.ndarray:
    return _col(assets, "prob_growth_raw") - _col(assets, "prob_churn_raw")


# ── 뷰 레지스트리(canonical) ──────────────────────────────────────────────────
# 등록된 방향성 뷰 = [news, pattern]. 한 줄 추가로 K 가 늘고 파이프라인이 그대로 동작하는 '비계'.
VIEW_REGISTRY: list[ViewSpec] = [
    ViewSpec(name="news", signal=_signal_news, conf_col="gemini_confidence"),
    ViewSpec(name="pattern", signal=_signal_pattern, conf_col="confidence_growth"),
]
# RELATIONSHIP_RESERVED: relationship_score(거래관계 강도)는 *방향성 예측*이 아니라 현재상태=이동성
# (잘 안 움직이는 결속 고객인가)이다. 따라서 뷰 Q(방향)가 아니라 **Σ(공분산) 이동성 슬롯**에 들어가야
# 하며, 그 적절한 추정(고객별 잔액 이동성/끈끈함의 공분산 구조)은 실현 데이터(E1/갈래B)를 기다린다.
# 지금은 뷰에서 제외만 한다(과거 단일병합의 0.176 가중은 폐기). 데이터가 오면 여기에 Σ 슬롯을 연다.
RELATIONSHIP_RESERVED = "relationship_score"

DRI_WEIGHTS = {"has_financial": 0.3, "has_news": 0.25, "is_listed": 0.15, "trx_activity": 0.2}
DRI_BASE = 0.1
DEFAULT_TAU = 0.05
TAU_REF = DEFAULT_TAU       # Π 스케일 정규화 기준 τ(런타임 τ와 분리 → τ가 앵커↔뷰 손잡이로 작동)
OMEGA_FLOOR_ETA = 0.05      # Ω 하한 = η·(PτΣPᵀ)kk (§5.4, 과신 폭주 방지)
M_DRI = 100.0             # 1/DRI² 증폭 상한(§5.4)
# anomaly_score(이상 크기, in-distribution 여부)에 의한 Ω 팽창 게인. DRI·conf 와 같은 신뢰도 요인.
# 추정 대상이 아닌 기본값이며, 추후 eval.calibrate(calibrate_gamma_anom)에서 실현 적중률로 캘리브레이션한다.
GAMMA_ANOM = 2.0
# Π 스케일 정규화 상수(무차원). Π=λΣw_mkt 의 λ 자리이나 **위험회피계수가 아니다**(추정 대상 아님).
# 정의: 기준 τ_ref 에서 ‖Π‖/‖Q‖. 앵커-뷰 균형(=영업 공격성)은 λ가 아니라 τ로 조절한다(C3).
# 값 동결 근거: demo 데이터에서 τ=0.05 기준 앵커 사후기여(precision-form §9.2)가 ~30%(20–50% 권고대)가
# 되도록 스윕 선정(REPORT.md 측정표). 과거 calibrate_lambda 는 시장수익 부호 탓에 항상 1.0(앵커 ~3%).
LAMBDA_FIXED = 0.25
Q_CLIP_SIGMA = 3.0        # |Q| ≤ Q_CLIP_SIGMA·σ_asset 클립(§5.2)
# off-diagonal Ω 의 뷰상관 안전 클립(PSD·조건수 보호). R_view 고유값을 [ε, 1]로 클립해 |ρ|<1 보장
# → per-asset K×K Ω 블록이 강한 양정치 유지(완전중복 ρ=1 의 특이 Ω 회피, 정지조건 PSD 게이트).
VIEW_CORR_MAX = 0.98
_VIEW_CORR_EIG_FLOOR = 1e-6


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


def _col(assets: pd.DataFrame, name: str, default: float = 0.0) -> np.ndarray:
    n = len(assets)
    if name not in assets.columns:
        return np.full(n, default)
    return assets[name].fillna(default).to_numpy(dtype="float64")


def build_view_signals(
    assets: pd.DataFrame, scaler: dict | None = None,
    registry: list[ViewSpec] | None = None,
) -> tuple[list[str], np.ndarray]:
    """레지스트리 뷰별 raw 방향신호를 (고정 스케일러 또는 배치 z-score) 표준화 → (뷰이름들, Z[N,K]).

    Z 의 각 열 = 한 뷰의 표준화 신호(평균0·분산1 지향, 무차원). 가중합/병합 없음(블록스택 전제).
    scaler 미주입 시 배치 z-score 폴백(추론 누수 위험, ADR-0004)을 1회 경고한다.
    """
    reg = registry if registry is not None else VIEW_REGISTRY
    n = len(assets)
    cols: list[np.ndarray] = []
    names: list[str] = []
    warned = False
    for v in reg:
        raw = np.asarray(v.signal(assets), dtype="float64")
        if scaler and v.name in scaler:
            mu, sd = scaler[v.name]
            z = (raw - mu) / sd if sd > 1e-12 else np.zeros_like(raw)
        else:
            if not warned:
                log.warning(
                    "고정 스케일러 미주입 → 배치 z-score 폴백(추론 누수 위험, ADR-0004)",
                    extra={"stage": "engine.inputs"},
                )
                warned = True
            z = _zscore(raw)
        cols.append(z)
        names.append(v.name)
    z_mat = np.column_stack(cols) if cols else np.zeros((n, 0))
    return names, z_mat


def _view_correlation(z_mat: np.ndarray, override) -> np.ndarray:
    """뷰 off-diagonal 용 K×K 상관행렬 R_view (단위대각, PSD·|ρ|<1 보장).

    override 미지정(None) → 표준화 신호의 경험 상관(설계 프록시). 스칼라 → 모든 비대각을 그 값으로
    (대각 1). K×K 행렬 → 그대로 사용. 어느 경우든 고유값 바닥/상한 클립으로 PSD·비특이 보장.
    퇴화 신호(분산 0)는 상관 NaN → 0(독립) 처리한다.
    """
    k = z_mat.shape[1]
    if k == 0:
        return np.zeros((0, 0))
    if override is None:
        if z_mat.shape[0] < 2:
            r = np.eye(k)
        else:
            with np.errstate(invalid="ignore", divide="ignore"):
                r = np.corrcoef(z_mat, rowvar=False)
            r = np.atleast_2d(r)
            r = np.where(np.isfinite(r), r, 0.0)     # 퇴화 신호 → 독립
            np.fill_diagonal(r, 1.0)
    elif isinstance(override, (int, float)):
        r = np.full((k, k), float(override))
        np.fill_diagonal(r, 1.0)
    else:
        r = np.asarray(override, dtype="float64")
        if r.shape != (k, k):
            raise ValueError(f"view_corr shape {r.shape} != ({k},{k})")
    r = 0.5 * (r + r.T)
    # PSD·비특이 보장: 고유값을 [ε, 1]로 클립 후 단위대각 재정규화(완전중복 ρ→VIEW_CORR_MAX 로 제한).
    w, vecs = np.linalg.eigh(r)
    w = np.clip(w, _VIEW_CORR_EIG_FLOOR, None)
    r = (vecs * w) @ vecs.T
    d = np.sqrt(np.clip(np.diag(r), 1e-18, None))
    r = r / np.outer(d, d)                            # 상관행렬로 재정규화(단위대각)
    off = r - np.eye(k)
    off = np.clip(off, -VIEW_CORR_MAX, VIEW_CORR_MAX)  # |ρ|<1 (특이 Ω 회피)
    return np.eye(k) + off


def _norm_weights(x: np.ndarray) -> np.ndarray:
    """음수 클립 후 합=1 정규화. NaN/Inf는 앵커 손상을 막기 위해 거부(균등붕괴 금지)."""
    x = np.asarray(x, dtype="float64")
    if not np.isfinite(x).all():
        raise ValueError("가중치에 NaN/Inf 가 있습니다(앵커 무음 균등붕괴 방지).")
    x = np.clip(x, 0.0, None)
    s = x.sum()
    return x / s if s > 0 else np.full(len(x), 1.0 / len(x))


def _view_omega_diag(
    assets: pd.DataFrame, view: ViewSpec, *, base: np.ndarray, inv_dri2: np.ndarray,
    anom_factor: np.ndarray, omega_scale: float, conf_cal: float | None,
) -> tuple[np.ndarray, float]:
    """한 뷰의 per-asset Ω 대각(하한 전) + 그 뷰의 c_cal 반환.

    ω_view,i = base · min(1/DRI²,M) · ((1−conf_view)/c_cal_view) · (1+γ·anomaly) · omega_scale.
    conf_view = 그 뷰의 신뢰도 컬럼(news=gemini_confidence, pattern=confidence_growth; E4 부분개선).
    """
    n = len(assets)
    conf = (np.clip(assets[view.conf_col].fillna(view.conf_default).to_numpy("float64"), 0.0, 1.0)
            if view.conf_col in assets.columns else np.full(n, view.conf_default))
    ccal = conf_cal if conf_cal is not None else float(np.clip(np.mean(1.0 - conf), 0.05, 1.0))
    omega = base * inv_dri2 * ((1.0 - conf) / ccal) * anom_factor * float(omega_scale)
    return omega, ccal


def assemble_bl_inputs(
    assets: pd.DataFrame,
    returns_panel,
    *,
    tau: float = DEFAULT_TAU,
    risk_aversion: float | None = None,
    scaler: dict | None = None,
    conf_cal: float | None = None,
    view_corr: float | np.ndarray | None = None,
    omega_scale: float = 1.0,
    gamma_anom: float | None = None,
    preference: str | None = None,
    registry: list[ViewSpec] | None = None,
) -> dict:
    """자산 메타(assets)와 수익률 패널(T×N)로 BL 입력 dict를 구성한다(뷰 레지스트리 블록스택).

    Π=앵커(∝Σw_mkt), 스케일은 LAMBDA_FIXED 로 뷰 Q(τ_ref)에 정규화(C3: λ는 정규화 상수, 위험회피 아님).
    risk_aversion 은 그 λ override(테스트용); None 이면 LAMBDA_FIXED. 앵커↔뷰 균형은 τ로 일원화.

    뷰는 레지스트리 기반 **블록스택**(E3a):
      - P = [I;I;…] (KN×N), K=len(registry).
      - Q = 뷰별 표준화신호 블록을 쌓되 각 블록을 Var=τ·mean(diagΣ)로 개별 단위정합 + |Q|≤3σ 클립.
      - Ω = KN×KN. per-view 대각 ω_v = base·min(1/DRI²,M)·((1−conf_v)/c_cal_v)·(1+γ·anomaly)·scale,
        하한 η·base. off-diagonal = R_view[a,b]·√(ω_a·ω_b) (두 번영뷰 상관 → 독립확증 과신 상쇄,
        동작 동등 유지). view_corr 로 R_view 를 override(None=경험 신호상관 프록시, 스칼라/행렬 가능).
    anomaly(이상 크기 ∈[0,1])는 in-distribution 신뢰도 신호로 모든 뷰 Ω 를 공통 팽창시킨다(방향 뷰 아님).
    """
    reg = registry if registry is not None else VIEW_REGISTRY
    k = len(reg)
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

    # 뷰별 표준화 신호 Z[N,K] → 블록별 단위정합 Q_block[N,K] (Var(Q_v)=τ·mean(diagΣ))
    names, z_mat = build_view_signals(assets, scaler, reg)
    q_clip = Q_CLIP_SIGMA * math.sqrt(max(mean_var, 1e-18))
    q_scales: dict[str, float] = {}
    q_block = np.zeros((n, k))
    for j, name in enumerate(names):
        z = z_mat[:, j]
        var_z = float(np.var(z))
        c = math.sqrt(tau * mean_var / var_z) if var_z > 1e-18 else 0.0
        q_block[:, j] = np.clip(c * z, -q_clip, q_clip)   # |Q_v| ≤ 3σ_asset
        q_scales[name] = c

    # Ω 블록: per-view 대각 + off-diagonal(뷰상관 R_view). base=(PτΣPᵀ)kk=τΣkk, ∝1/DRI²(캡), conf, anomaly
    base = tau * np.diag(sigma)
    inv_dri2 = np.minimum(1.0 / dri**2, M_DRI)
    gamma = GAMMA_ANOM if gamma_anom is None else float(gamma_anom)
    anomaly = (np.clip(assets["anomaly_score_raw"].fillna(0.0).to_numpy("float64"), 0.0, 1.0)
               if "anomaly_score_raw" in assets.columns else np.zeros(n))
    anom_factor = 1.0 + gamma * anomaly                   # ∈[1, 1+γ] 유계(수치 안전)

    omega_views = np.zeros((n, k))                         # per-view 대각(하한 후)
    c_cals: dict[str, float] = {}
    for j, v in enumerate(reg):
        od, ccal = _view_omega_diag(assets, v, base=base, inv_dri2=inv_dri2,
                                    anom_factor=anom_factor, omega_scale=omega_scale, conf_cal=conf_cal)
        od = np.maximum(od, OMEGA_FLOOR_ETA * base)        # 하한은 scale·anomaly 무관 안전장치
        omega_views[:, j] = od
        c_cals[v.name] = ccal

    r_view = _view_correlation(z_mat, view_corr)           # K×K 뷰상관(PSD·|ρ|<1)

    # P=[I;…;I] (KN×N), Q=쌓기(KN,), Ω=KN×KN(per-asset K×K 블록을 view-major 로 배치)
    big_p = np.tile(np.eye(n), (k, 1))                     # (KN, N)
    big_q = q_block.T.reshape(k * n)                       # view-major: [view0 모든자산, view1 …]
    sqrt_w = np.sqrt(np.clip(omega_views, 0.0, None))      # (N,K)
    big_omega = np.zeros((k * n, k * n))
    for a in range(k):
        for b in range(k):
            d = r_view[a, b] * sqrt_w[:, a] * sqrt_w[:, b]  # (N,) off-diag(a,b) per asset
            big_omega[a * n:(a + 1) * n, b * n:(b + 1) * n] = np.diag(d)

    # 결합 per-corp 충분통계(역방향 호환 q/omega): 블록스택과 *동일 사후*를 내는 단일뷰 등가량.
    #   J_i = 1ᵀ G_i⁻¹ 1 (결합 정밀도), h_i = 1ᵀ G_i⁻¹ q_i → omega_eff=1/J, q_eff=h/J.
    # 이 (q_eff,omega_eff)는 "이 블록이 어떤 단일뷰와 동등한가"라서 로깅·대시보드에 의미를 보존한다.
    q_eff, omega_eff = _combined_view_stats(omega_views, q_block, r_view)

    return {
        "tickers": tickers,
        "Sigma": sigma,
        "pi": pi,
        "P": big_p,
        "Q": big_q,
        "Omega": big_omega,
        "tau": tau,
        "w_mkt": w_mkt,
        "w_current": w_current,
        "DRI": dri,
        "lambda": lam,
        "view_names": names,
        "q_eff": q_eff,            # 결합 per-corp 뷰값(단일뷰 등가)
        "omega_eff": omega_eff,    # 결합 per-corp 뷰분산(단일뷰 등가)
        "metadata": {"n": n, "n_views": k, "view_names": names,
                     "q_scale": q_scales, "c_cal": c_cals,
                     "view_corr": r_view.tolist(),
                     "omega_scale": float(omega_scale),
                     "gamma_anom": gamma, "lambda_effective": float(pi_scale)},
    }


def _combined_view_stats(
    omega_views: np.ndarray, q_block: np.ndarray, r_view: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """per-asset K×K 뷰블록 G_i = diag(√ω) R_view diag(√ω) 의 결합 단일뷰 등가 (q_eff, omega_eff).

    G_i⁻¹ 를 자산별 배치 역행렬로 구해 J_i=1ᵀG⁻¹1, h_i=1ᵀG⁻¹q → omega_eff=1/J, q_eff=h/J.
    블록스택 P=[I;…] 하에서 (q_eff,omega_eff)를 가진 단일뷰(P=I)와 BL 사후가 정확히 일치한다.
    """
    n, k = omega_views.shape
    if k == 0:
        return np.zeros(n), np.full(n, np.inf)
    sqrt_w = np.sqrt(np.clip(omega_views, 1e-300, None))            # (N,K)
    g = sqrt_w[:, :, None] * r_view[None, :, :] * sqrt_w[:, None, :]  # (N,K,K)
    ginv = np.linalg.inv(g)                                          # (N,K,K)
    ones = np.ones(k)
    j = np.einsum("a,nab,b->n", ones, ginv, ones)                   # 1ᵀG⁻¹1
    h = np.einsum("a,nab,nb->n", ones, ginv, q_block)               # 1ᵀG⁻¹q
    omega_eff = 1.0 / np.where(j > 1e-300, j, np.nan)
    q_eff = np.where(j > 1e-300, h / j, 0.0)
    return q_eff, omega_eff
