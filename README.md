<!--
문서명: BL README (진입점/인덱스)
버전: v0.3
작성일: 2026-06-07
상태: Living
관련문서: [BL 개념 시리즈](docs/concept/01-what-is-black-litterman.md) · [기획/개요](docs/planning/01-project-overview.md) · [로드맵](docs/planning/03-roadmap.md) · [용어집](docs/planning/04-glossary.md) · [BL 모델 설계](docs/design/03-bl-model-design.md)
-->

# BL — AI 기반 Black-Litterman 법인 마케팅 최적화 시스템

> 블랙-리터만(Black-Litterman) 포트폴리오 최적화 이론을 **B2B 예금유치 마케팅**에 적용하여, 한정된 영업자원을 법인 고객 "포트폴리오"에 최적 배분하는 의사결정 지원 시스템.
> 핵심 사용자: 법인 영업 마케터/RM

---

## 핵심 아이디어

| 금융공학 | → | 마케팅 재해석 |
|---|---|---|
| 자산(asset) | → | 법인 고객사 |
| 기대수익률 | → | 예금유치·유지 가치 (CLV proxy) |
| 시장 균형 가중치 $w_{mkt}$ | → | 고객 예금(지갑) 규모 비중 (BL 앵커) |
| 투자자 전망 (View, Q) | → | AI 뷰 레지스트리 블록스택 (news=Gemini 감성 · pattern=XGBoost 성장−이탈; relationship→Σ 예약, anomaly→Ω) |
| 전망 불확실성 (Ω) | → | 데이터신뢰도(DRI)·뷰별 confidence·이상도(anomaly) ($\Omega \propto (1+\gamma\,\text{anomaly})/\text{DRI}^2$) + 뷰간 off-diagonal(신호상관 프록시) |
| 최적 가중치 $w^{*}$ | → | **권장 영업자원 배분** |

> **뷰 $Q$는 뷰 레지스트리 블록스택**으로 구성한다(E3a 비계): 등록된 방향성 뷰 = news(Gemini 감성) · pattern(XGBoost 성장−이탈) 둘이며, $P=[I;I]$ (KN×N) 블록스택에 각 뷰 블록을 단위정합해 쌓는다(과거의 손가중 $a$ 단일병합 폐기). 두 뷰는 **둘 다 번영축**(기업이 잘되나)이라 상관돼 있어, 그냥 독립 2뷰로 쪼개면 같은 신호를 두 번 세어 과신(E1)을 부른다 — 그래서 $\Omega$에 **뷰간 off-diagonal(표준화 신호상관 프록시)**을 넣어 독립확증 과신을 상쇄하고 단일병합과 **동작 동등**을 유지한다(직교 미래뷰는 상관≈0으로 자동 독립계상 → 한 줄 등록으로 꽂히는 플러그인 자리). **relationship(거래관계 강도)은 방향 예측이 아니라 현재상태=이동성이므로 뷰에서 제외하고 $\Sigma$ 이동성 슬롯으로 예약**(실현 데이터=E1b 대기). **IsolationForest 이상도(anomaly)도 방향 뷰가 아니라 Ω 신뢰도 변조 요인**이다 — 이상할수록 그 법인 뷰를 앵커(prior)로 후퇴(DRI·confidence와 형제, 게인 $\gamma=2.0$).
>
> 자산은 **Tier**(T1 상장외감 / T2 비상장중소 / T3 가상섹터노드 `IS_VIRTUAL`)로 분류하고, 외부 데이터는 **Track A**(ECOS 매크로: 금리·BSI, FinanceDataReader 지수) / **Track B**(Naver 뉴스)로 수집한다 ([용어집](docs/planning/04-glossary.md)). (※ BigKinds(구 Track C)는 폐쇄적 API라 사용하지 않음)

### 핵심 수식·파라미터 (요약)

진입점 요약이며, 상세 정식화·산식은 [BL 모델 설계](docs/design/03-bl-model-design.md) 참조.

- **앵커(무위 기본값)**: $\Pi \propto \Sigma\,w_{mkt}$ — 앵커는 **지갑(예금)규모 비중 $w_{mkt}$**다($w_{hybrid}$ 아님). 스케일 $\lambda$는 **위험회피계수가 아니라 $\Pi$를 뷰 $Q$ 스케일에 맞추는 정규화 상수**($\lambda_{\text{fix}}=0.25$, 추정 대상 아님). 앵커↔뷰 균형(=영업 공격성)은 $\tau$ 하나로 조절(C3, [설계 §4.2/§5.5](docs/design/03-bl-model-design.md)).
- **사후 기대수익**: $E[R] = \big[(\tau\Sigma)^{-1} + P^{\top}\Omega^{-1}P\big]^{-1}\big[(\tau\Sigma)^{-1}\Pi + P^{\top}\Omega^{-1}Q\big]$ (FULL 행렬, Cholesky solve).
- **확정 파라미터**: $\tau = 0.05$(앵커↔뷰 손잡이; 민감도 0.025/0.05/0.1=보수/균형/공격) · $\lambda_{\text{fix}} = 0.25$($\Pi$ 스케일 정규화 상수, 앵커 사후기여 ~30%) · $w_{\max} = 0.10$ · 수익률 = 잔액 log-return.
- **수치 안정화**: 고정 reg 하드바닥 폐기 → 고유값 바닥 $\lambda_{\text{floor}} = 10^{-8}\cdot \mathrm{tr}\Sigma/N$, 조건수 상한 $\kappa_{\max} = 10^6$.

---

## Black-Litterman이란? — 처음 보는 분을 위한 개념 문서

**Black-Litterman(BL)** 은 1990년 골드만삭스에서 나온 포트폴리오 배분 이론이다. 기대수익을 직접 추정하면 결과가 입력 오차에 폭주하고 소수 자산에 쏠리는(코너 해) 평균-분산 최적화(MVO)의 고질병을, **"시장 균형을 안정적 출발점(사전)으로 두고 분석가의 전망을 베이즈로 살짝 얹는다"** 는 발상으로 길들인다.

이 프로젝트는 그 틀을 **B2B 예금유치 마케팅**에 이식한다 — 자산=법인 고객사, 시장 균형 비중=지갑(예금)규모 비중, 투자자 전망=AI 뷰 레지스트리(news·pattern 2뷰) 블록스택(이상도 anomaly는 Ω 신뢰도 변조, relationship은 Σ 예약), 최적 가중치=권장 영업자원 배분. 전망과 그 신뢰도를 **AI가 자동·재현 가능하게** 생성하고, BL은 그 신호를 **설명가능하게**(균형 vs 전망으로 분해) 결합한다.

처음 보는 분은 아래 순서로 읽으면 된다(확정 수식·파라미터의 권위 소스는 [BL 모델 설계서](docs/design/03-bl-model-design.md)).

1. [**무엇이고 왜 BL인가**](docs/concept/01-what-is-black-litterman.md) — MVO의 두 병폐 → 역최적화·베이즈 결합의 직관 (수식 최소)
2. [**정교한 수학적 구조**](docs/concept/02-mathematical-structure.md) — 사전·전망·사후, 사후식의 두 동치 표현, 차원 정합
3. [**왜 조명받지 못했나·한계**](docs/concept/03-why-overlooked-and-limitations.md) — τ 논쟁·Ω 명세 난점·시장균형 요구 등 7가지와 그 대응
4. [**AI 결합·설명가능성**](docs/concept/04-ai-augmentation-and-explainability.md) — AI가 전망 병목을 푸는 이점, BL이 "설명가능한 결합 레이어"인 이유

---

## Quickstart

```bash
pip install -e .            # 코어 설치 (GPU 가속은 옵션: pip install -e ".[gpu]")
bl-run demo                 # 합성 데이터로 전 파이프라인 실행 → site/index.html 생성 (키 불필요)
# 브라우저로 site/index.html 열기 (또는: python -m http.server -d site)
```

데모는 `data/sample/`의 소형 합성 데이터(PII 없음)로 **features → models(XGBoost·IForest) →
Black-Litterman → 마케팅 의사결정 → 대시보드**까지 전 과정을 실행합니다. `main` 푸시 시
GitHub Pages에 자동 배포됩니다(`.github/workflows/pages.yml`).

> 🔗 라이브 데모(배포 후): **https://blunderblus.github.io/black_litterman_project/**
> — ⚠️ 합성 데이터 기반 **와이어프레임**(구조/데이터 확인용, 디자인 추후 적용)

### 실데이터로 전환 (API 키만 입력하면 동일 파이프라인이 실데이터로 동작)

```bash
cp .env.example .env        # 아래 키 채우기 (BL_ 프리픽스)
#   BL_DART_API_KEY     = OpenDART 재무
#   BL_ECOS_API_KEY     = 한국은행 ECOS 매크로
#   BL_NAVER_CLIENT_ID/SECRET = Naver 뉴스
#   BL_GEMINI_API_KEY   = (선택) Gemini 감성 (없으면 규칙기반 폴백)
# 내부 소스(target_master, post_data)는 data/raw/ 에 배치(접근통제)
python -c "from bl.pipeline import run; run()"   # 키 있으면 ingest, 없으면 sample 자동 디스패치
```

`bl.pipeline.run()` 은 키 유무를 감지해 **ingest(실데이터)** 또는 **sample(합성)** 경로를 자동
선택하며, 그 이후 다운스트림은 완전히 동일합니다. 부분 키만 있으면 가능한 소스는 실데이터로,
나머지는 합성으로 graceful 대체합니다.

---

## 프로젝트 상태

**토이(Google Drive + Colab) → 클라우드 격상: 베이스 구현 완료 · GitHub 반영됨.**

- ✅ **구현·검증 완료** (전 파이프라인 오프라인 end-to-end, **132 테스트 통과**, 적대적 코드리뷰·다중에이전트 리뷰 반영):
  - P0 스캐폴드 · P1 데이터레이어(멱등 upsert·ID crosswalk·universe)
  - 피처(**DuckDB SQL** window 함수, 시점분리·누수차단) · 모델(XGBoost 성장/이탈 · IsolationForest 이상, walk-forward)
  - BL 엔진(**FULL 공분산**·사후수익·제약 최적화) · BL 입력/출력변환 · 합성 샘플데이터 · 와이어프레임 대시보드 · ingest(키 게이팅) · CI/Pages
  - **가치검증·캘리브레이션**: walk-forward 백테스트(권고 vs naive 베이스라인, 실현 잔액수익 채점, `bl.eval.backtest`) · **정책 손잡이**(τ/λ_fix/γ_anom/축가중/Ω) 역산(`bl.eval.calibrate`) · 권고 append 원장 + **uplift 채점**(`bl.serve.ledger`: 처치군 vs 자연대조군 영업효과로 raw 번영 채점 탈출, `score_ledger_uplift`)
- ✅ **API 키만 넣으면 실데이터로 동일 파이프라인 동작** (`bl.pipeline.run()` 키 감지 → ingest/sample 자동 디스패치)
- 🔜 다음: **실데이터** 라이브 검증(하니스 완비 — 합성 위 검증 통과, 실데이터만 꽂으면 동일) · 대시보드 디자인 적용(현재는 **와이어프레임**) · 운영화(스케줄·관측성)
- 격상 핵심 변경:
  - 공분산 대각 근사 → **FULL 공분산**(Ledoit-Wolf 수축) 복원
  - **pandas → DuckDB 하이브리드**: 데이터 가공(피처 window 함수·조인·ASOF)은 DuckDB SQL, ML/BL 선형대수는 NumPy/CuPy
  - **GPU 유무 = 속도만 차이** (NumPy/SciPy ↔ CuPy, CPU/GPU 상대오차 <1e-8 회귀테스트)
  - pickle 폐기 → DuckDB+Parquet · 설정·시크릿 외부화 · 데이터 누수 차단(시점분리·embargo·고정 스케일러)
  - **뉴스 감성(enrich)**: Gemini **구조화 단발 호출**(temperature=0)+콘텐츠해시 **멱등 JSON 캐시**+`pub_date` **시점 컷오프** — 재현성·누수차단(자율 에이전트 아님, confidence는 외부 산출)
  - 대시보드 246MB 인라인 JSON 폐기 → 경량 외부 `data.js` + 자기완결 HTML

자세한 격상 배경과 과거 토이의 결함 진단은 [기획/개요](docs/planning/01-project-overview.md)와 [로드맵](docs/planning/03-roadmap.md) §2 참조.

---

## 검증 (Validation)

BL 배분이 *단순 베이스라인보다 나은 결정을 내는가* 를 합성 데이터 위에서 **walk-forward 백테스트**로
측정한다(`python -m bl.eval.backtest`). 각 시점 T 에서 데이터를 ≤T 로 잘라(point-in-time, 누수차단)
권고를 산출하고 **T+3개월 실현 잔액수익**으로 채점한다(implied-vol 의 '체결가'에 대응하는 묶임줄).

| 전략 | 평균 실현수익(3m) | 실현 IR |
|---|---|---|
| **BL 권고** | **−0.1%(≈0)** | **≈0.0** |
| naive(지갑규모 비중) 베이스라인 | −12.2% | −3.06 |
| 동일가중 | −3.7% | — |

→ **BL vs naive: lift +16.1%p · win-rate 100%(12/12 윈도우) · IC +0.39 · IR +1.17 · Precision@K +0.13.**

> 위 수치는 **E2(anomaly→Ω 신뢰도 변조) · C3(앵커 λ→Π 스케일 정규화 상수) · E3a(뷰 레지스트리 블록스택) 적용 현재 코드** 기준이다. C3·E2 결합(lift +12.1%p · IC +0.32)에서 E3a로 raw lift가 +16.1%p로 오른 주원인은 **블록스택이 아니라 의도된 relationship-drop**(현재상태=이동성 축을 뷰에서 빼 Σ로 예약)이다 — 합성 데모에서 relationship은 잔액성장과 무관한 노이즈 축이라 빼면 raw lift가 오른다(분해: view_corr 0→0.6 전 구간 lift +0.15~0.17로 블록스택 자체 효과는 minor, [REPORT](REPORT.md)). **그러나 이 raw lift 상승은 *번영 추종*일 뿐 영업효과(uplift) 아님(굿하트)** — 채점이 raw 실현 잔액수익(번영)이라 공격성↑이 곧 "가만둬도 클 법인" 추종이다. **블록스택은 기본 경로(경험프록시 ρ)에서 *랭킹*을 실질 보존**(단일병합 대비 target_weight Spearman 0.87·top-5 4/5, *경험적* — 엄밀 방향불변은 per-view ω 등가일 때만, [REPORT §3](REPORT.md))하고, off-diagonal이 독립확증 과신을 상쇄해 앵커 사후기여를 20–50% 게이트 안에 유지한다(과신·극단가중 없음, win 100%). 운영값은 처치 레이어(uplift 채점)·실데이터 재캘리로 확정하며 기본은 보수값 유지.

하이퍼파라미터도 같은 백테스트의 실현지표로 **역산**한다(`python -m bl.eval.calibrate`): τ∈{0.025,0.05,0.1}
은 전 구간 win-rate 100%로 안정이라 기존값 **τ=0.05 가 near-optimal**(τ↑=공격/τ↓=보수 민감도는 [설계 §5.5](docs/design/03-bl-model-design.md)), **뷰 off-diagonal 상관 $\rho_{\text{view}}$**(`calibrate_view_corr`, E3a 블록스택의 뷰 결합 손잡이 — 과거 손가중 축그리드 대체)는 {경험프록시, 0, 0.3, 0.6, 0.9} 그리드로, **anomaly의 Ω 변조 강도 $\gamma_{\text{anom}}$**(`calibrate_gamma_anom`)은 $\gamma\in\{0,1,2,4\}$ 그리드로 역산한다. 결합 코드(E2+C3)에서도 **$\gamma=0$(lift +0.149)이 lift 최대** — *anomaly에 방향 알파가 없다는 본 설계전환(E2: anomaly를 4번째 방향 뷰에서 Ω 신뢰도 요인으로 이전)을 데이터가 확인*한다. 기본 **$\gamma=2$**(lift +0.120)는 실데이터에서 일회성 이상치(M&A 대금·세금환급 등)의 뷰를 prior(앵커)로 후퇴시키는 보수적 사전이며(win 100%·IC 유지, 단 lift·IR는 γ=0 대비 하락 — 일회성 이상치 방어 비용), 실데이터 도착 시 동일 도구로 재캘리브레이션한다.

> ⚠️ **정직한 범위**: 위 수치는 *합성 데이터의 positive-control* 이다 — 합성엔 학습가능한 성장/이탈
> 신호가 설계상 존재하므로 "기계가 신호를 잡아 naive 를 이긴다"는 검증이지 실데이터 증명은 아니다.
> 다만 검증·캘리브레이션 **하니스가 완비**되어 실데이터(`data/raw/`)를 꽂으면 동일 표가 산출되며,
> 하이퍼파라미터 default 는 합성 과적합을 피해 변경하지 않았다(실데이터 도착 시 동일 도구로 재추정).
>
> ⚠️ **번영 추종 ≠ 영업효과(채점 기준 명시)**: 위 walk-forward 검증표는 **raw 실현 잔액수익(번영)** 채점이라
> alpha/lift 는 *영업 처치효과(uplift)가 아니라 '번영 추종' 지표*다(C1 — 현 뷰는 *번영 예측*). **uplift 채점
> 레이어가 추가됨**(`serve.ledger.update_treatment`→`score_ledger_uplift`: 처치군 vs 자연대조군 uplift,
> raw `score_ledger`는 deprecated) — 단 이는 **운영 원장에 처치/대조 데이터가 쌓여야** 산출되므로, 위 표는
> 여전히 raw 채점이고 **운영 uplift 로 재해석**해야 한다. 그 전까지 공격성 상향은 보류한다.

### 영업 공격성 정책 손잡이 (보수↔공격)

공격성은 세 손잡이로 노출한다 — **τ↑ · λ_fix↓ · γ_anom↓ = 공격**(앵커↔뷰 균형). 코드: `run_from_frames`/
`run_backtest(tau=·lambda_fixed=·gamma_anom=)`, 스윕: `eval.calibrate.sweep_tau`/`sweep_lambda_fixed`/
`calibrate_gamma_anom`. 합성에선 **세 손잡이 모두 공격적일수록 raw lift/IC/IR 단조 증가**(스펙트럼 표 [REPORT.md §10](REPORT.md))인데, 이는 *번영 추종*이라 **굿하트 위험** — 그래서 **기본은 보수값 고정**(τ=0.05·λ_fix=0.25·γ_anom=2.0)하고, 운영값은 **uplift 채점(처치 레이어 추가됨, `score_ledger_uplift`)에 KB 실데이터 처치/대조가 누적된 뒤 재캘리브레이션으로 확정**한다(지금 한 점 고정 금지).

---

## 문서 구조

### 📖 개념·이론 (Black-Litterman 입문) — [`docs/concept/`](docs/concept/)

| 문서 | 내용 |
|---|---|
| [01 무엇이고 왜 BL인가](docs/concept/01-what-is-black-litterman.md) | MVO의 병폐, 역최적화·베이즈 결합 직관, 마케팅 은유 (입문) |
| [02 정교한 수학적 구조](docs/concept/02-mathematical-structure.md) | 사전/전망/사후, 정칙형↔정준형 동치, 차원 정합, 수치 안정화 |
| [03 왜 조명받지 못했나·한계](docs/concept/03-why-overlooked-and-limitations.md) | τ 논쟁·Ω 명세·시장균형 요구 등 7가지 한계와 설계 대응 |
| [04 AI 결합·설명가능성](docs/concept/04-ai-augmentation-and-explainability.md) | AI 뷰·신뢰도 자동화, 글래스박스 결합 레이어, 규제 맥락 |

### 📐 기획 및 기술문서 — [`docs/planning/`](docs/planning/)

| 문서 | 내용 |
|---|---|
| [01 프로젝트 개요·비전](docs/planning/01-project-overview.md) | 배경·문제정의, 비전/미션, 솔루션 개요, 격상 동기, 범위, 이해관계자, KPI |
| [02 요구사항 정의서 (PRD)](docs/planning/02-prd.md) | 사용자 스토리, 기능(FR)/비기능(NFR) 요구사항, 데이터 요구사항, 수용기준 |
| [03 로드맵·마이그레이션](docs/planning/03-roadmap.md) | 단계별 마일스톤(P0~P7), Colab→클라우드 이관, 리스크 관리 |
| [04 용어집](docs/planning/04-glossary.md) | BL·도메인·식별자·데이터자산·기술 용어 정의(Tier·Track 포함) |

### 🏗️ 설계서 — [`docs/design/`](docs/design/)

| 문서 | 내용 |
|---|---|
| [01 시스템 아키텍처](docs/design/01-system-architecture.md) | 논리/물리 아키텍처, 컴포넌트, 패키지 구조, 기술 스택 |
| [02 데이터 파이프라인](docs/design/02-data-pipeline.md) | 소스 인벤토리, 스키마, ID crosswalk, 시점정합·누수방지, 멱등성 |
| [03 BL 모델 설계](docs/design/03-bl-model-design.md) | 수학 정식화(full 공분산), Π/P/Q/Ω, 사후분포, 최적화, 검증 |
| [04 연산·가속(GPU/CPU) 설계](docs/design/04-compute-design.md) | NumPy/SciPy↔CuPy 백엔드 추상화, 성능, 수치 안정성 |
| [05 대시보드·배포 설계](docs/design/05-dashboard-design.md) | Quarto 단일소스, 샘플데이터, GitHub Pages, 보안/PII |

### 📋 아키텍처 결정 기록 (ADR) — [`docs/design/adr/`](docs/design/adr/)

| ADR | 결정 |
|---|---|
| [ADR-0001](docs/design/adr/ADR-0001-compute-backend.md) | 연산 백엔드: NumPy/SciPy + CuPy 단일코드 디스패치 |
| [ADR-0002](docs/design/adr/ADR-0002-storage-format.md) | 저장 포맷: DuckDB + Parquet (pickle 폐기) |
| [ADR-0003](docs/design/adr/ADR-0003-identifier-mapping.md) | 식별자: 명시적 ID crosswalk |
| [ADR-0004](docs/design/adr/ADR-0004-leakage-free-training.md) | 학습: 시점분리·누수금지 표준 |

---

## 기술 스택

- **언어**: Python 3.11+
- **데이터 가공(ETL)**: **DuckDB**(window 함수·조인·ASOF·멱등 upsert) + Parquet — 수백만 행 핫패스
- **수치/가속(수학)**: NumPy · SciPy (CPU) + CuPy (GPU 옵션) — 공분산·사후·최적화 (DuckDB와 분업)
- **최적화**: scipy.optimize (SLSQP) · cvxpy (OSQP/ECOS, 옵션)
- **ML**: XGBoost (성장/이탈) · scikit-learn Isolation Forest (이상) — walk-forward 검증
- **LLM**: Google Gemini 2.5 Flash-Lite — 뉴스 감성 **구조화 출력**(sentiment∈[-1,1]·event_type·salience, temperature=0) · 콘텐츠해시 **멱등 JSON 캐시**(재현성) · `pub_date` **시점 컷오프**(누수 차단) · 규칙기반 렉시콘 폴백(키 없을 때)
- **대시보드**: 자기완결 HTML(**와이어프레임**) + 외부 `data.js` → GitHub Pages (설계상 Quarto 단일소스 옵션)

## 리포지토리 구조

```
src/bl/
  common/   config(pydantic)·compute(NumPy↔CuPy 디스패치)·io(DuckDB/Parquet)·identifiers(crosswalk)·http·dates·logging
  synth/    합성 데모 데이터 생성기 → data/sample/
  ingest/   financial(DART)·macro(ECOS)·news(Naver)  (키 게이팅, 순수 파서 단위테스트)
  refine/ enrich/  뉴스 dedup · Gemini 구조화 감성(멱등 캐시·시점컷오프) / 규칙 폴백
  features/ DuckDB SQL 피처/라벨 + 고정 스케일러
  models/   growth_churn(XGBoost)·anomaly(IForest)·validation(walk-forward)
  engine/   covariance(Ledoit-Wolf)·inputs(Σ·Π·P·Q·Ω)·optimize(사후·QP)
  serve/    mart(§8 출력변환)·dashboard_data(JSON)·dashboard_html(와이어프레임)
  pipeline.py  run_demo(합성) / run(키 감지 디스패치)   ·   cli.py  bl-run demo
tests/      111 passing     docs/  기획·설계·ADR      data/sample/  합성 데모(44KB)      site/  빌드된 대시보드
```

---

## 데이터 식별자 주의

`corp_code`(canonical, DART) · `biz_reg_no`(사업자등록번호) · `jurir_no`(법인등록번호) · `stock_code`(상장코드)는 **서로 다른 키**다.
과거 토이에서 `biz_reg_no`를 `jurir_no`로 잘못 조인하여 데이터 99.4%가 소실된 전례가 있어, 격상판은 **명시적 ID crosswalk**를 두고 직접 조인을 금지한다 ([ADR-0003](docs/design/adr/ADR-0003-identifier-mapping.md)).

---

*문서명: BL README(진입점) · 버전 v0.3 · 작성일 2026-06-07 · 상태 Living*
