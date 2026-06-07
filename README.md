<!--
문서명: BL README (진입점/인덱스)
버전: v0.2
작성일: 2026-06-07
상태: Draft
관련문서: [기획/개요](docs/planning/01-project-overview.md) · [로드맵](docs/planning/03-roadmap.md) · [용어집](docs/planning/04-glossary.md) · [BL 모델 설계](docs/design/03-bl-model-design.md)
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
| 투자자 전망 (View, Q) | → | AI 4축 신호 앙상블 (news=Gemini 감성 · pattern=XGBoost 성장/이탈 · anomaly=IsolationForest 이상 · relationship=거래관계 강도) |
| 전망 불확실성 (Ω) | → | 데이터신뢰도(DRI)·모델 confidence ($\Omega \propto 1/\text{DRI}^2$) |
| 최적 가중치 $w^{*}$ | → | **권장 영업자원 배분** |

> **AI 모델 3종(XGBoost · IsolationForest · Gemini) + 거래관계(relationship) 축 = 4축으로 Q를 결합**한다. 축 가중 $a$ = (news 0.35, pattern 0.35, anomaly 0.15, relationship 0.15), 합=1. relationship은 계좌수·급여이체·주거래 등 거래관계 강도다.
>
> 자산은 **Tier**(T1 상장외감 / T2 비상장중소 / T3 가상섹터노드 `IS_VIRTUAL`)로 분류하고, 외부 데이터는 **Track A**(ECOS 매크로: 금리·BSI, FinanceDataReader 지수) / **B**(Naver 뉴스) / **C**(BigKinds 뉴스)로 수집한다 ([용어집](docs/planning/04-glossary.md)).

### 핵심 수식·파라미터 (요약)

진입점 요약이며, 상세 정식화·산식은 [BL 모델 설계](docs/design/03-bl-model-design.md) 참조.

- **앵커(역최적화)**: $\Pi = \lambda\,\Sigma\,w_{mkt}$ — 앵커는 **지갑(예금)규모 비중 $w_{mkt}$**다($w_{hybrid}$ 아님; $w_{hybrid}$는 최적화 초기값·턴오버 기준으로만 사용).
- **사후 기대수익**: $E[R] = \big[(\tau\Sigma)^{-1} + P^{\top}\Omega^{-1}P\big]^{-1}\big[(\tau\Sigma)^{-1}\Pi + P^{\top}\Omega^{-1}Q\big]$ (FULL 행렬, Cholesky solve).
- **확정 파라미터(가설값 포함)**: $\tau = 0.05$(민감도 0.025/0.05/0.1) · $\lambda$ = 캘리브레이션(출발 기본값 2.5, 클립 $[1,5]$) · $w_{\max} = 0.10$ · 수익률 = 잔액 log-return.
- **수치 안정화**: 고정 reg 하드바닥 폐기 → 고유값 바닥 $\lambda_{\text{floor}} = 10^{-8}\cdot \mathrm{tr}\Sigma/N$, 조건수 상한 $\kappa_{\max} = 10^6$.

---

## 프로젝트 상태

**토이(Google Drive + Colab) → 프로덕션(클라우드) 격상 진행 중.**

- 현재 단계: **기획·설계 문서화** (git 미개설 · 베이스 코드 구축 전)
- 다음 단계(로드맵 Phase와 정렬, P0~P7): **P0** 베이스 코드 구축·`git init` → **P1~P2** 데이터 레이어 이관·ID crosswalk → **P3** 피처·모델 재구현 → **P4** BL 입력·최적화 재구현 → **P5~P6** 합성 샘플데이터·GitHub Pages 데모 → **P7** 운영화(스케줄·관측성·회귀테스트)
- 격상 핵심 변경:
  - Colab 속도 제약으로 희생됐던 **공분산 대각 근사 → FULL 공분산**(Ledoit-Wolf 수축) 복원
  - **GPU 유무 = 속도만 차이** (NumPy/SciPy ↔ CuPy 단일코드 디스패치, 수치 동일 · CPU/GPU 상대오차 <1e-8 회귀테스트)
  - pickle 폐기 → **DuckDB + Parquet**, 설정·시크릿 외부화, 데이터 누수 차단
  - 대시보드 버전 난립(44개 HTML·709MB) → 파라미터화 단일 소스 + 데이터 HTML 분리

자세한 격상 배경과 과거 토이의 결함 진단은 [기획/개요](docs/planning/01-project-overview.md)와 [로드맵](docs/planning/03-roadmap.md) §2 참조.

---

## 문서 구조

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
- **저장**: DuckDB (수집/OLAP) + Parquet (분석/교환)
- **수치/가속**: NumPy · SciPy (CPU) + CuPy (GPU 옵션)
- **최적화**: cvxpy (OSQP/ECOS) · scipy.optimize (SLSQP)
- **ML**: XGBoost (성장/이탈) · scikit-learn Isolation Forest (이상)
- **LLM**: Google Gemini 2.5 Flash-Lite (뉴스 감성 ∈[-1,1] + 검증셋 캘리브레이션 confidence, 하드코딩 금지)
- **대시보드**: Quarto + Plotly → GitHub Pages 정적 배포

---

## 데이터 식별자 주의

`corp_code`(canonical, DART) · `biz_reg_no`(사업자등록번호) · `jurir_no`(법인등록번호) · `stock_code`(상장코드)는 **서로 다른 키**다.
과거 토이에서 `biz_reg_no`를 `jurir_no`로 잘못 조인하여 데이터 99.4%가 소실된 전례가 있어, 격상판은 **명시적 ID crosswalk**를 두고 직접 조인을 금지한다 ([ADR-0003](docs/design/adr/ADR-0003-identifier-mapping.md)).

---

*문서명: BL README(진입점) · 버전 v0.2 · 작성일 2026-06-07 · 상태 Draft*
