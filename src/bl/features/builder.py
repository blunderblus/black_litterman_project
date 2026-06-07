"""피처/라벨 — 시점 분리 시계열·재무·매크로 결합 → train_set/inference_set.

설계: docs/design/02-data-pipeline.md(features), ADR-0004(누수 차단). 과거 노트북 07 대응.
핵심 교정: **미래 잔액(bal_future_3m)은 라벨에만 쓰고 피처에서 제외**(look-ahead 차단).
inference_set 은 라벨/미래 컬럼 없이 base_ym 시점 피처만 포함.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from bl.common.dates import ym_add

if TYPE_CHECKING:
    import duckdb

    from bl.common.config import Settings

# 라벨 임계(설계 07): 3개월 후 잔액이 절반 미만→이탈, 1.2배 초과→성장.
CHURN_DROP = 0.5
GROWTH_RISE = 1.2
LABEL_HORIZON = 3
FIN_DISCLOSURE_LAG = 3   # 사업보고서 공시지연(개월): fin_ym 이후 이 개월부터 사용 가능(look-ahead 방지)

# 피처 컬럼(이 목록에 미래/라벨 컬럼은 절대 포함하지 않는다 — 누수 차단의 단일 소스).
FEATURE_COLS = [
    "bal", "bal_lag1", "bal_lag3", "bal_ma3", "bal_ma6", "bal_std6", "bal_mom", "volatility_6m",
    "trx_in", "trx_out", "trx_activity", "payroll_yn", "main_bank_yn",
    "revenue", "net_income", "total_assets", "total_equity", "cash_amount", "debt_ratio",
    "base_rate", "ktb3y", "bsi_mfg",
    "has_financial", "is_listed",
]
# 절대 피처가 되어선 안 되는 컬럼(정적 가드).
FORBIDDEN_FEATURE_COLS = {"bal_future_3m", "label_churn", "label_growth"}


def _balance_features(post: pd.DataFrame) -> pd.DataFrame:
    """월별 잔액 패널 → 시계열 피처 + (라벨용) 미래 잔액. corp_code 별 시점 정렬."""
    df = post.sort_values(["corp_code", "base_ym"]).copy()
    g = df.groupby("corp_code")["bal"]
    df["bal_lag1"] = g.shift(1)
    df["bal_lag3"] = g.shift(3)
    df["bal_ma3"] = g.transform(lambda s: s.rolling(3, min_periods=1).mean())
    df["bal_ma6"] = g.transform(lambda s: s.rolling(6, min_periods=1).mean())
    df["bal_std6"] = g.transform(lambda s: s.rolling(6, min_periods=2).std())
    df["bal_mom"] = df["bal"] / df["bal_lag3"] - 1.0
    df["volatility_6m"] = df["bal_std6"] / df["bal_ma6"]              # 변동계수(이탈위험 대용)
    # 라벨 전용 미래값(LEAD) — 피처로 새지 않게 별도 보관
    df["bal_future_3m"] = g.shift(-LABEL_HORIZON)
    df["trx_in"] = df["trx_cnt_in_6m"].astype("float64")
    df["trx_out"] = df["trx_cnt_out_6m"].astype("float64")
    df["trx_activity"] = df["trx_in"] + df["trx_out"]
    return df


def build_features_from_frames(
    post_data: pd.DataFrame,
    financial_wide: pd.DataFrame,
    macro: pd.DataFrame,
    target_master: pd.DataFrame,
    base_ym: int,
) -> dict[str, pd.DataFrame]:
    """프레임 입력으로 train_set/inference_set 을 구성한다(순수 함수, 오프라인 검증용).

    Returns: {'train': train_set(라벨 포함), 'inference': inference_set(base_ym, 라벨 없음)}.
    """
    df = _balance_features(post_data)

    # 재무(ASOF 단순화: corp별 최신 1행 broadcast) + has_financial
    fin_cols = ["revenue", "net_income", "total_assets", "total_equity", "cash_amount", "debt_ratio"]
    fin = financial_wide.copy()
    if not fin.empty:
        fin = fin.sort_values("base_ym").groupby("corp_code", as_index=False).last()
        fin["debt_ratio"] = fin["total_liabilities"] / fin["total_assets"].replace(0, np.nan)
        fin["_fin_avail_ym"] = fin["base_ym"].map(lambda y: ym_add(int(y), FIN_DISCLOSURE_LAG))
        df = df.merge(fin[["corp_code", "_fin_avail_ym", *fin_cols]], on="corp_code", how="left")
        # point-in-time: 공시 가용 시점 이전(base_ym < 공시가용월)에는 재무를 비운다(look-ahead 차단)
        not_avail = df["_fin_avail_ym"].notna() & (df["base_ym"] < df["_fin_avail_ym"])
        df.loc[not_avail, fin_cols] = np.nan
        df = df.drop(columns=["_fin_avail_ym"])
    df["has_financial"] = df["revenue"].notna().astype(int) if "revenue" in df.columns else 0

    # 매크로(base_ym 시점 정합)
    mac = macro.pivot_table(index="base_ym", columns="metric_code", values="value", aggfunc="last")
    mac = mac.rename(columns={"BASE_RATE": "base_rate", "KTB3Y": "ktb3y", "BSI_MFG": "bsi_mfg"})
    df = df.merge(mac.reset_index(), on="base_ym", how="left")

    # 메타(tier/sector/is_listed)
    tm = target_master[["corp_code", "TIER", "sector_code", "stock_code"]].copy()
    tm["is_listed"] = tm["stock_code"].notna().astype(int)
    df = df.merge(tm[["corp_code", "TIER", "sector_code", "is_listed"]], on="corp_code", how="left")

    # 라벨(미래 잔액 기준) — 미래가 있는 행만 라벨 유효
    fut = df["bal_future_3m"]
    df["label_churn"] = np.where(fut.notna(), (fut < df["bal"] * CHURN_DROP).astype(int), np.nan)
    df["label_growth"] = np.where(fut.notna(), (fut > df["bal"] * GROWTH_RISE).astype(int), np.nan)
    df["group"] = np.where(df["has_financial"] == 1, "A", "B")    # 2그룹(재무 유무)

    # 누수 가드: 피처 목록에 금지 컬럼이 섞이지 않았는지 정적 확인
    leaked = FORBIDDEN_FEATURE_COLS & set(FEATURE_COLS)
    if leaked:
        raise AssertionError(f"누수: 미래/라벨 컬럼이 FEATURE_COLS에 포함됨 {leaked}")

    feat_present = [c for c in FEATURE_COLS if c in df.columns]
    meta = ["corp_code", "base_ym", "TIER", "sector_code", "group"]

    # train: 라벨이 유효한(미래 존재) 행만. inference: base_ym 시점(라벨 없음, 미래 컬럼 제외).
    train = df[df["bal_future_3m"].notna()][meta + feat_present + ["label_churn", "label_growth"]].copy()
    inference = df[df["base_ym"] == base_ym][meta + feat_present].copy()
    return {"train": train.reset_index(drop=True), "inference": inference.reset_index(drop=True)}


def build_features(con: "duckdb.DuckDBPyConnection", settings: "Settings") -> "pd.DataFrame":
    """DuckDB 결합 래퍼 — RAW/post_data 테이블에서 읽어 build_features_from_frames 호출.

    ingest 연동 후 구현(현재는 build_features_from_frames(프레임) 사용).
    """
    raise NotImplementedError(
        "ingest 연동 후 구현 — 현재는 build_features_from_frames(post_data, financial_wide, macro, "
        "target_master, base_ym) 사용"
    )
