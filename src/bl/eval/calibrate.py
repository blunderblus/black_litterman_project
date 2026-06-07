"""캘리브레이션 — BL 하이퍼파라미터를 walk-forward 백테스트의 *실현지표*로 역산한다.

설계: docs/design/03-bl-model-design.md §5.2(τ 민감도 그리드), §5.4/§9.3·ADR-0004 §5(Ω reliability),
§11(파라미터 표). 핵심 전환: README 의 '가설값'(선언) → 실현 잔액수익으로 검증된 '추정값'.

- sweep_tau: τ ∈ {0.025,0.05,0.1}(설계 그리드)를 백테스트로 평가 → lift/IC/IR 안정성·최적값 확인.
- calibrate_axis_weights: 뷰 3축(news/pattern/relationship) 가중 a 그리드를 실현 IC 로 역산 —
  비율보존 재정규화 기본값 ≈(0.412,0.412,0.176)이 최적인지 데이터로 검증(anomaly 는 Ω 요인으로 분리).
- calibrate_omega_scale: Ω 전역 신뢰스케일을 실현 lift 로 역산. ADR-0004 §5 reliability 보정의
  1차 근사(전역 스케일)이며, 다음단계는 축별 Platt/isotonic reliability(예측 confidence↔실현 적중률).
- calibrate_gamma_anom: anomaly 의 Ω 변조 강도 γ 를 실현 lift/IC 로 역산 — anomaly 가 방향 뷰가
  아니라 Ω 신뢰도 요인(DRI·conf 형제)이라는 설계 전환(E2)을 데이터로 검증한다.

비용 주의: 각 후보 = 전체 백테스트 1회(파이프라인 수십 회). step/min_train_months 로 조절한다.
"""

from __future__ import annotations

import pandas as pd

from bl.engine.inputs import AXIS_WEIGHTS
from bl.eval.backtest import run_backtest

# 축가중 후보 그리드(합=1, 뷰 3축 news/pattern/relationship). 첫 항목 = 현재 운영값(eval 시 baseline).
DEFAULT_AXIS_GRID: list[dict] = [
    dict(AXIS_WEIGHTS),                                       # 현재 ≈ (0.412,0.412,0.176)
    {"news": 0.30, "pattern": 0.50, "relationship": 0.20},   # pattern 강화
    {"news": 0.50, "pattern": 0.30, "relationship": 0.20},   # news 강화
    {"news": 0.35, "pattern": 0.35, "relationship": 0.30},   # relationship 강화
    {"news": 0.34, "pattern": 0.33, "relationship": 0.33},   # 균등
]
DEFAULT_TAU_GRID: tuple[float, ...] = (0.025, 0.05, 0.1)
DEFAULT_OMEGA_GRID: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0)
# anomaly Ω 변조 게인 γ 그리드. γ=0 = anomaly 무시(Ω 변조 없음). 기본 운영값 GAMMA_ANOM=2.0.
DEFAULT_GAMMA_GRID: tuple[float, ...] = (0.0, 1.0, 2.0, 4.0)

# 백테스트 summary 에서 추출하는 비교지표(목적함수 후보 포함)
_METRIC_KEYS = ("lift_bl_vs_market", "mean_ic", "ir_bl", "win_rate_bl_gt_market",
                "mean_prec_at_k_bl", "n_windows")


def _metrics(summary: dict) -> dict:
    """백테스트 summary → 비교지표 부분집합(결측은 NaN)."""
    return {k: summary.get(k, float("nan")) for k in _METRIC_KEYS}


def _best(df: pd.DataFrame, objective: str) -> dict:
    """objective 컬럼 최대 행을 dict 로(빈 df 면 빈 dict). NaN 은 idxmax 가 자동 제외."""
    if df.empty or df[objective].isna().all():
        return {}
    return df.loc[df[objective].idxmax()].to_dict()


def sweep_tau(
    frames: dict, taus: tuple[float, ...] = DEFAULT_TAU_GRID, *,
    objective: str = "lift_bl_vs_market", **bt_kwargs,
) -> tuple[pd.DataFrame, dict]:
    """τ 그리드를 백테스트로 평가 → (지표표, 최적 τ 행). objective 로 최적 선택."""
    rows = [{"tau": tau, **_metrics(run_backtest(frames, tau=tau, **bt_kwargs)["summary"])}
            for tau in taus]
    df = pd.DataFrame(rows)
    return df, _best(df, objective)


def calibrate_axis_weights(
    frames: dict, grid: list[dict] = DEFAULT_AXIS_GRID, *,
    objective: str = "mean_ic", **bt_kwargs,
) -> tuple[pd.DataFrame, dict]:
    """축가중 a 그리드를 실현 IC(기본)로 역산 → (지표표, 최적 가중 행)."""
    rows = [{**aw, **_metrics(run_backtest(frames, axis_weights=aw, **bt_kwargs)["summary"])}
            for aw in grid]
    df = pd.DataFrame(rows)
    return df, _best(df, objective)


def calibrate_omega_scale(
    frames: dict, scales: tuple[float, ...] = DEFAULT_OMEGA_GRID, *,
    objective: str = "lift_bl_vs_market", **bt_kwargs,
) -> tuple[pd.DataFrame, dict]:
    """Ω 전역 신뢰스케일을 실현 lift(기본)로 역산 → (지표표, 최적 스케일 행).

    omega_scale<1 = 뷰를 더 신뢰(검증됐으므로) → 큰 tilt, >1 = 덜 신뢰 → prior(market) 회귀.
    """
    rows = [{"omega_scale": sc, **_metrics(run_backtest(frames, omega_scale=sc, **bt_kwargs)["summary"])}
            for sc in scales]
    df = pd.DataFrame(rows)
    return df, _best(df, objective)


def calibrate_gamma_anom(
    frames: dict, gammas: tuple[float, ...] = DEFAULT_GAMMA_GRID, *,
    objective: str = "lift_bl_vs_market", **bt_kwargs,
) -> tuple[pd.DataFrame, dict]:
    """anomaly 의 Ω 변조 강도 γ 를 실현 lift(기본)로 역산 → (지표표, 최적 γ 행).

    γ=0 = anomaly 무시(Ω 변조 없음), γ↑ = 이상 법인 뷰를 더 강하게 prior(앵커)로 후퇴.
    anomaly 가 Q(방향 뷰)가 아니라 Ω(신뢰도)에 들어가야 한다는 설계 전환(E2)을 데이터로 검증한다.
    """
    rows = [{"gamma_anom": g, **_metrics(run_backtest(frames, gamma_anom=g, **bt_kwargs)["summary"])}
            for g in gammas]
    df = pd.DataFrame(rows)
    return df, _best(df, objective)


if __name__ == "__main__":
    from bl.pipeline import _load_sample

    fr = _load_sample("data/sample")
    print("=== τ sweep (목적=lift) ===")
    tdf, tbest = sweep_tau(fr)
    print(tdf.to_string(index=False))
    print(f"→ best τ = {tbest.get('tau')} (lift={tbest.get('lift_bl_vs_market'):+.4f})")

    print("\n=== 축가중 a 역산 (목적=mean_ic) ===")
    adf, abest = calibrate_axis_weights(fr)
    print(adf.to_string(index=False))
    print(f"→ best a = news {abest.get('news')}/pattern {abest.get('pattern')}/"
          f"relationship {abest.get('relationship')} (IC={abest.get('mean_ic'):+.4f})")

    print("\n=== Ω-scale 캘리브레이션 (목적=lift) ===")
    odf, obest = calibrate_omega_scale(fr)
    print(odf.to_string(index=False))
    print(f"→ best Ω-scale = {obest.get('omega_scale')} (lift={obest.get('lift_bl_vs_market'):+.4f})")

    print("\n=== γ_anom (anomaly Ω 변조) 캘리브레이션 (목적=lift) ===")
    gdf, gbest = calibrate_gamma_anom(fr)
    print(gdf.to_string(index=False))
    print(f"→ best γ_anom = {gbest.get('gamma_anom')} (lift={gbest.get('lift_bl_vs_market'):+.4f})")
