"""Walk-forward 백테스트 — BL 권고가 naive(지갑규모) 베이스라인보다 실현 예금흐름에서 우월한가.

설계: docs/design/03-bl-model-design.md §9.1(walk-forward 백테스트, 다음기 실현 잔액수익 평가),
docs/planning/03-roadmap.md WBS-M1(walk-forward 백테스트 프레임).

핵심 아이디어 — *묶임줄(tether)*:
각 평가시점 T 에서 데이터를 ``base_ym ≤ T`` 로 잘라(point-in-time, 미래 누수 차단) 전체
파이프라인을 돌려 ``target_weight`` 를 산출하고, **T+horizon 의 실현 잔액수익**으로 채점한다.
implied-vol 의 '체결가'에 대응하는 '실현 예금변화'에 권고를 처음으로 묶는 단계이며, 이 값이
나오는 순간 README 의 '가설값'들은 *검증 가능한 추정값*으로 바뀐다.

집계 지표:
- 실현 포트폴리오 수익: ``Σ wᵢ·rᵢ`` (rᵢ = T→T+h 잔액 log-return). 전략별(BL / market / equal).
- 실현 IR: mean/std. (윈도우 overlap 시 자기상관으로 IR 과대평가 — 깨끗한 IR 은 ``step≥horizon``.)
- IC: BL active tilt(``target − market``)와 실현수익의 Spearman 순위상관 — 뷰가 *추가 정보*를 주는가.
- Precision@K: BL 상위가중 K개 중 실현수익이 단면중앙값 초과인 비율(vs market 베이스라인의 동일 지표).
- lift / win_rate: BL 이 naive 베이스라인을 실제로 이기는가(이 프로젝트 가치검증의 핵심 질문).

한계(정직): 합성 데모에서 company_sentiment·financial 은 단면 스냅샷이라 **잔액 패널만** 엄격히
시점분리된다(지배적 신호는 잔액이므로 결론에 큰 영향 없음). 실데이터 경로는 ingest 의 base_ym
컷오프(enrich.sentiment._cutoff_news, 재무 공시지연 ASOF)가 나머지 축의 시점분리를 처리한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bl.common.logging import get_logger
from bl.features.builder import LABEL_HORIZON
from bl.pipeline import run_from_frames

log = get_logger(__name__)

DEFAULT_MIN_TRAIN_MONTHS = 9   # T 이전 최소 학습 개월(분류기 안정 확보)
DEFAULT_TOP_K_FRAC = 0.2       # Precision@K 의 상위 K 비율


def realized_forward_returns(
    post_data: pd.DataFrame, base_ym: int, future_ym: int, *, log_return: bool = True
) -> pd.Series:
    """corp_code → 실현 전방 잔액수익(base_ym→future_ym). 두 시점 모두 양수 잔액인 corp만 반환.

    이것이 '체결가' 대용물이다: T 시점 권고를 T+h 의 실제 잔액변화로 채점하는 근거.
    """
    wide = post_data.pivot_table(index="base_ym", columns="corp_code", values="bal", aggfunc="last")
    wide.columns = wide.columns.astype(str)
    if base_ym not in wide.index or future_ym not in wide.index:
        return pd.Series(dtype="float64", name="r")
    b0 = wide.loc[base_ym].astype("float64")
    b1 = wide.loc[future_ym].astype("float64")
    valid = (b0 > 0) & (b1 > 0) & np.isfinite(b0) & np.isfinite(b1)
    ratio = b1[valid] / b0[valid]
    r = np.log(ratio) if log_return else (ratio - 1.0)
    return r.rename("r")


def _truncate_frames(frames: dict, base_ym: int) -> dict:
    """point-in-time: post_data·macro 를 base_ym 이하로 제한(미래 잔액/거시 누수 차단)."""
    out = dict(frames)
    post = frames["post_data"]
    out["post_data"] = post[post["base_ym"] <= base_ym].reset_index(drop=True)
    mac = frames.get("macro")
    if mac is not None and "base_ym" in getattr(mac, "columns", []):
        out["macro"] = mac[mac["base_ym"] <= base_ym].reset_index(drop=True)
    return out


def _renorm(w: np.ndarray) -> np.ndarray:
    """음수 클립 후 합=1 정규화(유효 자산 부분집합에 대한 포트폴리오 재정규화)."""
    w = np.clip(np.asarray(w, dtype="float64"), 0.0, None)
    s = w.sum()
    return w / s if s > 0 else np.full(len(w), 1.0 / len(w))


def _score_window(mart: pd.DataFrame, realized: pd.Series, *, top_k_frac: float) -> dict | None:
    """한 윈도우: mart(target/market weight) + 실현수익 → 전략별 포트폴리오 수익·IC·P@K."""
    from scipy.stats import spearmanr

    m = mart[["corp_code", "target_weight", "market_weight"]].copy()
    m["corp_code"] = m["corp_code"].astype(str)
    m = m.merge(realized, left_on="corp_code", right_index=True, how="inner")
    m = m[np.isfinite(m["r"].to_numpy(dtype="float64"))]
    n = len(m)
    if n < 2:
        return None

    r = m["r"].to_numpy(dtype="float64")
    wt = _renorm(m["target_weight"].to_numpy(dtype="float64"))
    wm = _renorm(m["market_weight"].to_numpy(dtype="float64"))
    active = wt - wm
    ic = float(spearmanr(active, r).correlation) if float(np.std(active)) > 1e-12 else np.nan

    k = max(1, int(round(top_k_frac * n)))
    med = float(np.median(r))
    top_bl = np.argsort(wt)[-k:]
    top_mkt = np.argsort(wm)[-k:]
    return {
        "n": n,
        "ret_bl": float(wt @ r),
        "ret_market": float(wm @ r),
        "ret_equal": float(np.mean(r)),
        "ic": ic,
        "prec_bl": float(np.mean(r[top_bl] > med)),
        "prec_market": float(np.mean(r[top_mkt] > med)),
    }


def summarize(per_window: pd.DataFrame, *, n_skipped: int = 0,
              horizon: int = LABEL_HORIZON, step: int = 1) -> dict:
    """윈도우별 결과 → 집계 지표(전략 비교·IR·IC·P@K lift). 빈 결과는 진단 dict 반환."""
    if per_window.empty:
        return {"n_windows": 0, "n_skipped": int(n_skipped), "note": "유효 윈도우 없음"}
    d = per_window

    def _ir(col: str) -> float:
        x = d[col].to_numpy(dtype="float64")
        sd = float(np.std(x))
        return float(np.mean(x) / sd) if sd > 1e-12 else float("nan")

    bl = d["ret_bl"].to_numpy(dtype="float64")
    mk = d["ret_market"].to_numpy(dtype="float64")
    return {
        "n_windows": int(len(d)),
        "n_skipped": int(n_skipped),
        "horizon_months": int(horizon),
        "step_months": int(step),
        "overlapping": bool(step < horizon),
        "mean_ret_bl": float(np.mean(bl)),
        "mean_ret_market": float(np.mean(mk)),
        "mean_ret_equal": float(d["ret_equal"].mean()),
        "lift_bl_vs_market": float(np.mean(bl) - np.mean(mk)),
        "ir_bl": _ir("ret_bl"),
        "ir_market": _ir("ret_market"),
        "win_rate_bl_gt_market": float(np.mean(bl > mk)),
        "mean_ic": float(d["ic"].mean(skipna=True)),
        "mean_prec_at_k_bl": float(d["prec_bl"].mean()),
        "mean_prec_at_k_market": float(d["prec_market"].mean()),
        "prec_lift_bl_vs_market": float(d["prec_bl"].mean() - d["prec_market"].mean()),
    }


def run_backtest(
    frames: dict, *, horizon: int = LABEL_HORIZON, step: int = 1,
    min_train_months: int = DEFAULT_MIN_TRAIN_MONTHS, top_k_frac: float = DEFAULT_TOP_K_FRAC,
    seed: int = 42, tau: float | None = None, view_corr: float | None = None,
    omega_scale: float = 1.0, gamma_anom: float | None = None, lambda_fixed: float | None = None,
) -> dict:
    """프레임을 walk-forward 로 백테스트한다(누수 차단 point-in-time).

    각 평가시점 T(=months[i])에서 frames 를 ≤T 로 잘라 파이프라인을 돌리고, months[i+horizon]
    의 실현 잔액수익으로 채점한다. 반환: {per_window(DataFrame), summary(dict), skipped(list)}.
    실패/유효자산 부족 윈도우는 **제외 사유와 함께 기록**한다(silent drop 금지).
    """
    post = frames["post_data"]
    months = sorted(int(m) for m in post["base_ym"].unique())
    last_idx = len(months) - horizon                 # months[i+horizon] 이 존재할 마지막 i(exclusive)
    eval_idx = list(range(min_train_months, last_idx, step))

    rows: list[dict] = []
    skipped: list[tuple[int, str]] = []
    for i in eval_idx:
        t, t_future = months[i], months[i + horizon]
        try:
            ft = _truncate_frames(frames, t)
            res = run_from_frames(ft, base_ym=t, seed=seed, render=False, source="backtest",
                                  tau=tau, view_corr=view_corr, omega_scale=omega_scale,
                                  gamma_anom=gamma_anom, lambda_fixed=lambda_fixed)
            realized = realized_forward_returns(post, t, t_future)
            sc = _score_window(res["mart"], realized, top_k_frac=top_k_frac)
            if sc is None:
                skipped.append((t, "유효 자산<2"))
                continue
            sc.update({"base_ym": t, "future_ym": t_future})
            rows.append(sc)
        except Exception as e:  # noqa: BLE001 — 한 윈도우 실패가 전체 백테스트를 막지 않도록
            log.warning(f"백테스트 윈도우 {t} 실패 → 제외: {e}", extra={"stage": "eval.backtest"})
            skipped.append((t, str(e)))

    cols = ["base_ym", "future_ym", "n", "ret_bl", "ret_market", "ret_equal",
            "ic", "prec_bl", "prec_market"]
    per_window = pd.DataFrame(rows, columns=cols)
    summary = summarize(per_window, n_skipped=len(skipped), horizon=horizon, step=step)
    if skipped:
        log.info(f"백테스트 제외 윈도우 {len(skipped)}개", extra={"stage": "eval.backtest"})
    return {"per_window": per_window, "summary": summary, "skipped": skipped}


def _format_summary(s: dict) -> str:
    """요약 dict → 사람이 읽는 표(콘솔/README 첨부용)."""
    if s.get("n_windows", 0) == 0:
        return f"백테스트 유효 윈도우 없음(skipped={s.get('n_skipped', 0)})"
    pct = lambda x: f"{x * 100:+.2f}%"  # noqa: E731
    lines = [
        f"윈도우 {s['n_windows']}개 (horizon={s['horizon_months']}m, step={s['step_months']}m, "
        f"overlap={s['overlapping']}, skipped={s['n_skipped']})",
        f"  실현수익 평균   BL {pct(s['mean_ret_bl'])} | market {pct(s['mean_ret_market'])} | "
        f"equal {pct(s['mean_ret_equal'])}",
        f"  BL vs market    lift {pct(s['lift_bl_vs_market'])} | "
        f"win-rate {s['win_rate_bl_gt_market'] * 100:.0f}% | IR(BL) {s['ir_bl']:.3f} / "
        f"IR(mkt) {s['ir_market']:.3f}",
        f"  IC(BL tilt)     {s['mean_ic']:+.3f}",
        f"  Precision@K     BL {s['mean_prec_at_k_bl']:.3f} | market {s['mean_prec_at_k_market']:.3f} "
        f"| lift {s['prec_lift_bl_vs_market']:+.3f}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    from bl.pipeline import _load_sample

    fr = _load_sample("data/sample")
    out = run_backtest(fr)
    print(_format_summary(out["summary"]))
    if not out["per_window"].empty:
        print("\nper-window:")
        print(out["per_window"].to_string(index=False))
