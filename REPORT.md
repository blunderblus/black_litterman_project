# E3a — 뷰 레지스트리 비계(scaffold): 단일 Q 병합 → 블록스택 독립뷰 구조

작업일: 2026-06-08 · 선행: C3(`6ce82e5`)·E2(`e850a5f`) 머지 위에 적층 · (C3 보고는 git history)

## 0. 한 줄 결론

**뷰를 '축가중 단일병합 Q'에서 '뷰 레지스트리 블록스택'($P=[I;I]$, 뷰별 단위정합 $Q$ 블록, per-view $\Omega$
+ off-diagonal 뷰상관)으로 재구성했다. 이 비계는 *기본 경로(경험 프록시)에서 융합 동작을 실질 보존한다*
— 단일병합 대비 target_weight Spearman 0.87·top-5 4/5(경험적 동등성, §4), off-diagonal이 두 번영뷰
(news·pattern)의 독립확증 과신을 상쇄한다. (정확히는 per-view ω 가 같을 때만 off-diag가 방향에 불변이고,
운영 경로의 상이 conf에서는 작은 ρ 기본값이 동등성을 떠받친다 — §3.) relationship 은 방향 뷰가 아니라
현재상태=이동성이라 뷰에서 빼 $\Sigma$ 이동성 슬롯으로 예약(E1b 대기)했고, 미래뷰는 한 줄 등록으로 꽂히는
플러그인 자리를 열었다. 정지조건 미발동.**

## 1. 변경 요약

| 파일 | 변경 |
| --- | --- |
| `engine/inputs.py` | `ViewSpec`·`VIEW_REGISTRY`([news,pattern]) 도입. `assemble_bl_inputs`: $P=[I;I]$ 블록스택(KN×N), 뷰별 단위정합 $Q$ 블록 쌓기, per-view $\Omega$ 대각(news=gemini_conf·pattern=conf_growth, E4 부분개선) + **off-diagonal** $R^{\text{view}}\sqrt{\omega_a\omega_b}$. 결합 단일뷰 등가 `q_eff`/`omega_eff` 산출. `build_views`/`AXIS_WEIGHTS`(손가중) 제거 → `build_view_signals`. `view_corr` 인자(off-diag 손잡이) + PSD-clip(`VIEW_CORR_MAX=0.98`). |
| `engine/optimize.py` | 변경 없음 — `_parse_inputs` 가 이미 $P$를 K×N 일반형(K=뷰수)으로 처리, KN×KN $\Omega$ 와 KN×KN solve 정상 동작(엔진 불요 확인). |
| `pipeline.py` | `_view_scaler` 를 레지스트리 정렬(relationship 제외). `axis_weights`→`view_corr` 손잡이 스레딩. mart `q`/`omega` = `q_eff`/`omega_eff`(법인당 스칼라, 역방향 호환). |
| `eval/backtest.py`·`calibrate.py` | `axis_weights`→`view_corr` 스레딩. `calibrate_axis_weights`(손가중 그리드)→**`calibrate_view_corr`**(off-diag 상관 그리드, E3b 자리). `DEFAULT_AXIS_GRID`→`DEFAULT_VIEW_CORR_GRID`. |
| `serve/ledger.py` | q/omega 가 결합 단일뷰 등가(법인당 스칼라)임을 문서화(additive·역방향 호환, primary 원장 불변). |
| 테스트 | 블록스택 KN×N·등-ω 방향불변·ω상이 방향조절(정직)·플러그인 기여·결합등가(view_corr 가변)·off-diag PSD 추가. **132→142 pass.** |
| 문서 | README·`docs/design/03` §2.1/§5(전면)/§9.2/§11·`01`·`02`·`planning 01/02/03`·`concept 01-04`·`ADR-0004`·`glossary` — 뷰 레지스트리·off-diag·relationship→Σ 예약으로 일괄 갱신(적대적 멀티에이전트 스윕). |

## 2. 구조 변경도

```
[E3a 전] 단일 병합 Q (법인당 1뷰)
  news,pattern,relationship 3축 → 손가중 a(0.412,0.412,0.176) 가중합 → 단위정합 → Q(N) ; P=I(N×N) ; Ω=diag(N)

[E3a 후] 뷰 레지스트리 블록스택 (법인당 K뷰)
  VIEW_REGISTRY=[news, pattern]                     relationship → Σ 이동성 슬롯 예약(E1b)
  뷰별 표준화 z_v → 블록별 단위정합 Q_v(Var=τ·meanΣ) anomaly → Ω 요인(E2, 모든 뷰 공통 곱)
        ↓ 쌓기(가중합 X)
  P = [I; I]  (KN×N)   Q = [Q_news; Q_pattern] (KN)
  Ω (KN×KN):  대각 = per-view (DRI·conf_v·anomaly)         ← 뷰별 confidence (E4 부분개선)
              off-diag(a,b) = R_view[a,b]·√(ω_a·ω_b)        ← 두 번영뷰 상관 → 과신 상쇄
        ↓ (로깅·대시보드용) 결합 단일뷰 등가
  q_eff = 1ᵀG⁻¹q / 1ᵀG⁻¹1,  omega_eff = 1/(1ᵀG⁻¹1)   (블록스택과 *동일* 사후를 내는 단일뷰)
```

엔진(`posterior_expected_return`)은 $E[R]=\Pi+\tau\Sigma P^\top(P\tau\Sigma P^\top+\Omega)^{-1}(Q-P\Pi)$ 의
K×K(=KN) solve 로 변경 없이 동작(K=2N=72 @데모, 조건수 건전).

## 3. 동작 동등의 수학 (왜 비계가 융합동작을 안 바꾸나 — 그리고 그 *조건*)

$P=[I;I]$ 에서 자산 $i$의 뷰 블록 $G_i=\text{diag}(\sqrt{\omega})R^{\text{view}}\text{diag}(\sqrt{\omega})$ 에 대해
결합 뷰값 $q_{\text{eff},i}=\dfrac{\mathbf 1^\top G_i^{-1}q_i}{\mathbf 1^\top G_i^{-1}\mathbf 1}$, 결합 정밀도 $\mathbf 1^\top G_i^{-1}\mathbf 1$:

- **등-ω 특수케이스(엄밀)**: $\omega_{a}=\omega_{b}=\omega$ 이면 $q_{\text{eff}}=\dfrac{q_1+q_2}{2}$ = **per-view 평균**으로
  off-diagonal $\rho$ 와 **무관**(방향 불변), 결합 정밀도 $=\dfrac{2}{\omega(1+\rho)}$ 만 $\rho$ 가 조절. 이때만 "off-diag는
  과신만 규제, 방향 보존"이 *정확히* 성립.
- **일반(운영) 경로**: 뷰별 confidence 가 상이($\omega_a\neq\omega_b$; news=gemini_confidence·pattern=
  confidence_growth, E4)하면 $q_{\text{eff}}$ 는 **정밀도가중 혼합**이라 off-diag 가 *방향(q_eff)도* 조절한다.
  즉 단일병합과의 동작 동등(랭킹 보존)은 **무조건 불변량이 아니라 기본 경로(경험 프록시 ρ가 작음)에서
  성립하는 *경험적* 속성**이다(§4 측정). $\rho{=}0$=독립계상($K$배 과신=E1 회귀)↔$\rho{\to}1$=특이 Ω 직전(조건수
  폭주, VIEW_CORR_MAX 차단). ω 상이 + 큰 ρ 에서는 랭킹이 오히려 더 흔들린다(§4 sweep ρ=0.9→0.847·0.95→0.696).

→ 따라서 **기본값을 경험 프록시(작은 ρ)로 두는 것이 동등성 보존의 핵심**이다. 한편 결합 등가
$(q_{\text{eff}},\omega_{\text{eff}})$ 단일뷰의 BL 사후가 블록스택과 **정확히 일치**함은 ρ 무관하게 성립한다(테스트
`test_combined_stats_reproduce_blockstack_posterior`) — 이는 '블록스택이 *어떤* 단일뷰로 환원됨'(로깅 정당성)이지
'그 단일뷰가 *과거 병합과 같음*'은 아니다. 후자가 §4의 경험 측정 대상이다.

## 4. 동작 동등 증거 (demo, 단일병합 대비 블록스택)

| view_corr | Spearman(target_weight) | Spearman(marketing) | top-5 | top-10 | 앵커기여@τ.05 | K×K 조건수 | max wᵢ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| (E3a 전 단일병합) | 1.000 | 1.000 | 5/5 | 10/10 | **0.440** | 137 | 0.10 |
| **empirical(ρ≈0.18, 기본)** | **0.865** | 0.886 | 4/5 | 8/10 | 0.374 | 283 | 0.10 |
| 0.0 (대각=이중계상) | 0.897 | 0.921 | 4/5 | 8/10 | 0.354 | 213 | 0.10 |
| 0.3 | 0.930 | 0.910 | 4/5 | 8/10 | 0.392 | 345 | 0.10 |
| 0.6 (데모 동등성 최적) | 0.943 | 0.918 | 5/5 | 8/10 | 0.402 | 780 | 0.10 |
| 0.9 | 0.847 | 0.831 | 3/5 | 6/10 | 0.317 | 3450 | 0.10 |
| 0.95 | 0.696 | 0.774 | 1/5 | 5/10 | 0.251 | 7273 | 0.10 |

- **랭킹상관 높음**(기본 0.865, C3가 수용한 0.785 상회), **max wᵢ 캡유지**(과신·극단가중 없음).
- $\rho{\to}1$ 은 동등성을 **악화**(랭킹 0.70·조건수 7273) — 완전중복=특이 Ω 의 수치 병리. 즉 동등성은
  $\rho{\approx}0.6$ 에서 최대이고 그 이상은 PSD/조건수가 깨진다(정지조건이 가리키는 지점).

**동등성 분해**(같은 Σ/Π/optimizer, demo):

| 비교 | Spearman(tw) | 의미 |
| --- | --- | --- |
| A(3축병합) vs B(news+pattern 2축병합) | 0.949 | **relationship-drop** 단독 효과(블록스택 무관) |
| B(2축병합) vs C(블록스택 empirical) | 0.926 | **블록스택 구조** 효과 |
| A(3축병합) vs C(블록스택 empirical) | 0.883 | 전체 |

→ ρ=0.6에서 A-vs-C(0.943) ≈ A-vs-B(0.949): **블록스택은 redundancy 가 옳게 명시되면 2축병합(count-once)을
정확히 재현**(융합중립). 잔여 차이는 *블록스택이 아니라 의도된 relationship-drop* 이 지배.

> ⚠️ **재현성 단서(정직)**: §4 표의 비교기준 행 — 'E3a 전 단일병합'(0.440/Spearman 1.000)·동등성 분해
> 'A 3축병합'·'B 2축병합' — 은 **E3a 적용 전 일회성 산출 스냅샷**이다. 손가중 단일병합 코드(`build_views`/
> `AXIS_WEIGHTS`)는 현재 트리에서 제거됐고(검증 스크립트는 미추적 `_scratch/`, 모델 학습 비결정성으로
> 소수점 흔들림: Spearman 0.86~0.93·ρ 0.15~0.19) 회귀 테스트 가드가 없다. 따라서 위 정량치는 *방향성 근거*로
> 읽되, 코드로 항상 재현되는 가드는 backtest 지표(§5, win 100%·max wᵢ 0.10 캡)와 `test_*`(블록스택 shape·
> 결합등가·등-ω 불변·플러그인 기여)다. 분해를 회귀 고정하려면 레거시 병합 헬퍼를 테스트 픽스처로 보존해야
> 한다(후속 가능, E3b).

## 5. 결합 보수성 재측정 (walk-forward backtest, 12 윈도우)

| 지표 | C3·E2 (E3a 전) | **+E3a (기본 empirical)** | 비고 |
| --- | --- | --- | --- |
| lift (BL−naive) | +12.08%p | **+16.1%p** | ↑ — 그러나 *번영 추종*(굿하트), 아래 |
| mean_IC | +0.312 | +0.391 | |
| ir_bl | −0.035 | +1.17 | |
| win_rate | 100% | 100% | 하방 회피 강건 보존 |
| mean_ret_bl | −0.15% | +3.9% | |
| 앵커 사후기여@τ=0.05 | 0.440 | 0.374 | 20–50% 게이트 내(급락 아님) |

**lift 상승(+12→+16)의 귀속(중요)**: view_corr 전 구간에서 lift 가 +0.15~0.17 로 유지(0.0→+0.168,
0.6→+0.151)되어 **블록스택 off-diag 효과는 minor**이고, 상승은 **의도된 relationship-drop**(데모에서
relationship 은 잔액성장과 무관한 노이즈 축)이 지배한다. **이 raw lift 상승은 가치 개선이 아니라
*번영 추종*이다(굿하트)** — 채점이 raw 실현 잔액수익(번영)이라 공격성↑=「가만둬도 클 법인」 추종(C1 미해결).
따라서 기본은 보수값 유지, 운영값은 처치 레이어(uplift)·실데이터 재캘리로 확정.

## 6. off-diagonal 처리 — 무엇을·왜

- **형태**: $\Omega_{(a,i),(b,i)}=R^{\text{view}}_{ab}\sqrt{\Omega_{(a,i)}\Omega_{(b,i)}}$, $R^{\text{view}}$=뷰상관행렬.
- **기본값 = 표준화 신호의 경험 상관(프록시)** — task 가 지정한 "표준화 신호 상관 프록시". 데이터 적응적이며
  **매직상수 없음**(AXIS_WEIGHTS 손가중 제거 정신 계승). 직교 미래뷰는 $\rho\approx0$ 로 자동 독립계상.
- **PSD/조건수 보호**: $R^{\text{view}}$ 고유값 $[\varepsilon,1]$ 클립 → 단위대각 재정규화 → 비대각 $|\rho|\le0.98$ 클립.
  특이 $\Omega$(완전중복) 회피. demo Ω eigmin>0, 조건수 283(기본) — PSD/조건수 게이트 미파손.
- **왜 ρ=0.6을 기본으로 안 박았나**: ρ=0.6 이 데모 동등성 최적(랭킹 0.943·앵커 0.402)이나, 이는 **데모특정
  최적값**이라 박으면 합성 positive-control **과적합(굿하트)** — 본 프로젝트가 반복 경고한 안티패턴이다.
  proper redundancy(실현잔차 상관)는 **E1b(실데이터)** 보류, 손잡이(`calibrate_view_corr`)로 노출.

## 7. 정지조건 점검 (전부 미발동 → 진행)

| 정지조건 | 판정 | 근거 |
| --- | --- | --- |
| 랭킹상관 낮음 | 통과 | Spearman 0.865(기본), C3 수용 0.785 상회 |
| 앵커 기여율 급락 | 통과 | 0.440→0.374, 20–50% 게이트 내(급락=붕괴 아님); 하락분도 relationship-drop 지배 |
| 과신 징후(극단 가중) | 통과 | max wᵢ=0.10 캡유지(전 view_corr 동일), 베이스라인과 동일 |
| off-diag PSD/조건수 게이트 파손 | 통과(사전봉쇄) | $R^{\text{view}}$ 고유값·\|ρ\|≤0.98 클립이 특이 Ω 를 **구조적으로 사전 차단**(이 조건은 트립되지 않게 *설계*된 것 — 능동 감시 게이트 아님; demo Ω eigmin>0·조건수 283). 능동 κ(Ω)≤κ_max 감시는 후속(E3b) |

## 8. 검증

- `pytest`: **142 passed**(132→142). `ruff check src tests`: clean. `mypy`: clean. demo 정상(λ=0.25).
- 신규/갱신 테스트: `test_P_is_block_stack_of_identity`, `test_combined_stats_reproduce_blockstack_posterior`(블록스택≡결합단일뷰 사후 정확 일치, view_corr∈{None,0.6,0.9}), `test_offdiag_at_equal_omega_is_average_direction_invariant`(등-ω 특수케이스: q_eff=평균·off-diag는 정밀도만), `test_offdiag_shifts_direction_at_unequal_omega`(★정직: ω 상이 운영경로에선 off-diag가 방향도 조절), `test_plugin_view_increases_k_and_contributes`(K=3 + 추가뷰 사후 기여), `test_omega_psd_and_offdiagonal_present`, `test_view_registry_is_news_pattern_only`, `test_q_variance_matches_tau_sigma_unit`(블록별 단위정합), `test_calibrate_view_corr_structure`.
- **적대적 멀티에이전트 리뷰**(4차원×검증, 14발견→7확정/7기각) 반영: 동작동등 주장을 '무조건 불변'→'등-ω 특수케이스 + 기본경로 경험적'으로 정정(코드 docstring·REPORT·설계 §3/§5.2/§5.4·README), 플러그인 테스트 충실화, Q 블록별·결합등가 강화, §5.4 ω_scale·§11 c(분산정합) 문서-코드 정합, §7 PSD '사전봉쇄' 명확화, §4 비재현 스냅샷 각주.

## 9. 결론

이 작업은 **미래 뷰(갈래B 이동성 등)의 플러그인 자리를 여는 비계**이며, 현재 융합동작은 **기본 경로에서
실질 보존**된다: 작은 경험프록시 ρ 기본값에서 블록스택이 랭킹을 보존(demo Spearman 0.87)하고 off-diagonal
이 번영뷰 독립확증 과신을 상쇄한다(엄밀 방향불변은 등-ω 특수케이스, 운영 동등성은 경험적 — §3). relationship
은 $\Sigma$ 이동성 슬롯으로 예약(E1b), 뷰별 off-diag 캘리브레이션은 E3b 자리(`calibrate_view_corr`)로 노출했다.
관측된 raw lift 상승은 의도된 relationship-drop의 부수효과이자 번영 추종(굿하트)일 뿐, 운영값 확정은
uplift 채점·실데이터 재캘리 이후다.

> 다음: **E1b**(실현잔차 상관으로 off-diag proper 추정 + relationship→Σ 이동성 구조) · **E3b**(`calibrate_view_corr`
> 로 뷰별 Ω 캘리브레이션) · uplift 기반 운영값 확정.
