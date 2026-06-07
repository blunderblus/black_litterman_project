"""엔드투엔드 통합 테스트 — 합성 데이터로 BL 전 파이프라인이 정합한 결정을 산출하는지 검증.

흐름: 자산메타+수익률패널 → assemble_bl_inputs → posterior_expected_return →
optimize_weights → compute_marketing_outputs. 토이의 퇴화/폭주/불일치가 재발 안 함을 확인.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bl.engine import inputs as bi
from bl.engine import optimize as opt
from bl.serve import mart


def _synthetic(n=12, t=120, seed=7):
    rng = np.random.default_rng(seed)
    f = rng.standard_normal((t, 1))
    panel = f @ rng.uniform(0.2, 1.0, (1, n)) * 0.02 + rng.standard_normal((t, n)) * 0.01
    assets = pd.DataFrame({
        "corp_code": [f"{i:08d}" for i in range(n)],
        "w_mkt": rng.uniform(0.5, 3.0, n),
        "w_current": rng.uniform(0.5, 3.0, n),
        "current_bal": rng.uniform(1e6, 1e9, n),
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
    return assets, panel


def test_end_to_end_pipeline_sane() -> None:
    assets, panel = _synthetic(12)
    inp = bi.assemble_bl_inputs(assets, panel)

    er = opt.posterior_expected_return(inp)
    assert er.shape == (12,)
    # 폭주 없음: |E[R]| 가 시장 변동성의 합리적 배수 이내(과거 1.29 류 아님)
    sigma_mkt = float(np.sqrt(inp["w_mkt"] @ inp["Sigma"] @ inp["w_mkt"]))
    assert np.abs(er).max() < 5 * sigma_mkt          # §9.4 폭주 가드(5σ)

    sigma_post = opt.posterior_covariance(inp)
    assert np.linalg.eigvalsh(sigma_post).min() > 0      # PSD 게이트 상속

    w = opt.optimize_weights(er, sigma_post, w_max=0.20)
    assert abs(w.sum() - 1.0) < 1e-6
    assert (w >= -1e-9).all() and (w <= 0.20 + 1e-6).all()

    df = assets[["corp_code", "current_bal"]].copy()
    df["current_weight"] = inp["w_current"]
    df["target_weight"] = w
    out = mart.compute_marketing_outputs(df, total_aum=float(assets["current_bal"].sum()))

    # 결정 정합: 점수 범위, 방향-액션 일관, weight_diff 비퇴화
    assert (out["marketing_score"].between(0, 100)).all()
    assert out["weight_diff"].abs().median() > 1e-6        # 퇴화(~1e-10) 아님
    for _, row in out.iterrows():
        if row["weight_diff"] < -mart.NEUTRAL_EPS:
            assert row["action_guide"] == mart.ACTION_DEFEND   # 축소에 매수 라벨 금지


def test_end_to_end_excludes_counterparties() -> None:
    # 금융 카운터파티 제외(설계 §7.2) → 해당 자산 가중 0
    assets, panel = _synthetic(10)
    inp = bi.assemble_bl_inputs(assets, panel)
    er = opt.posterior_expected_return(inp)
    w = opt.optimize_weights(er, inp["Sigma"], w_max=0.30, exclude=[0, 1])
    assert w[0] == 0.0 and w[1] == 0.0
    assert abs(w.sum() - 1.0) < 1e-6
