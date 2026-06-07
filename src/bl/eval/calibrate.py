"""캘리브레이션 — BL 하이퍼파라미터를 walk-forward 백테스트의 *실현지표*로 역산한다.

설계: docs/design/03-bl-model-design.md §5.2(τ 민감도 그리드), §5.4/§9.3·ADR-0004 §5(Ω reliability),
§11(파라미터 표). 핵심 전환: README 의 '가설값'(선언) → 실현 잔액수익으로 검증된 '추정값'.

- sweep_tau: τ ∈ {0.025,0.05,0.1}(설계 그리드)를 백테스트로 평가 → lift/IC/IR 안정성·최적값 확인.
- calibrate_view_corr: 뷰 off-diagonal Ω 상관 ρ_view 그리드를 실현지표로 역산 — 두 번영뷰(news·pattern)
  중복을 얼마나 상쇄할지(독립확증 과신 제어)를 데이터로 정한다. E3a 블록스택의 뷰 결합 손잡이이며,
  과거의 손가중 a 그리드(calibrate_axis_weights, 단일병합 전제)를 대체한다(E3b 자리; 손가중은 BL Ω가 융합).
  ρ_view=None(경험 신호상관 프록시)이 기본이고, 그리드는 명시 스칼라(0=독립↔1 근접=완전중복) 후보다.
- calibrate_omega_scale: Ω 전역 신뢰스케일을 실현 lift 로 역산. ADR-0004 §5 reliability 보정의
  1차 근사(전역 스케일)이며, 다음단계는 축별 Platt/isotonic reliability(예측 confidence↔실현 적중률).
- calibrate_gamma_anom: anomaly 의 Ω 변조 강도 γ 를 실현 lift/IC 로 역산 — anomaly 가 방향 뷰가
  아니라 Ω 신뢰도 요인(DRI·conf 형제)이라는 설계 전환(E2)을 데이터로 검증한다.
- sweep_lambda_fixed: 앵커 Π 스케일 정규화 상수 λ_fix(C3)를 스윕 → 보수↔공격 스펙트럼. λ_fix↑ = 앵커↑
  (보수), λ_fix↓ = 뷰 지배(공격). τ↑·λ_fix↓·γ_anom↓ 가 공통 '공격' 방향(설계 §5.5).

⚠️ **번영 추종 ≠ 영업효과(중요)**: 현 백테스트는 **raw 실현 잔액수익(번영)** 으로 채점하므로, 여기서 나오는
lift/IC/'best'는 *영업 처치효과(uplift)가 아니라 '번영 추종' 지표*다(C1 미해결). 공격성을 올리면 합성
positive-control에 과적합(굿하트)될 수 있으니, **이 도구로 운영값을 확정하지 말 것** — 기본은 보수값 유지,
실제 운영값은 처치 레이어(uplift 채점) 머지 후 KB 실데이터에서 재캘리브레이션해 확정한다.

비용 주의: 각 후보 = 전체 백테스트 1회(파이프라인 수십 회). step/min_train_months 로 조절한다.
"""

from __future__ import annotations

import pandas as pd

from bl.eval.backtest import run_backtest

DEFAULT_TAU_GRID: tuple[float, ...] = (0.025, 0.05, 0.1)
# 뷰 off-diagonal Ω 상관 ρ_view 후보(E3a 블록스택). None=경험 신호상관 프록시(기본), 0=독립(K배 과신),
# 1 근접=완전중복(count-once, 보수). 첫 항목 None = 현재 운영값(eval baseline).
DEFAULT_VIEW_CORR_GRID: tuple[float | None, ...] = (None, 0.0, 0.3, 0.6, 0.9)
DEFAULT_OMEGA_GRID: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0)
# anomaly Ω 변조 게인 γ 그리드. γ=0 = anomaly 무시(Ω 변조 없음). 기본 운영값 GAMMA_ANOM=2.0.
DEFAULT_GAMMA_GRID: tuple[float, ...] = (0.0, 1.0, 2.0, 4.0)
# 앵커 Π 스케일 정규화 상수 λ_fix 그리드. 낮을수록 앵커↓→뷰 지배(공격), 높을수록 앵커↑(보수).
# 기본 운영값 LAMBDA_FIXED=0.25(앵커 사후기여 ~30%).
DEFAULT_LAMBDA_GRID: tuple[float, ...] = (0.1, 0.25, 0.5, 1.0)

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


def calibrate_view_corr(
    frames: dict, grid: tuple[float | None, ...] = DEFAULT_VIEW_CORR_GRID, *,
    objective: str = "mean_ic", **bt_kwargs,
) -> tuple[pd.DataFrame, dict]:
    """뷰 off-diagonal Ω 상관 ρ_view 그리드를 실현 IC(기본)로 역산 → (지표표, 최적 행).

    E3a 블록스택의 뷰 결합 손잡이(과거 손가중 calibrate_axis_weights 대체, E3b 자리). ρ_view 가
    클수록 두 번영뷰를 중복으로 보아 한 뷰처럼 묶고(보수, 앵커↑), 작을수록 독립확증(공격, 앵커↓).
    None=경험 신호상관 프록시. 결과의 view_corr 컬럼은 그리드 원소(None 은 문자열 'empirical')."""
    rows = []
    for rho in grid:
        m = _metrics(run_backtest(frames, view_corr=rho, **bt_kwargs)["summary"])
        rows.append({"view_corr": "empirical" if rho is None else rho, **m})
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


def sweep_lambda_fixed(
    frames: dict, lambdas: tuple[float, ...] = DEFAULT_LAMBDA_GRID, *,
    objective: str = "lift_bl_vs_market", **bt_kwargs,
) -> tuple[pd.DataFrame, dict]:
    """앵커 Π 스케일 정규화 상수 λ_fix(C3) 스윕 → (지표표, objective 최대 행). **보수↔공격 스펙트럼용**.

    λ_fix↓ = 앵커 약화 → 뷰 지배(공격), λ_fix↑ = 앵커 강화 → do-nothing 회귀(보수). 모듈 docstring의
    '번영 추종 ≠ 영업효과' 경고대로, 반환 'best'(raw 실현수익 기준)로 운영값을 확정하지 말 것 — 스펙트럼
    관찰·문서화 용도이며 운영값은 uplift 채점 후 재캘리한다.
    """
    rows = [{"lambda_fixed": lam,
             **_metrics(run_backtest(frames, lambda_fixed=lam, **bt_kwargs)["summary"])}
            for lam in lambdas]
    df = pd.DataFrame(rows)
    return df, _best(df, objective)


if __name__ == "__main__":
    from bl.pipeline import _load_sample

    fr = _load_sample("data/sample")
    print("=== τ sweep (목적=lift) ===")
    tdf, tbest = sweep_tau(fr)
    print(tdf.to_string(index=False))
    print(f"→ best τ = {tbest.get('tau')} (lift={tbest.get('lift_bl_vs_market'):+.4f})")

    print("\n=== 뷰 off-diag 상관 ρ_view 역산 (목적=mean_ic) ===")
    vdf, vbest = calibrate_view_corr(fr)
    print(vdf.to_string(index=False))
    print(f"→ best ρ_view = {vbest.get('view_corr')} (IC={vbest.get('mean_ic'):+.4f})")

    print("\n=== Ω-scale 캘리브레이션 (목적=lift) ===")
    odf, obest = calibrate_omega_scale(fr)
    print(odf.to_string(index=False))
    print(f"→ best Ω-scale = {obest.get('omega_scale')} (lift={obest.get('lift_bl_vs_market'):+.4f})")

    print("\n=== γ_anom (anomaly Ω 변조) 캘리브레이션 (목적=lift) ===")
    gdf, gbest = calibrate_gamma_anom(fr)
    print(gdf.to_string(index=False))
    print(f"→ best γ_anom = {gbest.get('gamma_anom')} (lift={gbest.get('lift_bl_vs_market'):+.4f})")

    print("\n=== λ_fix (앵커 Π 스케일) 스펙트럼 (보수↔공격; 운영값 확정 금지) ===")
    ldf, lbest = sweep_lambda_fixed(fr)
    print(ldf.to_string(index=False))
    print(f"→ raw-lift 최대 λ_fix = {lbest.get('lambda_fixed')} "
          f"(lift={lbest.get('lift_bl_vs_market'):+.4f}; ⚠ 번영추종 — 운영값은 uplift 재캘리 후 확정)")
