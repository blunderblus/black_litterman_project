"""데모 파이프라인 e2e + 신규 레이어(features/models/serve) 검증.

합성 샘플 → features(누수차단) → models(walk-forward) → BL → 마트 → 대시보드 산출까지
키 없이 전부 돌아가고 산출이 정합한지 확인한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bl.features.builder import FEATURE_COLS, FORBIDDEN_FEATURE_COLS, build_features_from_frames
from bl.synth.generate import generate_demo


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    d = tmp_path_factory.mktemp("sample")
    generate_demo(out_dir=d, seed=7)
    return {p.stem: pd.read_parquet(p) for p in Path(d).glob("*.parquet")}


def test_generate_demo_schema(sample) -> None:
    assert set(sample) == {"target_master", "post_data", "financial_wide", "company_sentiment", "macro"}
    assert sample["post_data"]["bal"].min() > 0          # 0 잔액 없음(log-return 안전)
    assert (sample["post_data"]["base_ym"].max()) == 202510


def test_features_no_leakage(sample) -> None:
    feat = build_features_from_frames(
        sample["post_data"], sample["financial_wide"], sample["macro"],
        sample["target_master"], 202510,
    )
    # 미래/라벨 컬럼이 피처 목록에 절대 없음
    assert not (FORBIDDEN_FEATURE_COLS & set(FEATURE_COLS))
    assert "bal_future_3m" not in feat["train"].columns or "bal_future_3m" not in FEATURE_COLS
    # inference 에는 라벨이 없어야(미래 미지)
    assert "label_churn" not in feat["inference"].columns
    # train 라벨은 결측 없이 0/1
    assert feat["train"]["label_churn"].notna().all()


def test_models_walk_forward_confidence(sample) -> None:
    from bl.models import growth_churn as gc

    feat = build_features_from_frames(
        sample["post_data"], sample["financial_wide"], sample["macro"],
        sample["target_master"], 202510,
    )
    models = gc.train(feat["train"], seed=7)
    # confidence 는 [0,1] 의 walk-forward AUC(하드코딩 0.85/0.65 아님)
    for t in ("growth", "churn"):
        assert 0.0 <= models[t]["confidence"] <= 1.0
    pred = gc.predict(models, feat["inference"])
    assert {"prob_growth_raw", "prob_churn_raw"} <= set(pred.columns)
    assert pred["prob_growth_raw"].between(0, 1).all()


def test_run_demo_end_to_end(tmp_path) -> None:
    from bl.pipeline import run_demo

    sdir = tmp_path / "sample"
    generate_demo(out_dir=sdir, seed=11)
    out = tmp_path / "site"
    r = run_demo(sample_dir=sdir, out_dir=out, seed=11)
    m = r["mart"]

    # 마트 정합: 점수 범위·가중치 합·비퇴화·방향-액션 일관
    assert (m["marketing_score"].between(0, 100)).all()
    assert abs(m["target_weight"].sum() - 1.0) < 1e-6
    assert m["weight_diff"].abs().median() > 1e-6        # 퇴화(1e-7) 아님
    assert m["bl_return"].abs().max() < 1.0              # 폭주(1.29) 아님
    from bl.serve.mart import ACTION_DEFEND, NEUTRAL_EPS
    for _, row in m.iterrows():
        if row["weight_diff"] < -NEUTRAL_EPS and row["current_bal"] >= 1.0:
            assert row["action_guide"] == ACTION_DEFEND

    # 대시보드 산출물 생성 + 외부 데이터 분리(크기 상한)
    assert (out / "index.html").exists()
    assert (out / "data.js").exists()
    data = json.loads((out / "data.json").read_text(encoding="utf-8"))
    assert data["summary"]["n"] == len(m)
    assert (out / "data.json").stat().st_size < 2_000_000   # 경량(246MB 인라인 아님)


def test_dashboard_export_topn(tmp_path) -> None:
    from bl.serve.dashboard_data import export_dashboard_json

    df = pd.DataFrame({
        "corp_code": [f"{i:08d}" for i in range(50)],
        "marketing_score": np.linspace(0, 100, 50),
        "current_bal": np.full(50, 1e7),
        "funding_gap": np.zeros(50),
    })
    export_dashboard_json(df, tmp_path, top_n=10)
    data = json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))
    assert len(data["rows"]) == 10                       # 상위 N 제한
    assert data["rows"][0]["marketing_score"] >= data["rows"][-1]["marketing_score"]  # 내림차순
