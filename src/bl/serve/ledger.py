"""권고 로깅 substrate — 매 실행의 BL 권고를 DuckDB append 원장(ledger)에 누적한다.

round-2 검증이 ABSENT 로 확정한 조각: 기존 파이프라인은 site/data.json 을 매번 덮어써(휘발)
과거 권고가 누적되지 않았다(run_id/timestamp 없음). 이 원장은 (run_ts, base_ym, corp_code) 키로
멱등 적재하되 run_ts 가 실행마다 달라 **시간축으로 누적**된다 — implied-vol 식 묶임줄의 '발행 기록'.

루프(설계 §9.1·§9.3, ADR-0004 §5):
  append_recommendations(매 실행) → 후일 score_ledger(post_data 의 실현 잔액과 조인, 뷰별 실현
  성과 채점) → 그 실현오차로 Ω/축가중 재캘리브레이션(eval.calibrate). 백테스트(eval.backtest)가
  과거 데이터로 한 일을, 원장은 발행→실현 시차를 두고 **라이브**로 수행한다.

upsert(멱등 DELETE-then-INSERT, io.py) 재사용: 같은 run_ts 재실행은 idempotent, 다른 run_ts 는
누적. overwrite-on-key 가 아니라 run_ts 를 키에 포함해 audit trail 을 보존한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from bl.common.io import duckdb_connect, upsert
from bl.common.logging import get_logger
from bl.features.builder import LABEL_HORIZON

log = get_logger(__name__)

LEDGER_TABLE = "recommendation_log"
LEDGER_KEYS = ["run_ts", "base_ym", "corp_code"]
# 원장에 보존할 권고 필드(후일 실현수익 조인·재캘리브레이션 입력). mart 에 있는 것만 적재.
_LOG_COLS = [
    "corp_code", "corp_name", "tier", "current_bal", "current_weight",
    "market_weight", "target_weight", "weight_diff", "bl_return",
    "q", "omega", "marketing_score", "action_guide",
]


def build_log_frame(result: dict, run_ts: str) -> pd.DataFrame:
    """파이프라인 result(mart+meta) → 원장 적재용 DataFrame(run_ts/base_ym/source 부착)."""
    mart = result["mart"]
    meta = result.get("meta", {})
    cols = [c for c in _LOG_COLS if c in mart.columns]
    out = mart[cols].copy()
    out.insert(0, "run_ts", str(run_ts))
    out.insert(1, "base_ym", int(meta.get("base_ym", 0)))
    out.insert(2, "source", str(meta.get("source", "")))
    out["corp_code"] = out["corp_code"].astype(str)
    return out


def append_recommendations(
    result: dict, *, db_path: str | Path, run_ts: str, table: str = LEDGER_TABLE
) -> int:
    """result 의 권고를 원장 테이블에 멱등 적재(키: run_ts,base_ym,corp_code). 적재 행수 반환."""
    df = build_log_frame(result, run_ts)
    con = duckdb_connect(db_path)
    try:
        n = upsert(con, table, df, keys=LEDGER_KEYS)
    finally:
        con.close()
    log.info(f"권고 원장 적재 {n}행", extra={"stage": "serve.ledger", "table": table, "run_ts": run_ts})
    return n


def read_ledger(db_path: str | Path, table: str = LEDGER_TABLE) -> pd.DataFrame:
    """원장 전체를 DataFrame 으로 읽는다(파일/테이블 없으면 빈 DataFrame)."""
    if str(db_path) != ":memory:" and not Path(db_path).exists():
        return pd.DataFrame()
    con = duckdb_connect(db_path, read_only=True)
    try:
        exists = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchall()
        if not exists:
            return pd.DataFrame()
        return con.execute(f'SELECT * FROM "{table}"').fetchdf()
    finally:
        con.close()


def score_ledger(
    ledger_df: pd.DataFrame, post_data: pd.DataFrame, *, horizon: int = LABEL_HORIZON,
    top_k_frac: float = 0.2,
) -> pd.DataFrame:
    """원장의 과거 권고를 현재 실현 잔액으로 채점(라이브 묶임줄). (run_ts,base_ym)별 지표 반환.

    base_ym 의 horizon 개월 뒤 실현 잔액이 post_data 에 존재하는 그룹만 채점한다(미래분 자동 제외).
    지표 정의는 eval.backtest._score_window 와 동일(ret_bl/ret_market/ic/prec_*).
    """
    from bl.common.dates import ym_add
    from bl.eval.backtest import _score_window, realized_forward_returns

    if ledger_df.empty:
        return pd.DataFrame()
    rows: list[dict] = []
    for (run_ts, base_ym), grp in ledger_df.groupby(["run_ts", "base_ym"]):
        future_ym = ym_add(int(base_ym), horizon)
        realized = realized_forward_returns(post_data, int(base_ym), future_ym)
        if realized.empty:
            continue                                      # 아직 실현 전(미래) → 채점 보류
        sc = _score_window(grp, realized, top_k_frac=top_k_frac)
        if sc is None:
            continue
        sc.update({"run_ts": run_ts, "base_ym": int(base_ym), "future_ym": future_ym})
        rows.append(sc)
    return pd.DataFrame(rows)
