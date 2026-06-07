- 문서명: Black-Litterman 개념 (2) — 정교한 수학적 구조
- 버전: v0.1
- 작성일: 2026-06-07
- 상태: Draft
- 작성주체: BL TF (테크니컬 라이터)
- 관련문서:
  - 📚 BL 개념 시리즈: [(1) 무엇이고 왜 BL인가](./01-what-is-black-litterman.md) · **(2) 수학적 구조** · [(3) 왜 조명받지 못했나·한계](./03-why-overlooked-and-limitations.md) · [(4) AI 결합·설명가능성](./04-ai-augmentation-and-explainability.md)
  - 권위 설계서(확정 수식·파라미터·검증): [BL 모델 설계](../design/03-bl-model-design.md)
  - 용어: [기획 04 용어집](../planning/04-glossary.md)

---

# Black-Litterman의 정교한 수학적 구조

> 본 문서는 BL을 **베이즈 추정 문제**로 엄밀히 전개한다. [(1)편](./01-what-is-black-litterman.md)의 직관을 식으로 옮기고, 본 프로젝트가 채택한 **확정 표기**(권위 소스: [설계 §3–§6](../design/03-bl-model-design.md))를 그대로 노출한다. 수식 자체보다 *"각 항이 어떤 차원이고 왜 그렇게 생겼는가"* 가 핵심이다.

## 0. 표기 약속

자산 수를 $N$, 전망(view) 수를 $K$ 라 한다. 모든 수익은 **잔액 log-return 기준**으로 본다(무위험수익 $r_f$ 는 $\lambda$ 캘리브레이션에만 반영; [설계 §2.2](../design/03-bl-model-design.md)).

| 기호 | 차원 | 의미 |
|---|---|---|
| $\Sigma$ | $N \times N$ | 자산수익률 공분산 (FULL, Ledoit-Wolf 수축) |
| $w_{mkt}$ | $N$ | 시장 균형 비중 (본 프로젝트: 지갑규모 비중) |
| $\lambda$ | 스칼라 | 시장 위험회피계수 |
| $\Pi$ | $N$ | 내재 균형(사전) 기대수익 |
| $\tau$ | 스칼라 | 사전 불확실성 스케일 |
| $P$ | $K \times N$ | 선택행렬 (어느 자산에 대한 전망인가) |
| $Q$ | $K$ | 전망값 벡터 |
| $\Omega$ | $K \times K$ | 전망 불확실성 (대각) |
| $E[R]$ | $N$ | 사후 기대수익 |
| $\Sigma_{post}$ | $N \times N$ | 사후 공분산 |
| $w^{*}$ | $N$ | 제약 최적화 해 |

---

## 1. 사전분포: 역최적화로 얻는 균형수익 $\Pi$

### 1.1 MVO 1차 조건을 거꾸로

위험회피 $\lambda$ 의 평균-분산 효용을 최대화하는 문제

$$
\max_{w}\; \mu^{\top}w - \frac{\lambda}{2}\,w^{\top}\Sigma w
$$

의 1차 조건은 $\mu - \lambda\Sigma w = 0$, 즉 $\mu = \lambda\Sigma w$ 다. 시장이 균형이어서 $w = w_{mkt}$ 가 최적이라고 *가정* 하면, 그 균형을 만들어내는 내재 기대수익이 곧 사전 평균이다.

$$
\boxed{\;\Pi = \lambda\,\Sigma\,w_{mkt}\;}
$$

이것이 **역최적화(reverse optimization)** 다. 노이즈가 큰 직접 추정치 $\hat\mu$ 대신, 균형이 함의하는 $\Pi$ 를 사전으로 쓴다.

### 1.2 사전분포와 $\tau$

균형수익 $\Pi$ 자체도 불확실하다. BL은 이 불확실성을 $\tau\Sigma$ 로 모형화한다 — 즉 **참값 $E[R]$ 의 사전분포**는

$$
E[R] \sim \mathcal{N}\!\left(\Pi,\ \tau\Sigma\right).
$$

$\tau$ 는 "균형수익을 얼마나 신뢰하는가"를 재는 작은 스칼라다($\tau$ 가 작을수록 사전을 강하게 신뢰). 공분산 구조는 $\Sigma$ 를 재사용하되 스케일만 $\tau$ 로 줄인다. 본 프로젝트 기본값은 $\tau = 0.05$ 이며 민감도 격자 $\{0.025, 0.05, 0.1\}$ 로 점검한다([설계 §5.5](../design/03-bl-model-design.md)). $\tau$ 의 표준값에는 문헌상 합의가 없다(→ [(3)편 §τ 논쟁](./03-why-overlooked-and-limitations.md)).

### 1.3 $\lambda$ 의 캘리브레이션

$\lambda$ 는 시장 위험프리미엄을 시장분산으로 나눠 캘리브레이션한다.

$$
\lambda = \frac{E[r_{mkt}] - r_f}{\sigma_{mkt}^{2}},\qquad
r_{mkt} = w_{mkt}^{\top} r,\quad \sigma_{mkt}^{2} = w_{mkt}^{\top}\Sigma\,w_{mkt}.
$$

본 프로젝트는 시작 기본값 $\lambda = 2.5$ 에서 출발해 위 식으로 실측하고 $\lambda \in [1, 5]$ 로 클립한다([설계 §4.2](../design/03-bl-model-design.md)).

---

## 2. 전망분포: $P,\ Q,\ \Omega$

분석가(또는 AI)의 전망은 세 객체로 표현된다.

### 2.1 선택행렬 $P$ — 어느 자산에 거는가

$P \in \mathbb{R}^{K \times N}$ 의 각 행이 하나의 전망이다.

- **절대뷰(absolute view)**: "자산 $i$ 의 수익은 $q$ 다." 해당 행은 원-핫 $e_i^{\top}$. AI 앙상블 전망 대부분이 여기 해당하므로, 절대뷰만 쓰면 $P$ 는 (전망 있는 자산에 대한) 항등 부분행렬이 된다.
- **상대뷰(relative view)**: "섹터 A가 섹터 B보다 우수." 행 가중치 합이 0이다(예: 성장상위군 $+w$, 하위군 $-w$).
- 전망이 없는 자산은 $P$ 에 행이 없다 → 사후가 자동으로 $\Pi$ 로 수렴.

### 2.2 전망값 $Q$ 와 불확실성 $\Omega$

$Q \in \mathbb{R}^{K}$ 는 각 전망의 기대값이고, $\Omega \in \mathbb{R}^{K \times K}$ 는 전망의 **불확실성 공분산**이다. 표준 구현에서 $\Omega$ 는 **대각**(전망 간 독립 가정)이며,

- $\Omega_{kk}$ 가 **클수록** 그 전망을 덜 믿는다(사후가 균형에 가깝게 남음).
- $\Omega_{kk} \to 0$ 은 "그 전망을 절대 확신"(사후가 그 전망을 정확히 따름).

핵심 제약은 **단위 정합**이다. $Q$ 와 $\Omega$ 는 $\tau\Sigma$ 와 같은 수익률² 차원을 가져야 베이즈 결합이 의미를 갖는다. 본 프로젝트의 과거 토이판은 $Q \approx 0.01$ 인데 $\Omega \approx 17$ 인 단위 부정합으로 전망이 사실상 무력화됐고, 격상판이 이를 교정한다([설계 §5.2·§5.4](../design/03-bl-model-design.md), → [(3)편](./03-why-overlooked-and-limitations.md)).

---

## 3. 사후분포: 베이즈 결합

### 3.1 정칙형 (precision form) — 본 프로젝트 채택형

사전 $\mathcal{N}(\Pi, \tau\Sigma)$ 와 전망 $\mathcal{N}(Q, \Omega)$(선택행렬 $P$ 경유)를 베이즈 결합하면, 사후 기대수익은 **정밀도(precision, 역공분산) 가중 합**으로 나온다.

$$
\boxed{\;E[R] = \Big[(\tau\Sigma)^{-1} + P^{\top}\Omega^{-1}P\Big]^{-1}\Big[(\tau\Sigma)^{-1}\Pi + P^{\top}\Omega^{-1}Q\Big]\;}
$$

구조를 읽으면: 사후수익은 *"사전의 정밀도로 가중된 사전 $\Pi$"* 와 *"전망의 정밀도로 가중된 전망 $Q$"* 의 가중 평균이다. 전망을 확신할수록($\Omega^{-1}$ 큼) 그 전망 쪽으로 끌린다.

### 3.2 정준형 (canonical form) — He & Litterman(1999)

동일한 사후수익을 다음 형태로도 쓸 수 있다.

$$
E[R] = \Pi + \tau\Sigma P^{\top}\Big[P\,\tau\Sigma\,P^{\top} + \Omega\Big]^{-1}\big(Q - P\Pi\big).
$$

이 형태는 베이즈 업데이트의 의미를 투명하게 드러낸다 — 사전 $\Pi$ 에서 출발해 **전망 잔차 $(Q - P\Pi)$**(전망이 균형과 얼마나 다른가)만큼만 보정한다.

> **두 식은 근사가 아니라 정확히 동치다.** Sherman–Morrison–Woodbury 행렬 항등식으로 §3.1 ↔ §3.2 가 대수적으로 같음을 보일 수 있다. 결과 $E[R]$ 은 동일하다. 본 프로젝트는 **정칙형(§3.1)** 을 채택하는데, $(\tau\Sigma)^{-1} + P^{\top}\Omega^{-1}P$ 가 정밀도의 합산 구조라 한 번의 선형해(Cholesky solve)로 풀기 편하기 때문이다(→ §5).

### 3.3 사후 공분산

추정 불확실성까지 반영한 사후 공분산은

$$
M = \Big[(\tau\Sigma)^{-1} + P^{\top}\Omega^{-1}P\Big]^{-1},\qquad
\Sigma_{post} = \Sigma + M.
$$

$M$ 은 사후 평균 추정의 불확실성, $\Sigma_{post}$ 는 그것까지 더한 보수적 위험 추정이다. 본 프로젝트의 제약 최적화는 기본적으로 $\Sigma_{post}$ 를 쓴다([설계 §6.2](../design/03-bl-model-design.md)).

### 3.4 두 극단 — 왜 BL이 "안전"한가

- **전망이 없으면** ($K = 0$, 또는 $\Omega^{-1} \to 0$): $E[R] \to \Pi$. 사후가 균형으로 수렴 → 코너 해 억제. *이것이 BL의 안전장치다.*
- **전망을 절대 확신하면** ($\Omega \to 0$): 해당 전망 자산에서 $E[R] \to Q$. 사후가 전망을 정확히 따른다.

대부분의 현실은 그 사이 어딘가이며, $\Omega$ 가 "균형과 전망 사이 어디에 설 것인가"를 연속적으로 조율한다.

---

## 4. 본 프로젝트의 구체화

위 일반식을 B2B 예금유치에 이식할 때, 각 객체는 다음으로 채워진다(엄밀 정의·캘리브레이션은 [설계서](../design/03-bl-model-design.md)가 권위 소스).

| 객체 | 본 프로젝트의 구체화 | 설계 절 |
|---|---|---|
| $\Sigma$ | 고객 잔액증가율(log-return) **FULL 표본공분산 + Ledoit-Wolf 수축**, PSD/조건수 게이트 | §3 |
| $w_{mkt}$ | 지갑(예금)규모 비중 (재무=cash_amount, 비재무=섹터 중앙값 배수 추정) | §4.1 |
| $\Pi$ | $\lambda\Sigma w_{mkt}$ (지갑규모 앵커, $w_{hybrid}$ 아님) | §4 |
| $Q$ | 뷰 3축(news/pattern/relationship) AI 신호 앙상블 $Q_{final}=a^{\top}\tilde s$, 가중 $a=(0.412, 0.412, 0.176)$ | §5.1–5.2 |
| $\Omega$ | $\Omega_{kk}=(P\tau\Sigma P^{\top})_{kk}\cdot \dfrac{1}{\mathrm{DRI}_i^{2}}\cdot \dfrac{1-\mathrm{conf}_i}{c_{cal}}\cdot (1+\gamma_{\text{anom}}a_i)$ (단위정합 × 무차원 신뢰가중; anomaly는 Ω 신뢰도 변조 요인, $\gamma_{\text{anom}}=2.0$), 하한 $\Omega_{floor}$ | §5.4 |

두 가지가 BL 표준의 **도메인 변형**임을 명시한다.

1. **$w_{mkt}$ = 지갑규모 비중** — 시가총액 기반 "시장 포트폴리오"를 정의할 수 없는 B2B 영역에서, 지갑규모를 균형 비중의 프록시로 택한다. 사후식은 임의의 정규 사전에도 적용 가능하므로 이 치환은 정당하다(Meucci 2008; → [(3)편](./03-why-overlooked-and-limitations.md)).
2. **$\Omega \propto 1/\mathrm{DRI}^2$** — 표준 He-Litterman의 $\Omega = \mathrm{diag}(P\tau\Sigma P^{\top})$(Meucci 2008이 일반화)에, 데이터신뢰도(DRI)·모델 confidence 기반 **무차원 신뢰 가중**을 곱한 변형이다. Idzorek(2007)의 "직관적 신뢰수준 → $\Omega$ 사상" 문제의식을 잇되, 그의 confidence 역산(back-solve)과 달리 base $\Omega$ 에 가중을 곱하는 단순화다(→ [(4)편](./04-ai-augmentation-and-explainability.md)).

---

## 5. 수치적으로 어떻게 푸는가

이론식은 역행렬 $(\cdot)^{-1}$ 로 가득하지만, **역행렬을 직접 만들면 안 된다**(수치 폭주의 원인). 실제 계산은 다음 원칙을 따른다([설계 §6.3](../design/03-bl-model-design.md), [04 연산 설계](../design/04-compute-design.md)).

- **선형해로 대체**: $(\tau\Sigma)^{-1}\Pi$ 같은 항은 역행렬 대신 $\tau\Sigma\,x = \Pi$ 를 **Cholesky로 푼다**($x$ 가 답).
- **공분산 정칙화**: $\Sigma$ 는 Ledoit-Wolf 수축 + 고유값 바닥 $\lambda_{floor} = 10^{-8}\cdot \mathrm{tr}\Sigma / N$ + 조건수 상한 $\kappa_{max} = 10^{6}$ 으로 안정화. 과거의 하드 바닥 `reg=1e-6` 은 폐기.
- **사후 검증**: $E[R]$ 분포가 정상 범위 안에 있는지 자동 점검(이탈 시 빌드 실패). 과거 토이판의 사후수익 폭주($E[R]_{max}=1.29$, 균형 평균의 약 125배)를 차단한다.
- **백엔드 동치**: CPU(NumPy/SciPy) ↔ GPU(CuPy) 결과 상대오차 $< 10^{-8}$ 회귀테스트. "동일 로직, 속도만 차이."

---

## 6. 한눈 정리

$$
w_{mkt} \xrightarrow{\ \Pi=\lambda\Sigma w_{mkt}\ } \Pi
\quad\oplus\quad (P, Q, \Omega)
\quad\xrightarrow{\ \text{Bayes}\ }\quad
E[R],\ \Sigma_{post}
\quad\xrightarrow{\ \text{제약 QP}\ }\quad w^{*}
$$

- 사후수익 = **정밀도 가중(사전 $\Pi$, 전망 $Q$)**. 정칙형과 정준형은 동치.
- 전망 없음 → 균형 수렴(안전), 전망 확신 → 전망 추종.
- 본 프로젝트는 정칙형 + Cholesky solve + 수축/게이트로 *폭주 없이* 푼다.

다음: **[(3) 왜 조명받지 못했나·한계](./03-why-overlooked-and-limitations.md)** — 이 우아한 구조가 왜 널리 안 쓰였는지, 그리고 그 한계를 본 설계가 어떻게 받아내는지.

---

## 참고문헌

- Black, F. & Litterman, R. (1992). "Global Portfolio Optimization." *Financial Analysts Journal*, 48(5), 28–43.
- He, G. & Litterman, R. (1999). "The Intuition Behind Black-Litterman Model Portfolios." *Goldman Sachs Investment Management Research* (SSRN 334304).
- Idzorek, T. M. (2007). "A Step-by-Step Guide to the Black-Litterman Model: Incorporating User-Specified Confidence Levels." In *Forecasting Expected Returns in the Financial Markets*, Academic Press, 17–38.
- Meucci, A. (2008). "The Black-Litterman Approach: Original Model and Extensions." SSRN 1117574.
- Walters, J. (2014). "The Black-Litterman Model in Detail." SSRN 1314585.
- Ledoit, O. & Wolf, M. (2004). "Honey, I Shrunk the Sample Covariance Matrix." *The Journal of Portfolio Management*, 30(4), 110–119.
