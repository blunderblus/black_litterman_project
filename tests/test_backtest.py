"""eval.backtest — walk-forward 백테스트(누수 차단·실현수익 채점) 테스트.

설계 §9.1. 합성 데이터엔 학습가능한 성장/이탈 신호가 존재하므로, 하니스가 그 신호를 잡아
naive(지갑규모) 베이스라인을 이기는 positive-control 까지 함께 검증한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bl.eval import backtest as bt
from bl.pipeline import _load_sample


def test_realized_forward_returns_sign_and_value() -> None:
    post = pd.DataFrame({
        "corp_code": ["A", "A", "A", "A", "B", "B", "B", "B"],
        "base_ym": [202401, 202402, 202403, 202404] * 2,
        "bal": [100.0, 110.0, 120.0, 130.0, 100.0, 90.0, 80.0, 70.0],
    })
    r = bt.realized_forward_returns(post, 202401, 202404)
    assert r["A"] > 0 and r["B"] < 0                       # 100→130 상승, 100→70 하락
    assert np.isclose(r["A"], np.log(130.0 / 100.0))       # log-return 정확


def test_realized_forward_returns_missing_future() -> None:
    post = pd.DataFrame({"corp_code": ["A"], "base_ym": [202401], "bal": [100.0]})
    assert bt.realized_forward_returns(post, 202401, 202404).empty


def test_truncate_frames_is_point_in_time() -> None:
    frames = _load_sample("data/sample")
    months = sorted(int(m) for m in frames["post_data"]["base_ym"].unique())
    cut = months[len(months) // 2]
    ft = bt._truncate_frames(frames, cut)
    assert int(ft["post_data"]["base_ym"].max()) <= cut    # 미래 잔액 누수 없음
    assert int(frames["post_data"]["base_ym"].max()) > cut  # 원본 프레임 불변


def test_run_backtest_structure_and_beats_naive() -> None:
    frames = _load_sample("data/sample")
    out = bt.run_backtest(frames, step=4)                  # step 크게 → 윈도우 수 제한(빠른 테스트)
    s, pw = out["summary"], out["per_window"]

    # 구조 + 누수 시점분리(평가시점이 항상 base_ym 보다 미래)
    assert s["n_windows"] >= 1 and not pw.empty
    assert (pw["future_ym"] > pw["base_ym"]).all()
    for k in ("mean_ret_bl", "mean_ret_market", "lift_bl_vs_market",
              "win_rate_bl_gt_market", "ir_bl", "mean_ic"):
        assert np.isfinite(s[k])

    # positive-control: 합성 신호가 있으므로 BL 이 지갑규모 베이스라인을 이겨야 한다
    assert s["lift_bl_vs_market"] > 0
    assert s["win_rate_bl_gt_market"] >= 0.5
    assert s["mean_ic"] > 0                                # 뷰가 실현수익과 양의 순위상관
