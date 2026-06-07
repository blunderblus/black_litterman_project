"""피처/라벨 — **DuckDB SQL**(window 함수) 기반 시점분리 피처 생성 → train_set/inference_set.

설계: docs/design/02-data-pipeline.md(features), ADR-0002(DuckDB+Parquet), ADR-0004(누수 차단).
과거 노트북 07 대응. 핫패스(잔액 패널 수백만 행의 LAG/rolling/LEAD·ASOF 조인)는 DuckDB가
pandas 대비 압도적이므로 **window 함수 SQL**로 처리한다. sklearn/XGBoost 핸드오프 경계에서만
DataFrame으로 materialize한다.

핵심 교정 유지: 미래(bal_future_3m)는 라벨에만(피처 제외, look-ahead 차단). 재무는 공시지연(lag)
반영 point-in-time(ASOF base_ym >= 공시가용월). 추론셋엔 라벨/미래 없음.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    import duckdb

    from bl.common.config import Settings

CHURN_DROP = 0.5
GROWTH_RISE = 1.2
LABEL_HORIZON = 3
FIN_DISCLOSURE_LAG = 3   # 사업보고서 공시지연(개월): fin_ym + lag 이후부터 사용 가능

FEATURE_COLS = [
    "bal", "bal_lag1", "bal_lag3", "bal_ma3", "bal_ma6", "bal_std6", "bal_mom", "volatility_6m",
    "trx_in", "trx_out", "trx_activity", "payroll_yn", "main_bank_yn",
    "revenue", "net_income", "total_assets", "total_equity", "cash_amount", "debt_ratio",
    "base_rate", "ktb3y", "bsi_mfg",
    "has_financial", "is_listed",
]
FORBIDDEN_FEATURE_COLS = {"bal_future_3m", "label_churn", "label_growth"}
_META = ["corp_code", "base_ym", "TIER", "sector_code", "group"]

# 공시 가용월(YYYYMM) = base_ym + FIN_DISCLOSURE_LAG 개월 (정수 연월 산술)
_AVAIL_YM = (
    "(((CAST(base_ym/100 AS INT)*12 + (base_ym%100) - 1 + {lag})/12)*100 "
    "+ ((CAST(base_ym/100 AS INT)*12 + (base_ym%100) - 1 + {lag})%12) + 1)"
).format(lag=FIN_DISCLOSURE_LAG)


def _run_features(con: "duckdb.DuckDBPyConnection") -> pd.DataFrame:
    """con 의 테이블(post_data/financial_wide/macro/target_master)로 피처 행렬 SQL 실행."""
    con.execute("""
        CREATE OR REPLACE TEMP VIEW _macro_wide AS
        SELECT base_ym,
          max(CASE WHEN metric_code='BASE_RATE' THEN value END) AS base_rate,
          max(CASE WHEN metric_code='KTB3Y'    THEN value END) AS ktb3y,
          max(CASE WHEN metric_code='BSI_MFG'  THEN value END) AS bsi_mfg
        FROM macro GROUP BY base_ym
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW _fin AS
        WITH d AS (
          SELECT *, row_number() OVER (PARTITION BY corp_code ORDER BY base_ym DESC) AS rn
          FROM financial_wide
        )
        SELECT corp_code, revenue, net_income, total_assets, total_equity, cash_amount,
          total_liabilities / NULLIF(total_assets,0) AS debt_ratio,
          {_AVAIL_YM} AS avail_ym
        FROM d WHERE rn = 1
    """)
    sql = f"""
    WITH bf AS (
      SELECT corp_code, base_ym, bal, payroll_yn, main_bank_yn,
        LAG(bal,1) OVER w AS bal_lag1,
        LAG(bal,3) OVER w AS bal_lag3,
        AVG(bal) OVER (PARTITION BY corp_code ORDER BY base_ym ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS bal_ma3,
        AVG(bal) OVER (PARTITION BY corp_code ORDER BY base_ym ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) AS bal_ma6,
        stddev_samp(bal) OVER (PARTITION BY corp_code ORDER BY base_ym ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) AS bal_std6,
        LEAD(bal,{LABEL_HORIZON}) OVER w AS bal_future_3m,
        CAST(trx_cnt_in_6m AS DOUBLE) AS trx_in,
        CAST(trx_cnt_out_6m AS DOUBLE) AS trx_out
      FROM post_data
      WINDOW w AS (PARTITION BY corp_code ORDER BY base_ym)
    ),
    j AS (
      SELECT bf.*,
        bf.bal / NULLIF(bf.bal_lag3,0) - 1 AS bal_mom,
        bf.bal_std6 / NULLIF(bf.bal_ma6,0) AS volatility_6m,
        bf.trx_in + bf.trx_out AS trx_activity,
        f.revenue, f.net_income, f.total_assets, f.total_equity, f.cash_amount, f.debt_ratio,
        mw.base_rate, mw.ktb3y, mw.bsi_mfg,
        t.TIER, t.sector_code,
        CASE WHEN t.stock_code IS NOT NULL THEN 1 ELSE 0 END AS is_listed
      FROM bf
      ASOF LEFT JOIN _fin f ON bf.corp_code = f.corp_code AND bf.base_ym >= f.avail_ym
      LEFT JOIN _macro_wide mw ON bf.base_ym = mw.base_ym
      LEFT JOIN target_master t ON bf.corp_code = t.corp_code
    )
    SELECT *,
      CASE WHEN revenue IS NOT NULL THEN 1 ELSE 0 END AS has_financial,
      CASE WHEN bal_future_3m IS NULL THEN NULL
           WHEN bal_future_3m < bal*{CHURN_DROP} THEN 1 ELSE 0 END AS label_churn,
      CASE WHEN bal_future_3m IS NULL THEN NULL
           WHEN bal_future_3m > bal*{GROWTH_RISE} THEN 1 ELSE 0 END AS label_growth,
      CASE WHEN revenue IS NOT NULL THEN 'A' ELSE 'B' END AS "group"
    FROM j
    """
    return con.execute(sql).fetchdf()


def _split(df: pd.DataFrame, base_ym: int) -> dict[str, pd.DataFrame]:
    """피처 행렬 → {train(라벨 유효), inference(base_ym, 라벨 없음)}. 누수 가드 포함."""
    leaked = FORBIDDEN_FEATURE_COLS & set(FEATURE_COLS)
    if leaked:
        raise AssertionError(f"누수: 미래/라벨 컬럼이 FEATURE_COLS에 포함됨 {leaked}")
    feat = [c for c in FEATURE_COLS if c in df.columns]
    train = df[df["bal_future_3m"].notna()][_META + feat + ["label_churn", "label_growth"]]
    inference = df[df["base_ym"] == base_ym][_META + feat]
    return {"train": train.reset_index(drop=True), "inference": inference.reset_index(drop=True)}


def build_features_from_frames(
    post_data: pd.DataFrame,
    financial_wide: pd.DataFrame,
    macro: pd.DataFrame,
    target_master: pd.DataFrame,
    base_ym: int,
) -> dict[str, pd.DataFrame]:
    """프레임 입력 → DuckDB(in-memory) 등록 후 SQL 피처 생성(순수 함수, 오프라인 검증용)."""
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        con.register("post_data", post_data)
        con.register("financial_wide", financial_wide)
        con.register("macro", macro)
        con.register("target_master", target_master)
        df = _run_features(con)
    finally:
        con.close()
    return _split(df, base_ym)


def build_features(con: "duckdb.DuckDBPyConnection", settings: "Settings", base_ym: int) -> dict:
    """DuckDB 테이블 → 피처 SQL 직접 실행(round-trip 없이 대용량 핫패스를 DuckDB가 처리)."""
    return _split(_run_features(con), base_ym)
