"""serve.ledger — 권고 append 원장(시간축 누적 audit trail) + 라이브 채점 테스트.

round-2 가 ABSENT 로 지적한 '권고 로깅 substrate' 의 회귀 가드.
"""

from __future__ import annotations

from bl.eval.backtest import _truncate_frames
from bl.pipeline import _load_sample, run_from_frames
from bl.serve import ledger as lg


def _result_at(frames: dict, base_ym: int) -> dict:
    return run_from_frames(_truncate_frames(frames, base_ym), base_ym=base_ym, render=False)


def test_append_and_read_ledger_accumulates(tmp_path) -> None:
    frames = _load_sample("data/sample")
    months = sorted(int(m) for m in frames["post_data"]["base_ym"].unique())
    db = str(tmp_path / "led.duckdb")

    r1 = _result_at(frames, months[10])
    n1 = lg.append_recommendations(r1, db_path=db, run_ts="20260101T000000Z")
    assert n1 > 0
    led = lg.read_ledger(db)
    assert len(led) == n1
    assert {"run_ts", "base_ym", "corp_code", "target_weight", "market_weight"} <= set(led.columns)

    # 다른 run_ts → 시간축 누적(overwrite 가 아님)
    r2 = _result_at(frames, months[11])
    lg.append_recommendations(r2, db_path=db, run_ts="20260201T000000Z")
    led2 = lg.read_ledger(db)
    assert len(led2) == n1 + len(r2["mart"])
    assert set(led2["run_ts"].unique()) == {"20260101T000000Z", "20260201T000000Z"}


def test_append_idempotent_same_run_ts(tmp_path) -> None:
    frames = _load_sample("data/sample")
    months = sorted(int(m) for m in frames["post_data"]["base_ym"].unique())
    db = str(tmp_path / "led.duckdb")
    r = _result_at(frames, months[10])
    lg.append_recommendations(r, db_path=db, run_ts="20260101T000000Z")
    lg.append_recommendations(r, db_path=db, run_ts="20260101T000000Z")  # 동일 run_ts 재실행
    assert len(lg.read_ledger(db)) == len(r["mart"])                     # 두 배 안 됨(멱등)


def test_read_ledger_missing_db_returns_empty(tmp_path) -> None:
    assert lg.read_ledger(str(tmp_path / "nope.duckdb")).empty


def test_score_ledger_against_realized(tmp_path) -> None:
    frames = _load_sample("data/sample")
    months = sorted(int(m) for m in frames["post_data"]["base_ym"].unique())
    db = str(tmp_path / "led.duckdb")
    base = months[10]                                                    # horizon 뒤 실현 잔액 존재
    lg.append_recommendations(_result_at(frames, base), db_path=db, run_ts="20260101T000000Z")

    scored = lg.score_ledger(lg.read_ledger(db), frames["post_data"])
    assert len(scored) == 1                                              # 발행→실현 채점 1건
    assert {"ret_bl", "ret_market", "ic", "future_ym"} <= set(scored.columns)
    assert int(scored["future_ym"].iloc[0]) > base                      # 채점은 미래 실현 기준
