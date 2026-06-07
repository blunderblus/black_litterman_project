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


def _returns_panel(post: pd.DataFrame, corp_order: list[str], base_ym: int) -> np.ndarray:
    """post_data → 자산별 잔액 log-return 패널(T×N, base_ym 이하). corp_order 순으로 정렬."""
    wide = (
        post[post["base_ym"] <= base_ym]
        .pivot_table(index="base_ym", columns="corp_code", values="bal", aggfunc="last")
        .sort_index()
    )
    wide = wide.reindex(columns=corp_order)
    logret = np.log(wide.to_numpy(dtype="float64"))
    panel = np.diff(logret, axis=0)                  # (T-1, N) log-return
    return panel


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
    fin_cash = fin[["corp_code", "cash_amount"]]
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


def _pipeline_from_frames(frames, *, out_dir="site", base_ym=DEFAULT_BASE_YM, top_n=200,
                          seed=42, render=True, source="live") -> dict:
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
    panel = _returns_panel(frames["post_data"], corp_order, base_ym)

    # BL 입력 → 사후수익 → 최적화
    inp = bi.assemble_bl_inputs(assets, panel)
    er = opt.posterior_expected_return(inp)
    sigma_post = opt.posterior_covariance(inp)
    w = opt.optimize_weights(er, sigma_post, w_max=0.10)

    # 마트(§3.2.3 컬럼) + §8 출력변환
    n = len(assets)
    mart = pd.DataFrame({
        "corp_code": assets["corp_code"].astype("string"),
        "corp_name": assets["TARGET_NAME"],
        "tier": assets["TIER"],
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


def load_frames(settings, sample_dir: str | Path = "data/sample", raw_dir: str | Path = "data/raw") -> dict:
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
    sent = enr.score_sentiment(news, settings) if news is not None and len(news) else None

    sample = _load_sample(sample_dir) if (fin is None or mac is None or sent is None) else None
    return {
        "target_master": tm, "post_data": post,
        "financial_wide": fin if fin is not None else sample["financial_wide"],
        "macro": mac if mac is not None else sample["macro"],
        "company_sentiment": sent if sent is not None else sample["company_sentiment"],
    }


def run(
    settings=None, *, out_dir: str | Path = "site", raw_dir: str | Path = "data/raw",
    sample_dir: str | Path = "data/sample", base_ym: int = DEFAULT_BASE_YM,
    top_n: int = 200, seed: int = 42, render: bool = True,
) -> dict:
    """실데이터(키)/데모(무키) 통합 실행 — **API 키만 채우면 동일 파이프라인이 실데이터로 동작**."""
    from bl.common.config import get_settings

    s = settings or get_settings()
    frames = load_frames(s, sample_dir=sample_dir, raw_dir=raw_dir)
    src = "synthetic-demo" if (s.env == "demo" or s.dart_api_key is None) else "live"
    return _pipeline_from_frames(frames, out_dir=out_dir, base_ym=base_ym, top_n=top_n,
                                 seed=seed, render=render, source=src)


if __name__ == "__main__":
    r = run_demo()
    m = r["mart"]
    print(f"assets={len(m)} | active_leads(>=80)={(m['marketing_score'] >= 80).sum()} | "
          f"λ={r['meta']['lambda']} | conf={r['meta']['model_confidence']}")
    print("top5:")
    print(m.sort_values("marketing_score", ascending=False)
          [["corp_name", "tier", "marketing_score", "action_guide", "bl_return", "weight_diff"]]
          .head(5).to_string(index=False))
