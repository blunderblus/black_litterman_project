"""합성 데모 데이터 생성기 — 설계 스키마 기반 소형 샘플을 data/sample/ 에 출력.

산출(parquet, 모두 합성·PII 없음):
- target_master.parquet     : 유니버스(corp_code, TARGET_NAME, biz_reg_no, jurir_no, stock_code,
                              IS_VIRTUAL, TIER, sector_code, region)
- post_data.parquet         : 월별 예금 패널(corp_code, base_ym, bal, trx_cnt_in_6m,
                              trx_cnt_out_6m, payroll_yn, main_bank_yn)  ← 내부 거래/잔액
- financial_wide.parquet    : 재무 요약(corp_code, base_ym, revenue, operating_profit,
                              net_income, total_assets, total_liabilities, total_equity, cash_amount)
- company_sentiment.parquet : 뉴스 감성(corp_code, sentiment_score[-1,1], confidence[0,1], event_cnt)
- macro.parquet             : 매크로(metric_code, base_ym, value)

결정적(seed 고정). 크기는 수십 KB 수준으로 깃헙 업로드 가능.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from bl.common.dates import ym_add
from bl.common.io import write_parquet

# 기준월(YYYYMM) — 24개월, 202510 종료(프로젝트 base_ym=202510)
BASE_YM_END = 202510
N_MONTHS = 24
N_OPERATING = 36          # 실제 운영 법인(잔액 패널 보유)
N_VIRTUAL = 4             # 가상 섹터 노드(T3)
SECTORS = ["64121", "47919", "41221", "23322", "27200", "64992"]
REGIONS = ["서울", "경기", "부산", "인천", "대구", "광주"]


def _ym_sequence(end_ym: int, n: int) -> list[int]:
    """end_ym 에서 거꾸로 n개월 YYYYMM 시퀀스(오름차순)."""
    y, m = divmod(end_ym, 100)
    out: list[int] = []
    for _ in range(n):
        out.append(y * 100 + m)
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return sorted(out)


def _digits(rng: np.random.Generator, width: int) -> str:
    return "".join(str(d) for d in rng.integers(0, 10, width))


def generate_demo(
    out_dir: str | Path = "data/sample",
    seed: int = 42,
    n_operating: int = N_OPERATING,
    n_virtual: int = N_VIRTUAL,
    n_months: int = N_MONTHS,
) -> dict[str, Path]:
    """합성 데모 데이터를 생성해 parquet 5종으로 저장한다. {name: path} 반환."""
    rng = np.random.default_rng(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    months = _ym_sequence(BASE_YM_END, n_months)

    # ---- 유니버스(target_master) ----
    rows = []
    corp_ids = [f"{i:08d}" for i in range(1, n_operating + 1)]
    n_listed = n_operating // 3                          # 약 1/3 상장(T1)
    for i, cc in enumerate(corp_ids):
        listed = i < n_listed
        rows.append({
            "corp_code": cc,
            "TARGET_NAME": f"데모법인{i + 1:02d}",
            "biz_reg_no": _digits(rng, 10),
            "jurir_no": _digits(rng, 13),
            "stock_code": f"{rng.integers(1, 999999):06d}" if listed else None,
            "IS_VIRTUAL": False,
            "TIER": "T1" if listed else "T2",
            "sector_code": SECTORS[i % len(SECTORS)],
            "region": REGIONS[i % len(REGIONS)],
        })
    for j in range(n_virtual):                            # 가상 섹터 노드(T3)
        rows.append({
            "corp_code": f"SECTOR_{SECTORS[j % len(SECTORS)]}",
            "TARGET_NAME": f"가상섹터_{SECTORS[j % len(SECTORS)]}",
            "biz_reg_no": None, "jurir_no": None, "stock_code": None,
            "IS_VIRTUAL": True, "TIER": "T3",
            "sector_code": SECTORS[j % len(SECTORS)], "region": None,
        })
    target_master = pd.DataFrame(rows)

    # ---- 월별 예금 패널(post_data) ----
    panel_rows = []
    # 자산별 시작잔액(로그정규)·추세(성장/이탈 섞기)·변동성
    start_bal = rng.lognormal(mean=np.log(5_000_000), sigma=1.2, size=n_operating)
    trend = rng.normal(0.005, 0.05, n_operating)          # 월 추세(성장/감소)
    # 풍부한 데모를 위해 강한 이탈/성장 군 주입(이탈→Defend, 성장→Aggressive Buy)
    decl = rng.choice(n_operating, size=6, replace=False)
    trend[decl] = rng.uniform(-0.25, -0.12, 6)            # 일부는 3개월 -50%↓(churn=1)
    rest = np.array([i for i in range(n_operating) if i not in decl])
    grow = rng.choice(rest, size=6, replace=False)
    trend[grow] = rng.uniform(0.08, 0.18, 6)
    vol = rng.uniform(0.02, 0.12, n_operating)
    payroll = rng.integers(0, 2, n_operating)
    mainbank = (rng.uniform(0, 1, n_operating) < 0.25).astype(int)
    for i, cc in enumerate(corp_ids):
        bal = start_bal[i]
        for ym in months:
            shock = rng.normal(trend[i], vol[i])
            bal = max(bal * (1.0 + shock), 1_000.0)        # 0 잔액 방지(최소 1천원)
            tin = max(int(rng.normal(8, 4)), 0)
            tout = max(int(rng.normal(7, 4)), 0)
            panel_rows.append({
                "corp_code": cc, "base_ym": ym, "bal": round(bal, 0),
                "trx_cnt_in_6m": tin, "trx_cnt_out_6m": tout,
                "payroll_yn": int(payroll[i]), "main_bank_yn": int(mainbank[i]),
            })
    post_data = pd.DataFrame(panel_rows)

    # ---- 재무(financial_wide) — has_financial 부분집합 ----
    has_fin_idx = sorted(rng.choice(n_operating, size=int(n_operating * 0.72), replace=False))
    fin_rows = []
    fin_ym = (BASE_YM_END // 100) * 100 + 12 - 100        # 직전 사업연도말(예: 202412)
    for i in has_fin_idx:
        rev = float(rng.lognormal(np.log(2e10), 1.0))
        ta = rev * rng.uniform(0.8, 2.5)
        eq = ta * rng.uniform(0.2, 0.6)
        ni = rev * rng.uniform(-0.05, 0.12)
        fin_rows.append({
            "corp_code": corp_ids[i], "base_ym": fin_ym,
            "revenue": round(rev), "operating_profit": round(ni * 1.3),
            "net_income": round(ni), "total_assets": round(ta),
            "total_liabilities": round(ta - eq), "total_equity": round(eq),
            "cash_amount": round(ta * rng.uniform(0.05, 0.25)),
        })
    financial_wide = pd.DataFrame(fin_rows)

    # ---- 뉴스 감성(company_sentiment) — 부분집합 ----
    sent_idx = sorted(rng.choice(n_operating, size=int(n_operating * 0.5), replace=False))
    sentiment = pd.DataFrame([{
        "corp_code": corp_ids[i],
        "sentiment_score": round(float(rng.uniform(-1, 1)), 3),
        "confidence": round(float(rng.uniform(0.4, 0.95)), 3),
        "event_cnt": int(rng.integers(1, 12)),
    } for i in sent_idx])

    # ---- 매크로(macro) ----
    macro_rows = []
    for code, base, drift in [("BASE_RATE", 3.5, 0.0), ("KTB3Y", 3.3, 0.0), ("BSI_MFG", 95.0, 0.0)]:
        v = base
        for ym in months:
            v = v + rng.normal(drift, 0.1)
            macro_rows.append({"metric_code": code, "base_ym": ym, "value": round(v, 3)})
    macro = pd.DataFrame(macro_rows)

    artifacts = {
        "target_master": target_master,
        "post_data": post_data,
        "financial_wide": financial_wide,
        "company_sentiment": sentiment,
        "macro": macro,
    }
    paths: dict[str, Path] = {}
    for name, df in artifacts.items():
        paths[name] = write_parquet(df, out / f"{name}.parquet")
    return paths


def generate_treatment_scenario(
    *,
    seed: int = 0,
    n: int = 200,
    true_uplift: float = 0.08,
    base_ym: int = 202506,
    horizon: int = 3,
    assignment: str = "observational",
    selection_strength: float = 1.6,
    prosperity_size_coef: float = 0.02,
    base_drift: float = 0.0,
    noise: float = 0.03,
    pretrend_coef: float = 0.0,
    run_ts: str = "SCN",
) -> dict:
    """검증용 합성 처치 시나리오 — 알려진 true_uplift + 처치배정 + 번영(size상관 drift)을 심는다.

    설계 의도(serve.ledger.score_ledger_uplift 식별 3분기를 *테스트로 검증* 가능하게):
      각 법인에 잠재 size(=선택·번영 공통 교란원)를 부여하고, baseline 월 drift μ_i 를 size 에 비례
      시킨다(번영 = 가만둬도 size 큰 법인이 더 큰다). 처치는 assignment 에 따라 배정한다:
        - "observational": logit(처치)=selection_strength·size → 큰 법인일수록 처치확률↑(★선택편향).
                           holdout 없음. raw 단순차이는 편향(번영 혼입), DiD/매칭은 편향 제거를 검증.
        - "rct"          : 무작위 배정(size 무관) + holdout_flag 기록 → RCT 가 true_uplift 복원 검증.
        - "none"         : 처치정보 전부 null → 폴백(prosperity_proxy) 정직성 검증.

    잔액 경로: pre→base 성장 = μ_i·h(번영) + treated·pretrend_coef(평행추세 위반항), base→future
    성장 = μ_i·h + treated·true_uplift. pretrend_coef=0(기본)이면 DiD(post−pre)가 법인별 μ_i·h(번영)를
    차분 소거하고 treated·true_uplift 만 남긴다. pretrend_coef≠0 이면 처치군이 *pre 에서도* 더(덜) 성장해
    평행추세 가정이 깨지고 DiD 가 true_uplift 에서 −pretrend_coef 만큼 편향된다(가정이 load-bearing 임을 검증).

    반환 {ledger_df, post_data, true_uplift, params}. ledger_df 는 score_ledger_uplift 입력 형식
    (run_ts/base_ym/corp_code + 안정적 권고 메타 current_bal/market_weight + 처치 레이어).
    """
    if assignment not in ("observational", "rct", "none"):
        raise ValueError(f"assignment 은 observational|rct|none 중 하나여야 함: {assignment!r}")
    rng = np.random.default_rng(seed)
    corp = [f"S{i:06d}" for i in range(n)]
    size = rng.standard_normal(n)                          # 표준화 size(선택·번영 공통 교란원)
    bal_pre = np.exp(13.0 + 0.8 * size)                    # 시작잔액(size 단조)
    mu = base_drift + prosperity_size_coef * size          # 월 baseline drift(번영, size 상관)

    if assignment == "rct":                                # 무작위 배정(size 무관) — holdout 기록
        treated = rng.integers(0, 2, n).astype("float64")
        holdout = 1.0 - treated                            # holdout_flag=1 = 보류(control)
    elif assignment == "observational":                    # size 의존 처치(선택편향), holdout 없음
        p = 1.0 / (1.0 + np.exp(-selection_strength * size))
        treated = (rng.uniform(0, 1, n) < p).astype("float64")
        holdout = np.full(n, np.nan)
    else:                                                  # none — 처치정보 전무
        treated = np.full(n, np.nan)
        holdout = np.full(n, np.nan)

    eff = np.where(np.isnan(treated), 0.0, treated)        # 미기록=비처치로 잔액 생성
    # pre 성장 = 번영 + (평행추세 위반 시) 처치군 pre-trend 발산. post 성장 = 번영 + 처치 uplift.
    r_pre = mu * horizon + eff * pretrend_coef + rng.normal(0, noise, n)
    r_post = mu * horizon + eff * true_uplift + rng.normal(0, noise, n)
    bal_base = bal_pre * np.exp(r_pre)
    bal_future = bal_base * np.exp(r_post)

    pre_ym, fut_ym = ym_add(base_ym, -horizon), ym_add(base_ym, horizon)
    post_data = pd.DataFrame({
        "corp_code": corp * 3,
        "base_ym": [pre_ym] * n + [base_ym] * n + [fut_ym] * n,
        "bal": np.concatenate([bal_pre, bal_base, bal_future]),
    })
    ledger_df = pd.DataFrame({
        "run_ts": run_ts,
        "base_ym": base_ym,
        "corp_code": corp,
        "current_bal": bal_base,                           # 안정적 권고 메타(매칭 공변량; size 단조)
        "market_weight": bal_base / float(bal_base.sum()),
        "treated": treated,
        "treat_ym": np.where(np.isnan(treated), np.nan, float(base_ym)),
        "treat_channel": ["price" if e == 1.0 else None for e in eff],
        "holdout_flag": holdout,
    })
    return {
        "ledger_df": ledger_df,
        "post_data": post_data,
        "true_uplift": float(true_uplift),
        "params": {"seed": seed, "n": n, "assignment": assignment, "horizon": horizon,
                   "selection_strength": selection_strength,
                   "prosperity_size_coef": prosperity_size_coef, "noise": noise,
                   "pretrend_coef": pretrend_coef},
    }


if __name__ == "__main__":
    import json

    p = generate_demo()
    print(json.dumps({k: str(v) for k, v in p.items()}, ensure_ascii=False, indent=2))
