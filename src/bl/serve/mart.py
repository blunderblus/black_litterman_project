"""대시보드 마트 + §8 출력 변환 — weight_diff → marketing_score → action_guide → funding_gap.

설계: docs/design/03-bl-model-design.md §8, docs/design/02-data-pipeline.md §3.2.3(마트 권위 스키마).
과거 토이 결함 교정: weight_diff 퇴화(~1e-7)·방향-액션 불일치·라벨 문자열 산재·0잔액 100점 차단.
- marketing_score: weight_diff 부호 보존 퍼센타일(§8.2).
- action_guide: weight_diff 부호와 일관(단일 상수 소스). NaN/0잔액은 매수 패밀리에서 제외.
- funding_gap: tier factor는 **score와 독립**(연속 함수 또는 명시 등급) — 경계 점프·이중가중 제거.
  0/극소/비유한 잔액은 부호 무관 gap=0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from bl.common.logging import get_logger

if TYPE_CHECKING:
    import pandas as pd

log = get_logger(__name__)

# 액션 라벨 단일 소스(산출=집계 문자열 불일치 차단)
ACTION_AGGRESSIVE = "적극 유치 (Aggressive Buy)"
ACTION_BUY = "유치 확대 (Buy)"
ACTION_HOLD = "유지 (Hold)"
ACTION_DEFEND = "관망/방어 (Watch/Defend)"
ACTION_NEW = "신규유치 후보 (New Lead)"

SCORE_PRIME = 80.0
SCORE_CORE = 50.0
TIER_FACTOR = {"PRIME": 0.10, "CORE": 0.05, "WATCH": 0.02}   # 명시 등급 계수(§8.2)
NEUTRAL_EPS = 1e-6
MIN_BAL_FOR_GAP = 1.0      # 잔액이 이 값 미만/비유한이면 funding_gap 0(0/극소 잔액 가드)


def marketing_score(weight_diff: np.ndarray) -> np.ndarray:
    """weight_diff Δ → marketing_score[0,100] (부호 보존 퍼센타일, §8.2).

    Δ≥0: 50 + 50·Δ/q+ (q+ = 양수 Δ의 p95). Δ<0: 50·(1 − |Δ|/|q−|) (q− = 음수 Δ의 p5).
    """
    d = np.asarray(weight_diff, dtype="float64")
    score = np.full(len(d), 50.0)
    pos = d > NEUTRAL_EPS
    neg = d < -NEUTRAL_EPS
    if pos.any():
        qp = float(np.percentile(d[pos], 95))
        if qp > 0:
            score[pos] = 50.0 + 50.0 * (d[pos] / qp)
    if neg.any():
        qn = float(np.percentile(d[neg], 5))
        if qn < 0:
            score[neg] = 50.0 * (1.0 - (np.abs(d[neg]) / abs(qn)))
    return np.clip(score, 0.0, 100.0)


def action_guide(weight_diff: np.ndarray, score: np.ndarray) -> list[str]:
    """weight_diff 부호와 score로 액션 라벨 부여(부호 일관). NaN Δ는 HOLD(미지)로 처리."""
    d = np.asarray(weight_diff, dtype="float64")
    s = np.asarray(score, dtype="float64")
    out: list[str] = []
    for di, si in zip(d, s, strict=True):
        if not np.isfinite(di) or abs(di) <= NEUTRAL_EPS:
            out.append(ACTION_HOLD)
        elif di > 0:
            out.append(ACTION_AGGRESSIVE if si >= SCORE_PRIME else ACTION_BUY)
        else:
            out.append(ACTION_DEFEND)
    return out


def _continuous_factor(score: np.ndarray) -> np.ndarray:
    """score→funding 계수: WATCH..PRIME 사이 단조 **연속**(로지스틱). 경계 점프 제거(§8.2 대안)."""
    lo, hi = TIER_FACTOR["WATCH"], TIER_FACTOR["PRIME"]
    s = np.asarray(score, dtype="float64")
    return lo + (hi - lo) / (1.0 + np.exp(-(s - 65.0) / 8.0))


def funding_gap(
    weight_diff: np.ndarray,
    score: np.ndarray,
    total_aum: float,
    current_bal: np.ndarray | None = None,
    tier_class: np.ndarray | None = None,
) -> np.ndarray:
    """권고 자금 재배분 = Δ × TOTAL_AUM × f. f는 score와 **독립**(명시 등급 또는 연속함수).

    tier_class(PRIME/CORE/WATCH) 제공 시 그 등급 계수, 없으면 score의 연속 단조함수.
    0/극소/비유한 잔액은 부호 무관 gap=0.
    """
    d = np.asarray(weight_diff, dtype="float64")
    if tier_class is not None:
        factor = np.array([TIER_FACTOR.get(str(t).upper(), TIER_FACTOR["WATCH"]) for t in tier_class])
    else:
        factor = _continuous_factor(score)
    gap = d * float(total_aum) * factor
    if current_bal is not None:
        bal = np.asarray(current_bal, dtype="float64")
        gap = np.where(~np.isfinite(bal) | (bal < MIN_BAL_FOR_GAP), 0.0, gap)
    return gap


def compute_marketing_outputs(
    df: pd.DataFrame,
    *,
    total_aum: float | None = None,
    w_target_col: str = "target_weight",
    w_current_col: str = "current_weight",
    bal_col: str = "current_bal",
    tier_class_col: str = "tier_class",
) -> pd.DataFrame:
    """마트 df에 weight_diff·marketing_score·action_guide·funding_gap 추가. total_aum 미지정 시
    current_bal(음수 제외) 합으로 추정. NaN 가중치는 거부, 0/극소·비유한 잔액은 매수 패밀리에서 제외."""
    out = df.copy()
    wt = out[w_target_col].to_numpy(dtype="float64")
    wc = out[w_current_col].to_numpy(dtype="float64")
    if not (np.isfinite(wt).all() and np.isfinite(wc).all()):
        raise ValueError("target/current weight 에 NaN/Inf 가 있습니다(상류 정합 점검).")
    diff = wt - wc
    score = marketing_score(diff)
    bal = out[bal_col].to_numpy(dtype="float64") if bal_col in out.columns else None

    if total_aum is not None:
        aum = float(total_aum)
    elif bal is not None:
        aum = float(np.nansum(np.clip(bal, 0.0, None)))   # 음수 잔액 제외
    else:
        aum = 0.0
    if aum <= 0:
        log.warning("TOTAL_AUM ≤ 0 — funding_gap 신뢰 불가", extra={"stage": "serve.mart"})

    tier_class = out[tier_class_col].to_numpy() if tier_class_col in out.columns else None
    actions = action_guide(diff, score)
    gap = funding_gap(diff, score, aum, bal, tier_class)

    # 0/극소/비유한 잔액: 매수 패밀리 제외(신규유치 후보) + score를 PRIME 미만으로 캡(과거 'bal=0 100점' 차단)
    if bal is not None:
        guard = ~np.isfinite(bal) | (bal < MIN_BAL_FOR_GAP)
        for i in np.where(guard)[0]:
            score[i] = min(score[i], SCORE_CORE)
            actions[i] = ACTION_NEW if diff[i] > NEUTRAL_EPS else ACTION_HOLD

    # §9.4 퇴화 진단(분포가 한 점에 몰리면 경고)
    if len(score) >= 10 and float(np.std(score)) < 2.0:
        log.warning(
            f"marketing_score 분포 퇴화 의심(std={np.std(score):.2f}<2)",
            extra={"stage": "serve.mart"},
        )

    out["weight_diff"] = diff
    out["marketing_score"] = score
    out["action_guide"] = actions
    out["funding_gap"] = gap
    return out
