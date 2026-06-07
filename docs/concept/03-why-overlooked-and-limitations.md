- 문서명: Black-Litterman 개념 (3) — 왜 조명받지 못했나·한계
- 버전: v0.1
- 작성일: 2026-06-07
- 상태: Draft
- 작성주체: BL TF (테크니컬 라이터)
- 관련문서:
  - 📚 BL 개념 시리즈: [(1) 무엇이고 왜 BL인가](./01-what-is-black-litterman.md) · [(2) 수학적 구조](./02-mathematical-structure.md) · **(3) 왜 조명받지 못했나·한계** · [(4) AI 결합·설명가능성](./04-ai-augmentation-and-explainability.md)
  - 권위 설계서: [BL 모델 설계](../design/03-bl-model-design.md)

---

# BL은 왜 그동안 널리 조명받지 못했나 — 한계와 그 대응

> BL은 1991~92년에 공개됐고(1992년 *Financial Analysts Journal* 논문으로 널리 알려짐) MVO의 병폐를 우아하게 고친다. 그런데도 오랫동안 골드만삭스류 **기관 퀀트데스크의 전유물**에 가까웠고, 실무 대중은 더 단순한 휴리스틱이나 반대로 블랙박스 ML로 양분됐다. 왜일까? 이 문서는 BL이 *우아함에도 불구하고* 널리 안 쓰인 7가지 이유를 짚고, 각 한계를 **본 프로젝트 설계가 어떻게 받아내는지**를 1:1로 연결한다. (우아한 모델이 곧 채택을 보장하지 않는다는 점은, 실무 채택이 저조했던 MVO의 역사와도 동형이다.)

---

## 1. 구현 복잡성 — "직관적 닫힌형"이 아니다

He & Litterman(1999)조차 MVO 가중치를 "극단적이고 직관적이지 않다"고 했지만, BL 자신의 산출도 단순하지 않다. 역최적화 → 사전 구성 → $(\tau\Sigma)^{-1} + P^{\top}\Omega^{-1}P$ 결합 → 사후 → 제약 2차계획(QP)이라는 **다단 파이프라인**과 비자명한 선형대수(역행렬·조건수·PSD)가 필요하다. 입력도 5종($P, Q, \Omega, \tau, \Sigma$)으로 늘고 각각을 정합 단위로 맞춰야 한다. 표준 오픈소스 툴(PyPortfolioOpt 등)이 보편화된 것은 비교적 최근이라, 그 이전에는 사내 구현으로 전유됐다.

## 2. $\tau$ 논쟁 — 정하는 법에 합의가 없다

$\tau$(사전 불확실성 스케일)를 어떻게 정할지 문헌마다 처방이 충돌한다(원논문·He-Litterman·Satchell-Scowcroft·Idzorek가 제각각; Walters의 survey가 이를 연대기/분류로 정리). 다만 중요한 사실: He & Litterman(1999)은 **사후 기대수익 식에는 사실상 $\Omega/\tau$ 비(ratio)만 들어가** $\tau$ 를 따로 명시할 필요가 줄어든다고 했고, Meucci(2008)의 변형에선 $\tau$ 가 사후식에 아예 나타나지 않는다. 그래서 흔히 $0.025{\sim}0.05$ 의 작은 값으로 두거나 $\Omega$ 에 흡수시킨다.

> 주의: "$\tau$ 는 무의미"는 과장이다 — 사후 *공분산* 에는 $\tau$ 가 남는다(Spivey 2012). 정확히는 *"사후수익식 한정으로 흡수 가능"* 이다.

## 3. 가장 어려운 부분 — 뷰 $Q$, 특히 신뢰도 $\Omega$ 의 명세

Idzorek(2007)은 $\tau$ 와 $\Omega$ 를 "모델에서 가장 추상적이고 명세하기 어려운 파라미터"로 규정했다. 특히 $\Omega$ 는 "뷰가 틀릴 분산"을 사람이 손으로 숫자로 정해야 하는데 직관이 없다. 그래서 우회로가 둘 등장했다.

- **He-Litterman(1999) 표준**: $\Omega$ 의 산포를 시장 변동성·상관에서 상속 — $\Omega \propto \mathrm{diag}(P\,\tau\Sigma\,P^{\top})$ (Meucci 2008이 신뢰도 스칼라로 정리·일반화).
- **Idzorek(2007)**: 사용자가 0~100%의 직관적 confidence만 주면 그로부터 $\Omega$ 를 역산(back-solve).

이 둘은 *"$\Omega$ 를 직접 정하기 어렵다"* 는 같은 문제에서 출발한 다른 해법이다. **본 프로젝트가 $\Omega$ 를 손으로 안 쓰고 DRI·confidence로 자동 산정하고 $(P\tau\Sigma P^{\top})$ 에 단위정합시키는 설계는 정확히 이 계보 위에 있다**([설계 §5.4](../design/03-bl-model-design.md)).

## 4. 시장균형/시가총액 요구 — 비공모·B2B에서 "시장 포트폴리오"가 없다

BL의 사전은 CAPM 균형, 즉 "시가총액 가중 시장 포트폴리오를 최적으로 만드는 내재수익"이다. 유동 공모주식에는 자연스럽지만, 사모·비상장·B2B 고객처럼 관측 가능한 시가총액이 없는 유니버스에는 적용이 막힌다. Meucci(2008)도 균형 기반 사전이 "BL을 글로벌 분산펀드의 택티컬 운용으로 제한하는 것처럼 보인다"고 했다 — 단, 사후식 자체는 균형이 아닌 **임의의 정규 사전** 에도 쓸 수 있다(원저자들도 액티브 운용에선 사전 기대=0을 사용). 따라서 본 프로젝트가 $w_{mkt}$ 를 **지갑(예금)규모 비중**으로 정의하는 것은 이 "시장 포트폴리오 대용" 문제에 대한 정당한 프록시 선택이다([설계 §4.1](../design/03-bl-model-design.md)).

## 5. 여전히 $\Sigma$ 추정이 필요하다

BL은 $\mu$(기대수익) 추정의 불안정성은 완화하지만, 공분산 $\Sigma$ 는 여전히 데이터로 추정해야 한다 — 역최적화 $\Pi=\lambda\Sigma w_{mkt}$ 와 사후식이 모두 $\Sigma$ 에 의존한다. 시점 수 $T$ 가 자산 수 $N$ 보다 작으면($T < N$) 표본공분산은 특이(singular)·고조건수가 되어 $(\tau\Sigma)^{-1}$ 폭주를 유발한다. 그래서 **Ledoit-Wolf 수축이 사실상 필수**가 된다([설계 §3.2](../design/03-bl-model-design.md)). 즉 *"$\mu$ 민감성은 줄였지만 $\Sigma$ 추정오차는 그대로"* 다.

## 6. 뷰의 주관성 (garbage-in) — 좋은 뷰가 없으면 균형뿐

BL의 안전장치인 "뷰 없는 자산은 균형으로 수렴"은 동시에 한계다. 뷰가 약하거나 정보가 없으면 BL은 비싼 계산 끝에 **그냥 시장(균형) 포트폴리오를 돌려준다**. 알파는 전적으로 $Q$ 의 질에서 나오며, 인간 전망은 과신·군집 등 인지편향에 취약하고 적절한 $\Omega$ 를 매기기 어렵다(Kolm & Ritter 2021).

> **BL은 알파를 보장하지 않는다 — BL은 좋은 뷰를 일관되게 결합하는 회계장치(framework)일 뿐이다.** 본 프로젝트의 3축 AI 앙상블은 바로 이 "$Q$ 를 체계적·재현가능하게 생성"하려는 시도다(→ [(4)편](./04-ai-augmentation-and-explainability.md)).

## 7. 기관 전유 + 휴리스틱·블랙박스에 가려짐

복잡성·툴 부재·시가총액 사전 요구가 겹쳐 BL은 기관 퀀트데스크에 전유됐고, 실무 대중은 (a) 추정 입력이 거의 없어 견고한 **단순 휴리스틱**(리스크 패리티·동일가중)이나 (b) 해석가능성을 포기한 **블랙박스 ML** 로 양분됐다. BL은 "해석가능한 베이즈 구조"라는 중간지대를 점하지만, 그 우아함이 곧 채택을 보장하진 못했다.

---

## 한계 ↔ 본 설계의 대응 (요약)

| # | 한계 | 본 프로젝트 설계의 대응 | 설계 절 |
|---|---|---|---|
| 1 | 구현 복잡성 | 모듈화 파이프라인 + 정칙형 1회 Cholesky solve | [§6.3](../design/03-bl-model-design.md) |
| 2 | $\tau$ 논쟁 | $\tau=0.05$ 고정 + 민감도 격자 | [§5.5](../design/03-bl-model-design.md) |
| 3 | $\Omega$ 명세 난점 | DRI·confidence 자동 산정 + $(P\tau\Sigma P^{\top})$ 단위정합 (Meucci·Idzorek 계보) | [§5.4](../design/03-bl-model-design.md) |
| 4 | 시가총액 균형 요구 | $w_{mkt}$ = 지갑규모 프록시 (정규 사전 적용 정당화) | [§4.1](../design/03-bl-model-design.md) |
| 5 | $\Sigma$ 추정오차 | Ledoit-Wolf 수축 + 고유값바닥 + 조건수 상한 | [§3.2–3.3](../design/03-bl-model-design.md) |
| 6 | garbage-in(뷰 주관성) | 3축 AI 앙상블이 인간 뷰의 주관·편향을 줄인 체계적·재현가능 뷰로 보완 | [§5.1](../design/03-bl-model-design.md) |
| 7 | 전유·블랙박스 양분 | 오픈 파이프라인 + 설명가능 결합 레이어 | → [(4)편](./04-ai-augmentation-and-explainability.md) |

---

## 마무리

BL의 까다로운 규약들(단위 정합, 수축, 조건수 게이트, $\Omega$ 캘리브레이션)은 *장식이 아니라* 위 한계들을 정면으로 받아내기 위한 장치다. 그러나 어떤 설계도 **좋은 전망(Q)** 없이는 균형 포트폴리오 이상을 내지 못한다. 본 프로젝트의 모든 성능 기대치는 **미검증 가설**이며 walk-forward 백테스트로 검증 예정이다([설계 §12](../design/03-bl-model-design.md)).

다음: **[(4) AI 결합·설명가능성](./04-ai-augmentation-and-explainability.md)** — 바로 그 "좋은 전망"을 AI가 어떻게 체계적으로 만들고, 왜 BL이 그 AI 신호를 "설명가능하게" 결합하는 레이어인지.

---

## 참고문헌

- He, G. & Litterman, R. (1999). "The Intuition Behind Black-Litterman Model Portfolios." *Goldman Sachs Investment Management Research* (SSRN 334304).
- Black, F. & Litterman, R. (1992). "Global Portfolio Optimization." *Financial Analysts Journal*, 48(5), 28–43.
- Idzorek, T. M. (2007). "A Step-by-Step Guide to the Black-Litterman Model: Incorporating User-Specified Confidence Levels." In *Forecasting Expected Returns in the Financial Markets*, Academic Press, 17–38.
- Meucci, A. (2008). "The Black-Litterman Approach: Original Model and Extensions." SSRN 1117574.
- Walters, J. (2014). "The Black-Litterman Model in Detail." SSRN 1314585.
- Kolm, P. N. & Ritter, G. (2021). "Black-Litterman and Beyond: The Bayesian Paradigm in Investment Management." (NYU Courant working paper.)
- Spivey, M. (2012). "The Parameter Tau in Idzorek's Version of the Black-Litterman Model." (analysis note.)
