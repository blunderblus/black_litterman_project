"""처치(treatment) 레이어 + uplift 채점 — raw 실현수익(번영)에서 *처치 vs 대조 uplift*(영업효과)로.

substrate(ledger/backtest)가 채점하던 realized_forward_returns 는 "그 후 잔액이 얼마나 늘었나"(번영)라
영업효과(uplift)가 아니었다. 본 테스트는 처치 레이어 스키마, 식별 3분기(RCT/관측처치/번영프록시),
그리고 합성 known-uplift 복원(raw 편향 vs DiD·매칭 편향제거)을 검증한다(설계 §9, ADR-0004).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bl.common.io import duckdb_connect, upsert
from bl.eval.backtest import _truncate_frames
from bl.pipeline import _load_sample, run_from_frames
from bl.serve import ledger as lg
from bl.synth.generate import generate_treatment_scenario

# ── 1. 스키마: 처치 레이어가 additive 하며 권고 시점엔 null(영업 전) ──────────────────────────

def test_build_log_frame_adds_null_treatment_layer() -> None:
    frames = _load_sample("data/sample")
    months = sorted(int(m) for m in frames["post_data"]["base_ym"].unique())
    res = run_from_frames(_truncate_frames(frames, months[10]), base_ym=months[10], render=False)
    df = lg.build_log_frame(res, run_ts="20260101T000000Z")
    assert set(lg.TREATMENT_COLS) <= set(df.columns)            # 처치 컬럼 존재
    for c in lg.TREATMENT_COLS:
        assert df[c].isna().all()                               # 권고 시점엔 전부 null(영업 전)


def test_demo_logging_intact_with_treatment_schema(tmp_path) -> None:
    """기존 demo 파이프라인/로깅이 처치 컬럼 추가 후에도 정상(처치 null 상태로 적재)."""
    frames = _load_sample("data/sample")
    months = sorted(int(m) for m in frames["post_data"]["base_ym"].unique())
    db = str(tmp_path / "led.duckdb")
    res = run_from_frames(_truncate_frames(frames, months[10]), base_ym=months[10], render=False)
    n = lg.append_recommendations(res, db_path=db, run_ts="20260101T000000Z")
    led = lg.read_ledger(db)
    assert len(led) == n > 0
    assert {"run_ts", "base_ym", "corp_code", "target_weight"} <= set(led.columns)  # 기존 필드 보존
    assert set(lg.TREATMENT_COLS) <= set(led.columns)                               # 처치 레이어 적재
    for c in ("treated", "holdout_flag", "treat_ym"):
        assert pd.to_numeric(led[c], errors="coerce").isna().all()                  # 적재 직후 null


def test_append_migrates_pre_treatment_ledger(tmp_path) -> None:
    """처치 레이어 이전에 적재된 원장(처치 컬럼 없음)에 append 해도 깨지지 않는다(additive 진화).

    원장의 목적은 시간축 누적이라 실배포엔 사전 테이블이 존재한다 — upsert 의 '신규 컬럼 미지원'
    가드에 걸리지 않도록 누락 처치 컬럼을 ADD COLUMN 으로 선제 진화하는지 검증.
    """
    db = str(tmp_path / "led.duckdb")
    frames = _load_sample("data/sample")
    months = sorted(int(m) for m in frames["post_data"]["base_ym"].unique())

    # 구(舊) 스키마 원장 모사: 전체 권고 컬럼은 갖되 처치 4컬럼만 없는 상태(처치 레이어 이전 적재)
    res0 = run_from_frames(_truncate_frames(frames, months[9]), base_ym=months[9], render=False)
    old = lg.build_log_frame(res0, run_ts="t0").drop(columns=lg.TREATMENT_COLS)
    n0 = len(old)
    con = duckdb_connect(db)
    try:
        upsert(con, lg.LEDGER_TABLE, old, keys=lg.LEDGER_KEYS)
    finally:
        con.close()

    res = run_from_frames(_truncate_frames(frames, months[10]), base_ym=months[10], render=False)
    n = lg.append_recommendations(res, db_path=db, run_ts="20260101T000000Z")   # 크래시 없이 누적
    led = lg.read_ledger(db)
    assert len(led) == n0 + n                                                    # 구행 + 신규 누적
    assert set(lg.TREATMENT_COLS) <= set(led.columns)                           # 처치 컬럼 진화됨


# ── 2. 식별 복원: (a) RCT 가 true_uplift 를 노이즈 내 복원 (다중 seed, 비-tautological) ─────────
# 허용오차 근거: n=200·2 arm 이라 r_post std≈0.067 → ITT SE≈0.067·√(2/100)≈0.0095. per-seed 0.03≈3·SE,
# 다중 seed 평균은 더 타이트(0.02). 단일 seed 산술재현이 아니라 5 seed 전반의 복원을 검증한다.

def test_rct_recovers_true_uplift_across_seeds() -> None:
    ests = []
    for seed in range(5):
        sc = generate_treatment_scenario(seed=seed, assignment="rct")
        row = lg.score_ledger_uplift(sc["ledger_df"], sc["post_data"]).iloc[0]
        assert row["metric_kind"] == "rct"                      # holdout 존재 → 가장 깨끗한 식별
        assert row["n_treated"] >= 3 and row["n_control"] >= 3
        assert abs(row["uplift"] - sc["true_uplift"]) < 0.03    # per-seed: ≈3·SE 내 복원
        ests.append(row["uplift"])
    assert abs(float(np.mean(ests)) - 0.08) < 0.02             # 5-seed 평균은 더 타이트하게 복원


def test_rct_zero_effect_negative_control() -> None:
    """true_uplift=0 이면 RCT 추정도 ≈0 — 추정기가 없는 효과를 만들어내지 않음(non-fabrication)."""
    ests = [lg.score_ledger_uplift(
                generate_treatment_scenario(seed=s, assignment="rct", true_uplift=0.0)["ledger_df"],
                generate_treatment_scenario(seed=s, assignment="rct", true_uplift=0.0)["post_data"]
            ).iloc[0]["uplift"] for s in range(5)]
    assert all(abs(e) < 0.03 for e in ests)                    # 효과 0 → 추정 0 부근


# ── 3. 식별 복원: (b) 선택편향 하에서 raw 단순차이=편향, DiD·매칭=편향제거(다중 seed 대비) ──────────
# 허용오차 근거: DiD SE≈0.006 → per-seed did_err<0.025(≈4·SE). 핵심 강건 주장은 did_err<raw_err·
# matched_err<raw_err(편향 감소)이며 5 seed 전반에서 성립함을 확인(단일 seed 운에 의존하지 않음).

def test_observational_did_and_matching_remove_selection_bias() -> None:
    true = 0.08
    did_errs, matched_errs, raw_errs = [], [], []
    for seed in range(5):
        sc = generate_treatment_scenario(seed=seed, assignment="observational")
        row = lg.score_ledger_uplift(sc["ledger_df"], sc["post_data"]).iloc[0]
        assert row["metric_kind"] == "observational" and row["method"] == "did"
        raw_err, did_err, matched_err = (abs(row[k] - true)
                                         for k in ("uplift_raw", "uplift_did", "uplift_matched"))
        # raw 단순차이는 선택편향(번영 혼입)으로 상향 편향, DiD·매칭은 raw 보다 true 에 가깝다(편향 감소)
        assert row["uplift_raw"] > true + 0.02                  # raw 상향 편향(번영 혼입)
        assert did_err < raw_err and matched_err < raw_err      # 편향 감소(수치 대비)
        assert did_err < 0.025                                  # DiD 가 번영 drift 소거 → true 복원
        assert row["metric_kind"] != "prosperity_proxy"
        did_errs.append(did_err)
        matched_errs.append(matched_err)
        raw_errs.append(raw_err)
    # 5-seed 평균: raw 편향(≫) ≫ DiD·매칭 편향
    assert np.mean(raw_errs) > 0.05
    assert np.mean(did_errs) < 0.015 and np.mean(matched_errs) < 0.02


def test_observational_uplift_se_matches_reported_estimate() -> None:
    """uplift_se 는 *보고된 primary(DiD)* 의 SE 여야 한다(raw SE 를 빌려쓰지 않음 — 통계 추론 정합)."""
    sc = generate_treatment_scenario(seed=0, assignment="observational")
    row = lg.score_ledger_uplift(sc["ledger_df"], sc["post_data"]).iloc[0]
    # DiD 의 차분 SE 는 raw 2-표본 SE 보다 작다(번영 분산 차분 소거). 둘이 *다름* = 추정량별 SE 분리됨.
    assert np.isfinite(row["uplift_se"]) and np.isfinite(row["uplift_raw_se"])
    assert row["uplift_se"] < row["uplift_raw_se"]


def test_did_biased_under_parallel_trends_violation() -> None:
    """평행추세 가정이 깨지면(처치군 pre-trend 발산) DiD 가 편향 — 식별가정이 load-bearing 임을 검증.

    pretrend_coef=0.05 면 DiD 는 true_uplift(0.08)에서 −0.05 만큼 편향(≈0.03)되어야 한다. 즉 DiD 의
    복원은 합성 DGP 의 우연이 아니라 *평행추세 가정에 실제로 의존*함을 보인다(tautology 반증).
    """
    for seed in range(5):
        sc = generate_treatment_scenario(seed=seed, assignment="observational", pretrend_coef=0.05)
        row = lg.score_ledger_uplift(sc["ledger_df"], sc["post_data"]).iloc[0]
        assert abs(row["uplift_did"] - 0.08) > 0.03            # 가정 위반 → DiD 가 true 에서 크게 이탈
        assert row["uplift_did"] < 0.06                        # −pretrend_coef 방향(하향)으로 편향


# ── 4. 폴백 정직성: 처치 전무 → metric_kind='prosperity_proxy' + ★경고 ───────────────────────

def test_fallback_is_flagged_and_warns_when_no_treatment() -> None:
    sc = generate_treatment_scenario(seed=0, assignment="none")
    with pytest.warns(UserWarning, match="번영 프록시"):
        res = lg.score_ledger_uplift(sc["ledger_df"], sc["post_data"])
    row = res.iloc[0]
    assert row["metric_kind"] == "prosperity_proxy"             # 하류가 오인 못 하게 플래그
    assert np.isnan(row["uplift"])                              # uplift 아님(번영 프록시)
    assert np.isfinite(row["raw_return"])                       # 폴백은 raw 실현수익만 보고
    summ = lg.summarize_uplift(res)
    assert summ["is_prosperity_proxy"] is True
    assert summ["metric_kind"] == "prosperity_proxy"


def test_raw_ledger_scoring_never_labeled_uplift() -> None:
    """처치 없는 raw 채점(deprecated score_ledger)은 uplift 컬럼을 만들지 않는다(라벨 오용 방지)."""
    frames = _load_sample("data/sample")
    months = sorted(int(m) for m in frames["post_data"]["base_ym"].unique())
    res = run_from_frames(_truncate_frames(frames, months[10]), base_ym=months[10], render=False)
    led = lg.build_log_frame(res, run_ts="20260101T000000Z")
    raw = lg.score_ledger(led, frames["post_data"])
    assert not raw.empty
    assert "uplift" not in raw.columns                          # raw 채점에 'uplift' 라벨 금지
    assert {"ret_bl", "ret_market"} <= set(raw.columns)         # 번영 채점은 raw 수익 지표만


# ── 5. update_treatment 로 사후 처치정보를 채우면 score_ledger_uplift 가 (a)/(b)로 분기 ─────────

def _persist_ledger(db: str, ledger_df: pd.DataFrame) -> None:
    """시나리오 ledger_df 를 원장 테이블에 적재(권고 substrate 와 동일 키·멱등)."""
    con = duckdb_connect(db)
    try:
        upsert(con, lg.LEDGER_TABLE, ledger_df, keys=lg.LEDGER_KEYS)
    finally:
        con.close()


def test_update_treatment_roundtrip_and_branches_to_observational(tmp_path) -> None:
    sc = generate_treatment_scenario(seed=0, assignment="none")     # 처치 전무로 적재
    led_df = sc["ledger_df"]
    db = str(tmp_path / "led.duckdb")
    _persist_ledger(db, led_df)

    # 적재 직후엔 처치정보 전무 → 번영 프록시 분기
    with pytest.warns(UserWarning):
        before = lg.score_ledger_uplift(lg.read_ledger(db), sc["post_data"])
    assert (before["metric_kind"] == "prosperity_proxy").all()

    # 영업 발생 후 일부 행에 사후 처치정보를 채운다(treated 1/0 각 ≥3) → 관측처치 분기
    corps = led_df["corp_code"].tolist()
    base_ym = int(led_df["base_ym"].iloc[0])
    rt = str(led_df["run_ts"].iloc[0])
    for cc in corps[:5]:
        nrows = lg.update_treatment(db, {"run_ts": rt, "base_ym": base_ym, "corp_code": cc},
                                    treated=1, treat_ym=base_ym, treat_channel="price")
        assert nrows == 1                                            # 같은 행 1건 갱신(왕복)
    for cc in corps[5:10]:
        lg.update_treatment(db, {"run_ts": rt, "base_ym": base_ym, "corp_code": cc}, treated=0)

    led = lg.read_ledger(db)
    assert pd.to_numeric(led["treated"], errors="coerce").notna().sum() == 10   # 갱신 왕복 확인
    after = lg.score_ledger_uplift(led, sc["post_data"])
    assert (after["metric_kind"] == "observational").all()          # null→observational 로 분기 전환


def test_update_treatment_holdout_branches_to_rct(tmp_path) -> None:
    sc = generate_treatment_scenario(seed=0, assignment="none")
    led_df = sc["ledger_df"]
    db = str(tmp_path / "led.duckdb")
    _persist_ledger(db, led_df)

    corps = led_df["corp_code"].tolist()
    base_ym = int(led_df["base_ym"].iloc[0])
    rt = str(led_df["run_ts"].iloc[0])
    # 무작위 보류 파일럿: 일부는 처치 arm(holdout_flag=0), 일부는 보류 arm(holdout_flag=1)
    for cc in corps[:5]:
        lg.update_treatment(db, {"run_ts": rt, "base_ym": base_ym, "corp_code": cc}, holdout_flag=0)
    for cc in corps[5:10]:
        lg.update_treatment(db, {"run_ts": rt, "base_ym": base_ym, "corp_code": cc}, holdout_flag=1)

    after = lg.score_ledger_uplift(lg.read_ledger(db), sc["post_data"])
    assert (after["metric_kind"] == "rct").all()                    # holdout 존재 → RCT 최강식별


def test_update_treatment_missing_key_raises(tmp_path) -> None:
    db = str(tmp_path / "led.duckdb")
    with pytest.raises(ValueError, match="키 누락"):
        lg.update_treatment(db, {"run_ts": "x", "base_ym": 202506}, treated=1)   # corp_code 누락
