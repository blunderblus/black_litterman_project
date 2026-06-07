"""파이프라인 오케스트레이터 — 데모(합성/오프라인) & 실데이터(키) 공통 다운스트림.

데모 경로(키 불필요): data/sample/* → features → models(XGBoost·IForest) → BL 입력 →
사후수익·최적화 → 마트(§8 출력변환) → 대시보드 JSON/HTML(docs/).
실데이터 경로: ingest(키)로 동일 테이블을 채우면 같은 다운스트림이 그대로 동작한다.

설계: docs/design/ (전체). 본 모듈은 '딱 나오도록' 묶는 글루 + 자산/수익률 패널 조립.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from bl.common.io import read_parquet
from bl.common.logging import get_logger
from bl.engine import inputs as bi
from bl.engine import optimize as opt
from bl.features.builder import build_features_from_frames
from bl.models import anomaly as anom
from bl.models import growth_churn as gc
from bl.serve import dashboard_data
from bl.serve import mart as mart_mod

log = get_logger(__name__)

DEFAULT_BASE_YM = 202510
NONFIN_WALLET_MULT = 3.0     # 비재무 고객 wallet_size = 현재잔액 × (섹터 배수 대용)


def _load_sample(sample_dir: str | Path) -> dict[str, pd.DataFrame]:
    d = Path(sample_dir)
    names = ["target_master", "post_data", "financial_wide", "company_sentiment", "macro"]
    return {n: read_parquet(d / f"{n}.parquet") for n in names}


def _returns_panel(
    post: pd.DataFrame, corp_order: list[str], base_ym: int
) -> tuple[np.ndarray, list[str]]:
    """post_data → 자산별 잔액 log-return 패널(T×N) + 유효 corp 리스트(완전관측 열만).

    견고성: corp_code dtype 정규화(int↔str), 0/음수 잔액→NaN, 부분관측/미매칭 열 제거
    (corp_order 와 동기). 비유한 열이 BL 공분산 계산에서 크래시하지 않도록 사전 차단.
    """
    wide = (
        post[post["base_ym"] <= base_ym]
        .pivot_table(index="base_ym", columns="corp_code", values="bal", aggfunc="last")
        .sort_index()
    )
    wide.columns = wide.columns.astype(str)              # dtype 정규화(int corp_code 대응)
    order = [str(c) for c in corp_order]
    wide = wide.reindex(columns=order)
    arr = wide.to_numpy(dtype="float64")
    arr = np.where(arr > 0, arr, np.nan)                 # 0/음수 잔액 → NaN(log 보호)
    panel = np.diff(np.log(arr), axis=0)                 # (T-1, N) log-return
    valid = np.isfinite(panel).all(axis=0) if panel.size else np.zeros(len(order), bool)
    valid_corps = [c for c, v in zip(order, valid, strict=True) if v]
    return panel[:, valid], valid_corps


def _view_scaler(assets: pd.DataFrame) -> dict:
    """뷰 3축 raw 신호의 (mean,std) — build_views.axis_raw 와 동일 정의(단면 표준화 명시화).

    배치 z-score 폴백 경로(추론 누수 경고)를 제거하고, 단면 표준화를 의도된 스케일러로 주입한다.
    anomaly 는 뷰 축이 아니라 Ω 신뢰도 변조 요인이므로 여기(뷰 스케일러)에 포함하지 않는다.
    """
    n = len(assets)

    def col(name: str) -> np.ndarray:
        return assets[name].fillna(0).to_numpy("float64") if name in assets.columns else np.zeros(n)

    axes = {
        "news": col("gemini_score"),
        "pattern": col("prob_growth_raw") - col("prob_churn_raw"),
        "relationship": col("relationship_score"),
    }
    out: dict = {}
    for k, v in axes.items():
        sd = float(np.std(v))
        out[k] = (float(np.mean(v)), sd if sd > 1e-12 else 1.0)
    return out


def _assemble_assets(frames: dict, ml: pd.DataFrame, an: pd.DataFrame, base_ym: int) -> pd.DataFrame:
    """추론 시점 자산 메타 + 모델 신호 + 감성 + 관계 신호를 corp_code 기준으로 결합."""
    tm = frames["target_master"]
    post = frames["post_data"]
    fin = frames["financial_wide"]
    sent = frames["company_sentiment"]

    cur = post[post["base_ym"] == base_ym].copy()    # 추론 시점 스냅샷
    a = cur.merge(tm, on="corp_code", how="left")
    a = a.merge(ml, on="corp_code", how="left")
    a = a.merge(an, on="corp_code", how="left")
    a = a.merge(
        sent.rename(columns={"sentiment_score": "gemini_score", "confidence": "gemini_confidence"}),
        on="corp_code", how="left",
    )
    # 다년도 재무는 corp별 최신 1행으로 dedup 후 결합(중복 행 폭증 방지)
    fin_cash = (fin.sort_values("base_ym").groupby("corp_code", as_index=False).last()
                [["corp_code", "cash_amount"]] if not fin.empty
                else fin.assign(cash_amount=pd.NA)[["corp_code", "cash_amount"]])
    a = a.merge(fin_cash, on="corp_code", how="left")

    a["current_bal"] = a["bal"].astype("float64")
    a["has_financial"] = a["cash_amount"].notna().astype(int)
    a["has_news"] = a["gemini_score"].notna().astype(int)
    a["is_listed"] = a["stock_code"].notna().astype(int)
    a["trx_in"] = a["trx_cnt_in_6m"].astype("float64")
    a["trx_out"] = a["trx_cnt_out_6m"].astype("float64")
    ta = a["trx_in"] + a["trx_out"]
    a["trx_activity"] = ta / ta.max() if ta.max() > 0 else 0.0
    a["relationship_score"] = (
        0.5 * a["main_bank_yn"] + 0.3 * a["payroll_yn"] + 0.2 * a["trx_activity"]
    )
    # wallet_size(=w_mkt 앵커): 재무보유=cash_amount, 비재무=현재잔액×배수(섹터 대용)
    a["wallet_size"] = np.where(
        a["cash_amount"].notna(), a["cash_amount"], a["current_bal"] * NONFIN_WALLET_MULT
    )
    a["w_mkt"] = a["wallet_size"]
    a["w_current"] = a["current_bal"]
    a["gemini_score"] = a["gemini_score"].fillna(0.0)
    return a


def run_demo(
    sample_dir: str | Path = "data/sample",
    out_dir: str | Path = "site",
    base_ym: int = DEFAULT_BASE_YM,
    top_n: int = 200,
    seed: int = 42,
    render: bool = True,
) -> dict:
    """합성 데모: sample → 전 파이프라인 → 대시보드(site/). 키 불필요."""
    return _pipeline_from_frames(_load_sample(sample_dir), out_dir=out_dir, base_ym=base_ym,
                                 top_n=top_n, seed=seed, render=render, source="synthetic-demo")


def run_from_frames(
    frames: dict, *, base_ym: int = DEFAULT_BASE_YM, seed: int = 42,
    render: bool = False, source: str = "frames",
    tau: float | None = None, axis_weights: dict | None = None, omega_scale: float = 1.0,
    gamma_anom: float | None = None, lambda_fixed: float | None = None,
    ledger_path: str | None = None, run_ts: str | None = None,
) -> dict:
    """프레임(데모/실데이터/백테스트 공통) → 전체 BL 파이프라인 1회 실행(공개 진입점).

    렌더 없이(기본) mart 만 반환하므로 백테스트·오프라인 평가가 시점별로 반복 호출한다.
    tau/axis_weights/omega_scale/gamma_anom/lambda_fixed 은 **정책 손잡이**(보수↔공격) override —
    None 이면 모듈 기본 보수값 사용, eval.calibrate 가 실현지표로 역산. lambda_fixed = 앵커 Π 스케일
    정규화 상수(None→engine.inputs.LAMBDA_FIXED); τ↑·λ_fix↓·γ_anom↓ 가 공격 방향(설계 §5.5, REPORT).
    ledger_path 지정 시 권고를 append 원장에 적재한다(serve.ledger, 묶임줄 발행 기록).
    """
    result = _pipeline_from_frames(frames, base_ym=base_ym, seed=seed, render=render, source=source,
                                   tau=tau, axis_weights=axis_weights, omega_scale=omega_scale,
                                   gamma_anom=gamma_anom, lambda_fixed=lambda_fixed)
    if ledger_path:
        _log_to_ledger(result, ledger_path, run_ts)
    return result


def _log_to_ledger(result: dict, ledger_path: str, run_ts: str | None) -> None:
    """권고를 append 원장에 적재(run_ts 미지정 시 UTC 타임스탬프 생성)."""
    from datetime import UTC, datetime

    from bl.serve.ledger import append_recommendations

    ts = run_ts or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    append_recommendations(result, db_path=ledger_path, run_ts=ts)


def _pipeline_from_frames(frames, *, out_dir="site", base_ym=DEFAULT_BASE_YM, top_n=200,
                          seed=42, render=True, source="live",
                          tau=None, axis_weights=None, omega_scale=1.0, gamma_anom=None,
                          lambda_fixed=None) -> dict:
    """프레임(데모/실데이터 공통) → features→models→BL→마트→대시보드. 동일 다운스트림."""
    # features → models
    feat = build_features_from_frames(
        frames["post_data"], frames["financial_wide"], frames["macro"],
        frames["target_master"], base_ym,
    )
    gc_models = gc.train(feat["train"], seed=seed)
    ml = gc.predict(gc_models, feat["inference"])
    an_model = anom.train(feat["train"], seed=seed)
    an = anom.score(an_model, feat["inference"])

    # 자산 조립(추론 시점) + 수익률 패널
    assets = _assemble_assets(frames, ml, an, base_ym)
    assets = assets.dropna(subset=["current_bal"]).reset_index(drop=True)
    corp_order = assets["corp_code"].astype("string").tolist()
    panel, valid = _returns_panel(frames["post_data"], corp_order, base_ym)
    if len(valid) < len(corp_order):                     # 패널 비유효 자산 제거 + assets 동기
        log.warning(f"수익률 패널 비유효 {len(corp_order) - len(valid)}개 자산 제외",
                    extra={"stage": "pipeline", "kept": len(valid)})
        assets = (assets.set_index(assets["corp_code"].astype("string"))
                  .loc[valid].reset_index(drop=True))

    # BL 입력 → 사후수익 → 최적화 (뷰 스케일러 명시 주입: 배치 z-score 폴백 경로 제거)
    scaler = _view_scaler(assets)
    bl_kwargs: dict = {"axis_weights": axis_weights, "omega_scale": omega_scale,
                       "gamma_anom": gamma_anom, "risk_aversion": lambda_fixed}  # λ_fix override
    if tau is not None:
        bl_kwargs["tau"] = tau
    inp = bi.assemble_bl_inputs(assets, panel, scaler=scaler, **bl_kwargs)
    er = opt.posterior_expected_return(inp)
    sigma_post = opt.posterior_covariance(inp)
    w = opt.optimize_weights(er, sigma_post, w_max=0.10)

    # 마트(§3.2.3 컬럼) + §8 출력변환
    n = len(assets)
    mart = pd.DataFrame({
        "corp_code": assets["corp_code"].astype("string"),
        "corp_name": assets["TARGET_NAME"],
        "tier": assets["TIER"],
        # tier_class: funding_gap 계수용 운영등급(score 독립). T1→PRIME/T2→CORE/T3→WATCH
        "tier_class": assets["TIER"].map({"T1": "PRIME", "T2": "CORE", "T3": "WATCH"}).fillna("WATCH"),
        "sector_code": assets["sector_code"],
        "region": assets["region"],
        "current_bal": assets["current_bal"],
        "bl_return": er,
        "current_weight": inp["w_current"],
        "market_weight": inp["w_mkt"],
        "target_weight": w,
        "prob_growth_raw": assets.get("prob_growth_raw", pd.Series(np.full(n, np.nan))),
        "prob_churn_raw": assets.get("prob_churn_raw", pd.Series(np.full(n, np.nan))),
        "anomaly_score_raw": assets.get("anomaly_score_raw", pd.Series(np.full(n, np.nan))),
        "news_sentiment": assets["gemini_score"],
        "pi": inp["pi"],
        "q": inp["Q"],
        "omega": np.diag(inp["Omega"]),
    })
    mart = mart_mod.compute_marketing_outputs(
        mart, total_aum=float(np.nansum(assets["current_bal"]))
    )

    meta = {
        "base_ym": base_ym, "n_assets": n, "lambda": round(float(inp["lambda"]), 4),
        "tau": inp["tau"], "source": source,
        "model_confidence": {k: round(v["confidence"], 4) for k, v in gc_models.items()},
    }
    result = {"mart": mart, "inputs_meta": inp["metadata"], "meta": meta}

    if render:
        from bl.serve.dashboard_html import write_index

        json_path = dashboard_data.export_dashboard_json(mart, out_dir, top_n=top_n, metadata=meta)
        html_path = write_index(out_dir)
        result["json_path"] = json_path
        result["html_path"] = html_path
        log.info(
            "demo dashboard 생성", extra={"stage": "pipeline.run_demo", "json": json_path, "html": html_path},
        )
    return result


def load_frames(settings, sample_dir: str | Path = "data/sample", raw_dir: str | Path = "data/raw",
                base_ym: int = DEFAULT_BASE_YM) -> dict:
    """키가 있으면 ingest로 실데이터 프레임을, 없으면 합성 sample을 반환(동일 다운스트림).

    내부 소스(target_master/post_data)는 raw_dir(접근통제), 외부(재무/매크로/뉴스)는 공식 API.
    개별 수집 실패는 sample 로 graceful 대체(부분 키로도 대시보드 생성).
    """
    has_dart = settings.dart_api_key is not None
    if settings.env == "demo" or not has_dart:
        log.info("ingest 키 없음/demo 모드 → 합성 sample 사용", extra={"stage": "pipeline.load_frames"})
        return _load_sample(sample_dir)

    from bl.common.io import read_parquet
    from bl.enrich import sentiment as enr
    from bl.ingest import financial as ing_fin
    from bl.ingest import macro as ing_mac
    from bl.ingest import news as ing_news

    raw = Path(raw_dir)
    tm = read_parquet(raw / "target_master.parquet")    # 내부 유니버스(필수)
    post = read_parquet(raw / "post_data.parquet")       # 내부 예금 패널(필수)
    real = tm[~tm["IS_VIRTUAL"].fillna(False)]
    corp_codes = real["corp_code"].astype(str).tolist()
    targets = list(zip(corp_codes, real["TARGET_NAME"].astype(str), strict=False))
    months = sorted(int(m) for m in post["base_ym"].unique())
    years = sorted({m // 100 for m in months})

    def _try(fn, name):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            log.warning(f"{name} 수집 실패 → sample 대체: {e}", extra={"stage": "pipeline.load_frames"})
            return None

    fin = _try(lambda: ing_fin.collect_financial(settings, corp_codes, years), "financial")
    mac = _try(lambda: ing_mac.collect_macro(settings, months[0], months[-1]), "macro")
    news = _try(lambda: ing_news.collect_news(settings, targets), "news")
    # base_ym 시점 컷오프로 추론월 이후 뉴스 유입 차단(누수 방지, enrich.sentiment._cutoff_news)
    sent = (enr.score_sentiment(news, settings, base_ym=base_ym)
            if news is not None and len(news) else None)

    sample = _load_sample(sample_dir) if (fin is None or mac is None or sent is None) else None

    def _pick(val, key):
        if val is not None:
            return val
        assert sample is not None  # 위 조건상 결측이 있으면 sample 로드 보장(타입 내로잉)
        return sample[key]

    return {
        "target_master": tm, "post_data": post,
        "financial_wide": _pick(fin, "financial_wide"),
        "macro": _pick(mac, "macro"),
        "company_sentiment": _pick(sent, "company_sentiment"),
    }


def run(
    settings=None, *, out_dir: str | Path = "site", raw_dir: str | Path = "data/raw",
    sample_dir: str | Path = "data/sample", base_ym: int = DEFAULT_BASE_YM,
    top_n: int = 200, seed: int = 42, render: bool = True,
    ledger_path: str | None = None, run_ts: str | None = None,
) -> dict:
    """실데이터(키)/데모(무키) 통합 실행 — **API 키만 채우면 동일 파이프라인이 실데이터로 동작**.

    ledger_path 지정 시 이번 실행 권고를 append 원장에 적재한다(serve.ledger, 묶임줄 발행 기록).
    """
    from bl.common.config import get_settings

    s = settings or get_settings()
    frames = load_frames(s, sample_dir=sample_dir, raw_dir=raw_dir, base_ym=base_ym)
    src = "synthetic-demo" if (s.env == "demo" or s.dart_api_key is None) else "live"
    result = _pipeline_from_frames(frames, out_dir=out_dir, base_ym=base_ym, top_n=top_n,
                                   seed=seed, render=render, source=src)
    if ledger_path:
        _log_to_ledger(result, ledger_path, run_ts)
    return result


if __name__ == "__main__":
    r = run_demo()
    m = r["mart"]
    print(f"assets={len(m)} | active_leads(>=80)={(m['marketing_score'] >= 80).sum()} | "
          f"λ={r['meta']['lambda']} | conf={r['meta']['model_confidence']}")
    print("top5:")
    print(m.sort_values("marketing_score", ascending=False)
          [["corp_name", "tier", "marketing_score", "action_guide", "bl_return", "weight_diff"]]
          .head(5).to_string(index=False))
