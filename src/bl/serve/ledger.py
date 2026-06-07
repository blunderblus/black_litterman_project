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

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from bl.common.io import _q, duckdb_connect, upsert
from bl.common.logging import get_logger
from bl.features.builder import LABEL_HORIZON

log = get_logger(__name__)

LEDGER_TABLE = "recommendation_log"
LEDGER_KEYS = ["run_ts", "base_ym", "corp_code"]
# 원장에 보존할 권고 필드(후일 실현수익 조인·재캘리브레이션 입력). mart 에 있는 것만 적재.
# q/omega(E3a): 뷰 블록스택(법인당 K뷰) 하에서도 **법인당 스칼라**를 유지하기 위해, 블록스택과 동일
# 사후를 내는 *결합 단일뷰 등가*(q_eff/omega_eff)를 로깅한다(per-view 보조테이블 대신 결합 스칼라 선택,
# additive·역방향 호환 — primary 원장의 target_weight/bl_return 은 불변). per-view 분해가 필요하면
# inputs metadata(view_names·q_scale·c_cal·view_corr)에서 별도 보조테이블로 확장(E3b).
_LOG_COLS = [
    "corp_code", "corp_name", "tier", "current_bal", "current_weight",
    "market_weight", "target_weight", "weight_diff", "bl_return",
    "q", "omega", "marketing_score", "action_guide",
]
# 처치(treatment) 레이어 — 권고 시점엔 보통 null(아직 영업 전), 영업 발생 후 update_treatment 로 채운다.
#   treated       : 영업 실제 수행 0/1/null(미기록)
#   treat_ym      : 영업 시점(YYYYMM) 또는 null
#   treat_channel : 영업 채널(아래 TREAT_CHANNELS) 또는 null
#   holdout_flag  : 무작위 보류군 표식 0/1/null (RCT 식별용; 1=보류=control, 0=처치 arm)
TREATMENT_COLS = ["treated", "treat_ym", "treat_channel", "holdout_flag"]
TREAT_CHANNELS = ("price", "relationship", "bundle")     # 영업 채널 3종(메모리: 영업 3종)


def build_log_frame(result: dict, run_ts: str) -> pd.DataFrame:
    """파이프라인 result(mart+meta) → 원장 적재용 DataFrame(run_ts/base_ym/source + null 처치 레이어)."""
    mart = result["mart"]
    meta = result.get("meta", {})
    cols = [c for c in _LOG_COLS if c in mart.columns]
    out = mart[cols].copy()
    out.insert(0, "run_ts", str(run_ts))
    out.insert(1, "base_ym", int(meta.get("base_ym", 0)))
    out.insert(2, "source", str(meta.get("source", "")))
    out["corp_code"] = out["corp_code"].astype(str)
    # 처치 레이어는 권고 시점엔 null(영업 전). 후속 update_treatment 가 같은 행을 갱신한다.
    out["treated"] = np.nan
    out["treat_ym"] = np.nan
    out["treat_channel"] = None
    out["holdout_flag"] = np.nan
    return out


def _ensure_treatment_columns(con, table: str) -> None:
    """기존 원장 테이블에 처치 컬럼이 없으면 ADD COLUMN 으로 채운다(스키마 additive 진화).

    처치 레이어 도입 이전에 적재된 원장(시간축 누적이 목적이라 실배포엔 사전 테이블이 존재)에 그대로
    append 하면 upsert 의 '신규 컬럼=스키마 진화 미지원' 가드에 걸려 크래시한다. 누락 처치 컬럼만
    선제적으로 추가해 누적 append 를 깨지 않는다(VARCHAR·NULL — 최초 적재 전부-null 과 동일 타입).
    """
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?", [table]
    ).fetchall()
    if not rows:
        return                                       # 테이블 없음 → upsert 가 df 스키마(처치 포함)로 생성
    existing = {r[0] for r in rows}
    for c in TREATMENT_COLS:
        if c not in existing:
            con.execute(f"ALTER TABLE {_q(table)} ADD COLUMN {_q(c)} VARCHAR")


def append_recommendations(
    result: dict, *, db_path: str | Path, run_ts: str, table: str = LEDGER_TABLE
) -> int:
    """result 의 권고를 원장 테이블에 멱등 적재(키: run_ts,base_ym,corp_code). 적재 행수 반환."""
    df = build_log_frame(result, run_ts)
    con = duckdb_connect(db_path)
    try:
        _ensure_treatment_columns(con, table)        # 처치 레이어 이전 원장도 깨지 않게 additive 진화
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
        return con.execute(f"SELECT * FROM {_q(table)}").fetchdf()  # 식별자 인용(io._q, 직접 보간 금지)
    finally:
        con.close()


def update_treatment(
    db_path: str | Path,
    keys: dict,
    *,
    treated: int | float | None = None,
    treat_ym: int | None = None,
    treat_channel: str | None = None,
    holdout_flag: int | float | None = None,
    table: str = LEDGER_TABLE,
) -> int:
    """원장 권고행(키: run_ts,base_ym,corp_code)에 사후 처치정보를 채운다(영업 발생 후 갱신).

    권고는 보통 처치 null 로 적재되고, 영업이 일어난 뒤 본 helper 로 같은 행을 update 한다.
    None 인 필드는 건드리지 않는다(부분 갱신). 갱신된 행 수를 반환(키 미존재면 0).

    원장 저장 시 처치 컬럼은 최초 적재가 전부 null 이라 DuckDB 가 VARCHAR 로 만든다 — 숫자 파라미터를
    넣어도 문자열로 저장되며, score_ledger_uplift 가 읽을 때 to_numeric 로 복원한다(왕복 안전).
    """
    keys = dict(keys)
    missing = [k for k in LEDGER_KEYS if k not in keys]
    if missing:
        raise ValueError(f"update_treatment 키 누락 {missing} (필요: {LEDGER_KEYS})")
    updates = {"treated": treated, "treat_ym": treat_ym,
               "treat_channel": treat_channel, "holdout_flag": holdout_flag}
    updates = {k: v for k, v in updates.items() if v is not None}
    if not updates:
        return 0
    con = duckdb_connect(db_path)
    try:
        exists = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchall()
        if not exists:
            return 0
        set_clause = ", ".join(f"{_q(c)} = ?" for c in updates)
        cond = " AND ".join(f"{_q(k)} = ?" for k in LEDGER_KEYS)
        key_vals = [keys[k] for k in LEDGER_KEYS]
        con.execute(f'UPDATE {_q(table)} SET {set_clause} WHERE {cond}',
                    list(updates.values()) + key_vals)
        cnt = con.execute(f'SELECT count(*) FROM {_q(table)} WHERE {cond}', key_vals).fetchone()
        n = int(cnt[0]) if cnt else 0
    finally:
        con.close()
    log.info(f"처치정보 갱신 {n}행", extra={"stage": "serve.ledger.update_treatment", "fields": list(updates)})
    return n


def score_ledger(
    ledger_df: pd.DataFrame, post_data: pd.DataFrame, *, horizon: int = LABEL_HORIZON,
    top_k_frac: float = 0.2,
) -> pd.DataFrame:
    """[DEPRECATED — score_ledger_uplift 로 진화] 원장 권고를 *raw 실현 잔액수익*으로 채점.

    ★주의: 본 함수가 채점하는 realized_forward_returns 는 "그 후 잔액이 얼마나 늘었나"(=번영)이며
    영업 효과(uplift)가 아니다. 따라서 이 채점으로 캘리브레이션하면 C1 실패모드(가만둬도 클 법인 선호)를
    *강화*한다. 처치 vs 대조 uplift 로 채점하려면 score_ledger_uplift 를 사용하라(번영 프록시 탈출).

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


# ─────────────────────────────────────────────────────────────────────────────
# 처치 레이어 채점 — raw 실현수익(번영)에서 *처치군 vs 대조군 uplift*(영업효과)로 진화
#
# 핵심 통찰: 영업 자원이 한정돼 추천 법인을 다 영업할 수 없다(= BL 을 쓰는 바로 그 이유)는 사실이
# "추천받았으나 영업 못 한 법인 = 자연 대조군"을 자동 생성한다. 시스템 운영 자체가 uplift 식별용
# 데이터를 만든다. score_ledger_uplift 는 처치정보 수준에 따라 식별 방법을 자동 분기한다(아래).
# ─────────────────────────────────────────────────────────────────────────────

UPLIFT_COVARIATES = ("current_bal", "market_weight", "current_weight",
                     "weight_diff", "bl_return", "marketing_score")
# 식별 분기 후 결과행이 항상 갖는 컬럼(분기별 미산출 항목은 NaN). 하류가 오인하지 않도록 metric_kind 고정.
# uplift_se 는 *보고된 primary 추정치(uplift)의* SE(추정기와 정합). uplift_raw_se 는 raw 차이의 SE(대비용).
_UPLIFT_FIELDS = ("metric_kind", "n", "n_treated", "n_control", "uplift", "uplift_se",
                  "uplift_raw", "uplift_raw_se", "uplift_did", "uplift_matched", "raw_return", "method")


def _coerce_treatment(df: pd.DataFrame) -> pd.DataFrame:
    """처치 숫자 컬럼(treated/holdout_flag/treat_ym)을 to_numeric 로 복원(원장 VARCHAR 저장 대응)."""
    out = df.copy()
    for c in ("treated", "holdout_flag", "treat_ym"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _uplift_row(**kw: object) -> dict:
    """분기별 결과를 균일 스키마로 채운 행(미산출은 NaN)."""
    base: dict = {f: (np.nan if f not in ("metric_kind", "method") else "") for f in _UPLIFT_FIELDS}
    base["n"] = base["n_treated"] = base["n_control"] = 0
    base.update(kw)
    return base


def _two_sample_diff(rt: np.ndarray, rc: np.ndarray) -> tuple[float, float]:
    """평균차(처치-대조)와 풀링 표준오차(SE). 한쪽 표본<2 면 SE=NaN."""
    rt = np.asarray(rt, dtype="float64")
    rc = np.asarray(rc, dtype="float64")
    diff = float(rt.mean() - rc.mean())
    se = (float(np.sqrt(rt.var(ddof=1) / len(rt) + rc.var(ddof=1) / len(rc)))
          if len(rt) > 1 and len(rc) > 1 else float("nan"))
    return diff, se


def _covariate_matrix(m: pd.DataFrame, covariates: tuple[str, ...]) -> np.ndarray | None:
    """매칭용 공변량 행렬(완전관측·비퇴화 컬럼만; current_bal 은 log). 사용 가능 컬럼 없으면 None.

    주의: q/omega 의 *내부 차원*에 의존하지 않는다(E3a 가 그 차원을 바꿀 수 있음). 안정적 권고 메타만 쓴다.
    """
    cols: list[np.ndarray] = []
    for c in covariates:
        if c not in m.columns:
            continue
        v = pd.to_numeric(m[c], errors="coerce").to_numpy(dtype="float64")
        if not np.isfinite(v).all() or float(np.std(v)) < 1e-12:
            continue
        if c == "current_bal":
            v = np.log(np.clip(v, 1.0, None))
        cols.append(v)
    return np.column_stack(cols) if cols else None


def _matching_uplift(treated: np.ndarray, r: np.ndarray, x: np.ndarray | None) -> float:
    """propensity NN 매칭 ATT(선택편향=관측공변량 가정). 공변량/적합 실패 시 NaN.

    e(x)=P(처치|x) 를 로지스틱으로 추정 → 각 처치군을 propensity 가 가장 가까운 대조군에 매칭(복원추출)
    → ATT = mean(r_처치 − r_매칭대조). 관측공변량으로 선택편향이 설명되면 raw 차이의 편향을 제거한다.
    """
    if x is None:
        log.debug("매칭 생략 — 사용 가능한 공변량 없음(전부 결측/퇴화)", extra={"stage": "serve.ledger.uplift"})
        return float("nan")
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    t = np.asarray(treated, dtype=bool)
    if t.sum() < 1 or (~t).sum() < 1:
        return float("nan")
    xs = StandardScaler().fit_transform(np.asarray(x, dtype="float64"))
    try:
        ps = LogisticRegression(max_iter=1000).fit(xs, t.astype(int)).predict_proba(xs)[:, 1]
    except Exception as e:  # noqa: BLE001 — 완전분리 등 적합 실패 시 매칭 추정 포기(NaN)
        log.debug(f"매칭 propensity 적합 실패 → NaN: {e}", extra={"stage": "serve.ledger.uplift"})
        return float("nan")
    r = np.asarray(r, dtype="float64")
    ps_t, ps_c, r_t, r_c = ps[t], ps[~t], r[t], r[~t]
    idx = np.abs(ps_t[:, None] - ps_c[None, :]).argmin(axis=1)   # 최근접 propensity 대조 매칭
    return float(np.mean(r_t - r_c[idx]))


def _prep_uplift_window(grp: pd.DataFrame, r_post: pd.Series, r_pre: pd.Series) -> pd.DataFrame | None:
    """한 윈도우 권고행 + 실현 전방수익(r_post) + 직전 수익(r_pre, DiD용)을 corp 기준 결합."""
    m = _coerce_treatment(grp)
    m["corp_code"] = m["corp_code"].astype(str)
    m = m.merge(r_post.rename("r_post"), left_on="corp_code", right_index=True, how="inner")
    m = m[np.isfinite(m["r_post"].to_numpy(dtype="float64"))]
    if m.empty:
        return None
    if r_pre is not None and not r_pre.empty:
        m = m.merge(r_pre.rename("r_pre"), left_on="corp_code", right_index=True, how="left")
    else:
        m["r_pre"] = np.nan
    return m


def _score_uplift_window(m: pd.DataFrame, *, covariates: tuple[str, ...], min_group: int) -> dict:
    """결합 윈도우 → uplift 식별 dict(3분기). 처치정보 수준에 따라 RCT/관측/번영프록시 자동 선택."""
    n = int(len(m))
    hf = m["holdout_flag"] if "holdout_flag" in m.columns else pd.Series(np.nan, index=m.index)
    tr = m["treated"] if "treated" in m.columns else pd.Series(np.nan, index=m.index)

    # (a) RCT — 무작위 보류군이 있으면 가장 깨끗한 식별(ITT: 보류 arm 대비 처치 arm 의 실현수익 차)
    if hf.notna().any():
        arm_t = m[hf == 0]          # holdout_flag=0 → 처치 arm(영업 수행)
        arm_c = m[hf == 1]          # holdout_flag=1 → 보류 arm(control)
        if len(arm_t) >= min_group and len(arm_c) >= min_group:
            up, se = _two_sample_diff(arm_t["r_post"].to_numpy(), arm_c["r_post"].to_numpy())
            # RCT 는 uplift=raw(ITT) 동일 추정량이므로 SE 도 동일(uplift_se=uplift_raw_se).
            return _uplift_row(metric_kind="rct", n=n, n_treated=len(arm_t), n_control=len(arm_c),
                               uplift=up, uplift_se=se, uplift_raw=up, uplift_raw_se=se,
                               method="rct_itt")

    # (b) 관측 처치(무작위 아님) — 선택편향 주의. DiD(법인 고정효과=차분) + propensity 매칭으로 편향 제거.
    if tr.notna().any():
        treated_mask = tr == 1
        control_mask = tr == 0
        if int(treated_mask.sum()) >= min_group and int(control_mask.sum()) >= min_group:
            r_post = m["r_post"].to_numpy(dtype="float64")
            up_raw, raw_se = _two_sample_diff(r_post[treated_mask.to_numpy()], r_post[control_mask.to_numpy()])
            # DiD: 각 법인의 (post − pre) 로 법인별 baseline drift(번영)를 차분 소거 → 처치효과만 남김.
            # SE 도 차분량의 2-표본 SE 로 산출(보고 추정량과 정합 — raw SE 를 빌려 쓰지 않음).
            did = did_se = float("nan")
            pre_ok = m[np.isfinite(m["r_pre"].to_numpy(dtype="float64"))]
            td = pre_ok[pre_ok["treated"] == 1]
            cd = pre_ok[pre_ok["treated"] == 0]
            if len(td) >= min_group and len(cd) >= min_group:
                d_t = (td["r_post"] - td["r_pre"]).to_numpy(dtype="float64")
                d_c = (cd["r_post"] - cd["r_pre"]).to_numpy(dtype="float64")
                did, did_se = _two_sample_diff(d_t, d_c)
            # propensity 매칭(관측공변량으로 선택편향 설명). 매칭 SE 는 부트스트랩 필요 → 범위 밖(NaN).
            mo = m[tr.isin([0, 1])]
            x = _covariate_matrix(mo, covariates)
            matched = _matching_uplift((mo["treated"] == 1).to_numpy(), mo["r_post"].to_numpy(), x)
            # primary(uplift) 는 *편향보정 추정치*(DiD→매칭 순)만 채운다 — 보정 불가 시 NaN(번영 혼입
            # raw 차이를 'uplift' 로 라벨 금지). raw 단순차이는 uplift_raw 로만 대비 보고한다.
            primary = next((v for v in (did, matched) if np.isfinite(v)), float("nan"))
            method = ("did" if np.isfinite(did) else "matched" if np.isfinite(matched)
                      else "unadjusted")
            # uplift_se 는 보고된 primary 와 정합: DiD→did_se, 매칭→NaN(부트스트랩 미구현), 미보정→NaN.
            se = did_se if method == "did" else float("nan")
            return _uplift_row(metric_kind="observational", n=n,
                               n_treated=int(treated_mask.sum()), n_control=int(control_mask.sum()),
                               uplift=primary, uplift_se=se, uplift_raw=up_raw, uplift_raw_se=raw_se,
                               uplift_did=did, uplift_matched=matched, method=method)

    # (c) 처치정보 전무/부족 → raw 실현수익 폴백. ★uplift 아님 — 번영 프록시(C1 실패모드 강화).
    return _uplift_row(metric_kind="prosperity_proxy", n=n,
                       n_treated=int((tr == 1).sum()), n_control=int((tr == 0).sum()),
                       raw_return=float(np.mean(m["r_post"].to_numpy(dtype="float64"))),
                       method="raw_realized")


def score_ledger_uplift(
    ledger_df: pd.DataFrame, post_data: pd.DataFrame, *, horizon: int = LABEL_HORIZON,
    covariates: tuple[str, ...] = UPLIFT_COVARIATES, min_group: int = 3,
) -> pd.DataFrame:
    """원장 권고를 *처치군 vs 대조군 uplift*(영업효과)로 채점 — raw 실현수익(번영) 채점의 진화.

    처치정보 수준에 따라 식별 방법을 (run_ts,base_ym) 윈도우별로 자동 분기한다(설계 §9, ADR-0004):
      (a) holdout_flag 존재  → RCT: 보류 arm 대비 처치 arm 의 실현수익 차 = uplift (가장 깨끗한 식별).
      (b) treated 존재(무작위X) → 관측 처치: DiD(법인 고정효과 차분) + propensity 매칭으로 uplift 추정.
           raw 단순차이(uplift_raw)는 선택편향을 포함하므로 함께 보고해 대비한다(절대 'uplift' 라벨 금지).
      (c) 처치정보 전무/부족 → raw 실현수익 폴백. metric_kind='prosperity_proxy' 를 박고 ★경고를 낸다:
           "uplift 아님 — 번영 프록시. 이 채점은 C1 실패모드(가만둬도 클 법인 선호)를 강화한다."

    출력은 (run_ts,base_ym)별 행 DataFrame(균일 스키마). eval.calibrate 가 소비할 수 있는 형식이며,
    향후 Ω/축가중을 raw 수익이 아니라 uplift 로 캘리브레이션(E3b)하도록 연결한다(자동 연결은 범위 밖).
    """
    from bl.common.dates import ym_add
    from bl.eval.backtest import realized_forward_returns

    if ledger_df.empty:
        return pd.DataFrame()
    rows: list[dict] = []
    saw_proxy = False
    saw_unidentified = False                              # 관측처치 있으나 보정 불가(uplift=NaN)
    for (run_ts, base_ym), grp in ledger_df.groupby(["run_ts", "base_ym"]):
        future_ym = ym_add(int(base_ym), horizon)
        r_post = realized_forward_returns(post_data, int(base_ym), future_ym)
        if r_post.empty:
            continue                                      # 아직 실현 전(미래) → 채점 보류
        r_pre = realized_forward_returns(post_data, ym_add(int(base_ym), -horizon), int(base_ym))
        m = _prep_uplift_window(grp, r_post, r_pre)
        if m is None or len(m) < 2:
            continue
        sc = _score_uplift_window(m, covariates=covariates, min_group=min_group)
        sc.update({"run_ts": run_ts, "base_ym": int(base_ym), "future_ym": future_ym})
        saw_proxy = saw_proxy or sc["metric_kind"] == "prosperity_proxy"
        saw_unidentified = saw_unidentified or sc["method"] == "unadjusted"
        rows.append(sc)

    if saw_proxy:
        msg = ("score_ledger_uplift: 처치정보 없음/부족 → raw 실현수익 폴백(metric_kind='prosperity_proxy'). "
               "★이는 uplift 가 아니라 *번영 프록시*다 — 영업효과가 아닌 자연증가를 채점하므로 "
               "C1 실패모드(가만둬도 클 법인 선호)를 강화한다. treated/holdout_flag 를 update_treatment 로 "
               "채워 (a)RCT / (b)관측처치(DiD·매칭) 식별로 전환하라.")
        warnings.warn(msg, UserWarning, stacklevel=2)
        log.warning(msg, extra={"stage": "serve.ledger.uplift"})
    if saw_unidentified:
        # 관측 처치는 있으나 편향보정 불가(pre 없음 + 공변량 퇴화) → uplift=NaN 으로 정직 보고하되,
        # raw 차이(uplift_raw)는 선택편향을 포함하므로 silent 하지 않게 경고한다(method='unadjusted').
        umsg = ("score_ledger_uplift: 관측 처치 윈도우 일부가 편향보정 불가(pre 부재+공변량 퇴화) → "
                "uplift=NaN(metric_kind='observational', method='unadjusted'). uplift_raw 는 선택편향을 "
                "포함하므로 uplift 로 쓰지 말 것. pre 패널 또는 매칭 공변량을 확보해 DiD/매칭을 복원하라.")
        warnings.warn(umsg, UserWarning, stacklevel=2)
        log.warning(umsg, extra={"stage": "serve.ledger.uplift"})
    return pd.DataFrame(rows)


def summarize_uplift(per_window: pd.DataFrame) -> dict:
    """윈도우별 uplift 채점 → 집계 dict. metric_kind/번영프록시 플래그를 보존해 하류 오인을 차단한다."""
    if per_window.empty:
        return {"n_windows": 0, "metric_kind": "none", "is_prosperity_proxy": False,
                "note": "유효 채점 윈도우 없음"}
    d = per_window
    kinds = set(d["metric_kind"].astype(str))
    is_proxy = "prosperity_proxy" in kinds
    kind = ("prosperity_proxy" if is_proxy
            else "rct" if kinds == {"rct"}
            else "observational" if "observational" in kinds
            else sorted(kinds)[0])

    def _mean(col: str) -> float:
        return float(d[col].mean(skipna=True)) if col in d.columns else float("nan")

    return {
        "n_windows": int(len(d)),
        "metric_kind": kind,
        "is_prosperity_proxy": bool(is_proxy),
        "mean_uplift": _mean("uplift"),
        "mean_uplift_raw": _mean("uplift_raw"),
        "mean_uplift_did": _mean("uplift_did"),
        "mean_uplift_matched": _mean("uplift_matched"),
        "mean_raw_return": _mean("raw_return"),
    }
