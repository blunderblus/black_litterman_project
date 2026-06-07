"""§8 출력 변환 테스트 — marketing_score(부호보존)·action_guide(부호일관)·funding_gap."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bl.serve import mart


def test_marketing_score_range_and_neutral() -> None:
    diff = np.array([0.10, 0.05, 0.0, -0.05, -0.10])
    s = mart.marketing_score(diff)
    assert (s >= 0).all() and (s <= 100).all()
    assert abs(s[2] - 50.0) < 1e-9          # Δ=0 → 50(중립)
    assert s[0] >= s[1] >= s[2] >= s[3] >= s[4]  # 단조(Δ 큰 쪽이 높은 점수)


def test_marketing_score_not_degenerate() -> None:
    # 다양한 Δ → 점수가 100/50 양극에 몰리지 않음(과거 퇴화 방지)
    rng = np.random.default_rng(0)
    diff = rng.normal(0, 0.02, 200)
    s = mart.marketing_score(diff)
    assert s.std() > 5.0                     # 유효 분산


def test_action_guide_sign_consistent() -> None:
    diff = np.array([0.10, 0.02, 0.0, -0.05])
    s = mart.marketing_score(diff)
    g = mart.action_guide(diff, s)
    assert g[2] == mart.ACTION_HOLD          # Δ=0
    assert g[3] == mart.ACTION_DEFEND        # Δ<0 → 절대 'Buy' 아님(부호 일관)
    assert g[0] in (mart.ACTION_AGGRESSIVE, mart.ACTION_BUY)
    # Δ<0 에 매수 라벨이 절대 붙지 않음
    for di, gi in zip(diff, g, strict=True):
        if di < -mart.NEUTRAL_EPS:
            assert gi == mart.ACTION_DEFEND


def test_funding_gap_zero_balance_guard() -> None:
    diff = np.array([0.05, 0.05])
    s = mart.marketing_score(diff)
    gap = mart.funding_gap(diff, s, total_aum=1e9, current_bal=np.array([0.0, 1e8]))
    assert gap[0] == 0.0                      # 0 잔액 → 거액 gap 금지
    assert gap[1] != 0.0


def test_funding_gap_guards_both_signs_and_nan_balance() -> None:
    # 음수 Δ(축소)·NaN 잔액도 0/극소 잔액이면 gap=0 (리뷰 #2)
    diff = np.array([-0.05, 0.05, 0.05])
    s = mart.marketing_score(diff)
    gap = mart.funding_gap(diff, s, total_aum=1e9, current_bal=np.array([0.0, np.nan, 1e8]))
    assert gap[0] == 0.0          # 음수 Δ + 0 잔액 → withdraw 권고 금지
    assert gap[1] == 0.0          # NaN 잔액 → 가드
    assert gap[2] != 0.0


def test_funding_gap_factor_score_independent_no_jump() -> None:
    # 경계(80/50)에서 2x 점프가 없어야(연속 단조) (리뷰 #1)
    diff = np.array([0.05, 0.05])
    g_lo = mart.funding_gap(diff, np.array([79.9, 79.9]), 1e9)
    g_hi = mart.funding_gap(diff, np.array([80.1, 80.1]), 1e9)
    assert abs(g_hi[0] / g_lo[0] - 1.0) < 0.05      # 점프 아님(연속)


def test_funding_gap_explicit_tier_class() -> None:
    diff = np.array([0.05, 0.05])
    gap = mart.funding_gap(diff, np.array([10.0, 10.0]), 1e9, tier_class=np.array(["PRIME", "WATCH"]))
    assert gap[0] / gap[1] == mart.TIER_FACTOR["PRIME"] / mart.TIER_FACTOR["WATCH"]


def test_zero_balance_not_aggressive_buy() -> None:
    # 0 잔액 계좌가 100점 'Aggressive Buy' 받지 않음(리뷰 #3, 과거 결함 차단)
    df = pd.DataFrame({"target_weight": [0.20], "current_weight": [0.0], "current_bal": [0.0]})
    out = mart.compute_marketing_outputs(df, total_aum=1e9)
    assert out.loc[0, "marketing_score"] <= mart.SCORE_CORE
    assert out.loc[0, "action_guide"] == mart.ACTION_NEW
    assert out.loc[0, "funding_gap"] == 0.0


def test_compute_rejects_nan_weights() -> None:
    df = pd.DataFrame({"target_weight": [0.1, np.nan], "current_weight": [0.05, 0.05]})
    with pytest.raises(ValueError):
        mart.compute_marketing_outputs(df, total_aum=1e9)


def test_compute_marketing_outputs_columns() -> None:
    df = pd.DataFrame({
        "target_weight": [0.10, 0.02, 0.01],
        "current_weight": [0.02, 0.02, 0.05],
        "current_bal": [1e8, 1e7, 1e6],
    })
    out = mart.compute_marketing_outputs(df)
    for col in ["weight_diff", "marketing_score", "action_guide", "funding_gap"]:
        assert col in out.columns
    # weight_diff 부호와 action 일관
    assert out.loc[2, "weight_diff"] < 0
    assert out.loc[2, "action_guide"] == mart.ACTION_DEFEND
