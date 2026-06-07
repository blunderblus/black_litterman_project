- 문서명: ADR-0001 수치 가속 백엔드 결정
- 번호: ADR-0001
- 제목: 수치 가속 백엔드로 NumPy/SciPy(CPU) + CuPy(GPU 옵션) 단일코드 디스패치 채택
- 버전: v0.2
- 작성일: 2026-06-07
- 상태: Draft
- 결정자: BL 아키텍트
- 관련문서: [시스템 아키텍처](../01-system-architecture.md), [BL 모델 설계](../03-bl-model-design.md), [연산 설계](../04-compute-design.md), [ADR-0002 저장 포맷](./ADR-0002-storage-format.md)

## 맥락(Context)

BL은 Black-Litterman(블랙리터만) 포트폴리오 이론을 B2B 예금유치 마케팅에 적용하는 의사결정 지원 시스템이다. 핵심 연산은 다음과 같은 선형대수·수치최적화로 구성된다.

- 공분산 행렬 $\Sigma$ 추정 및 Ledoit-Wolf 수축(shrinkage)
- BL 사후수익 $E[R] = [(\tau\Sigma)^{-1} + P^{\top}\Omega^{-1}P]^{-1}[(\tau\Sigma)^{-1}\Pi + P^{\top}\Omega^{-1}Q]$ 계산[^solve]
- 최적 가중치 산출을 위한 convex QP(2차계획) 풀이

[^solve]: 위 사후식은 **닫힌형(closed-form) 표기**일 뿐이며, 실제 계산은 **직접 역행렬을 생성하지 않는다**. 격상판은 $(\tau\Sigma)^{-1}$ 등을 명시적으로 만들지 않고 **Cholesky 분해 후 전후치환(solve)** 으로 선형해를 구한다(상세 [03 §6.3](../03-bl-model-design.md), [04 §4.2·§7.2](../04-compute-design.md)). 직접 역행렬은 과거 폭주($E[R]_{\max}=1.29$)의 한 축이었으므로 격상판에서 **금지**된다.

원래 이 프로젝트는 Google Drive + Colab 무료 플랜에서 동작하는 토이 프로젝트였다. Colab의 런타임·메모리·속도 제약 때문에 다음과 같은 치명적 단순화가 일어났다.

- **As-is 실제 결함**: 공분산을 전체(full)로 쓰지 못하고 `np.diag(S)`로 **대각 성분만** 사용했다. 그 결과 자산(법인 고객) 간 상관을 통한 분산효과(diversification)가 완전히 소실되어, BL이 전제하는 공분산 구조 자체가 무력화되었다. 이는 "포트폴리오 최적화"라는 본 시스템의 정체성을 사실상 부정하는 수준의 결함이다. 또한 같은 과거 코드에서 균형수익 앵커가 시장비중 $w_{\text{mkt}}$가 아니라 $w_{\text{hybrid}}$였던 결함도 함께 있었으나, 이는 본 ADR(백엔드)의 범위 밖이며 BL 모델 설계([03 §0·§4](../03-bl-model-design.md))에서 $\Pi=\lambda\Sigma w_{\text{mkt}}$ 앵커로 교정된다.
- 동시에 `reg=1e-6` 같은 하드 바닥(floor)으로 조건수(condition number)를 강제 봉합하다 보니, 사후수익 $E[R]$이 최대 1.29까지 폭주하는 비정상 현상이 관측되었다.

이제 프로젝트는 **클라우드(충분한 하드웨어 가정)** 로 격상되었다. 핵심 요구는 두 가지다.

1. Colab 제약으로 희생된 부분의 복원: 특히 **FULL 공분산** 정상화(대각 → 전체).
2. GPU 유무에 따라 연산자원 활용만 달라지고, **로직과 수치 결과는 동일**하게 유지(속도만 차이). 즉 GPU가 있으면 가속하되, 없어도 동일한 결과를 재현해야 한다(연구·검증·회귀 테스트의 결정성 보장).

사용자 다수는 비기술직(법인 영업 마케터/RM)이며, 배포 환경은 GPU가 보장되지 않는다. 따라서 "GPU 필수" 스택은 채택할 수 없고, "CPU 기본 + GPU 선택적 가속"이 요구된다.

## 결정(Decision)

수치 가속 백엔드로 **NumPy + SciPy(CPU 기준) + CuPy(GPU 옵션)** 조합을 채택하고, **배열 모듈 디스패치(array module dispatch)** 패턴으로 단일 코드베이스에서 백엔드를 전환한다.

- 배열 백엔드는 런타임에 결정한다: `xp = cupy if (gpu_available and settings.use_gpu) else numpy`. 연산 코드는 `xp` 추상화에만 의존하여 한 번만 작성한다. 설정 플래그 `use_gpu`(또는 `BL_COMPUTE_BACKEND=cpu`)로 **CPU를 강제**할 수 있으며, 이 CPU 강제 경로가 회귀·재현성 검증의 **기준 경로(reference path)** 다([04 §3.3](../04-compute-design.md)).
- **GPU 가용성은 3단 자동감지**한다(① `import cupy` → ② `cuda.runtime.getDeviceCount() ≥ 1` → ③ 디바이스에서 테스트 연산 1회 + synchronize). **어느 단계든 실패하면 무중단(seamless) CPU 폴백**하며, 폴백 경로도 `float64` 고정·동일 알고리즘으로 수치 일치 계약을 동일하게 충족한다(상세 [04 §3.1~§3.4](../04-compute-design.md)). 단, `BL_COMPUTE_BACKEND=gpu`(강제 모드)에서 감지 실패 시에는 조용한 폴백 대신 명시적 실패(fail-fast)한다.
- 선형대수 일반 연산은 NumPy/CuPy의 호환 API(`xp.linalg.*`)를 사용한다. CPU 전용 고급 루틴(예: Ledoit-Wolf, 일부 SciPy 전용 분해)은 SciPy로 처리하되, GPU 경로에서는 입력을 CPU로 내려 동일 알고리즘을 적용하거나 CuPy 대응 루틴으로 1:1 매핑한다(매핑표 [04 §2.3](../04-compute-design.md)).
- 최적화 솔버는 역할을 구분한다([03 §7.3](../03-bl-model-design.md)): **cvxpy(OSQP/ECOS)** 는 **볼록 QP**(최소분산·턴오버패널티)의 표준이고, **`scipy.optimize`(SLSQP)** 는 **비볼록 비율형**(최대 Sharpe·IR)에 한정한다. QP 솔버 자체는 GPU 가속 대상이 아니며(희소·반복형, CPU 솔버가 성숙), GPU는 **분해/solve/GEMM 등 선형대수 핫스팟**에만 적용한다([04 §4.4](../04-compute-design.md)).
- **수치 일치 계약(numerical parity contract)**: CPU/GPU 경로는 **상대오차 `rtol < 1e-8`** 내에서 동일 결과를 보장하며 회귀 테스트로 강제한다(같은 입력 → 같은 출력). 이 계약은 $\Sigma$·$E[R]$·$w^*$ 등 **모든 핵심 산출물**에 동일하게 적용된다([04 §8.4 골든 테스트](../04-compute-design.md) `assert_allclose(..., rtol=1e-8)` 준수). 단, 이 `rtol < 1e-8` 계약은 **선형대수 핵심 연산**(분해·solve·GEMM·eigh) 기준이고, **반복형 QP 솔버(OSQP/ECOS)** 에는 솔버 자체의 수렴 허용오차(solver tolerance)가 별도로 적용된다(반복 솔버는 비트수준 동일을 보장하지 않음).
- dtype은 `float64`를 기본으로 한다(분해·조건수 안정성 우선). GPU에서 속도를 위해 `float32`를 쓰지 않는다(수치 일치 계약 위반 방지). **단, 확정 산출물이 아닌 민감도 탐색·프리뷰 용도에 한해** 설정 플래그(`BL_ALLOW_FP32_PREVIEW`, 기본 `false`)로 `float32`를 조건부 허용하되, **확정 결과는 항상 `float64`로 재계산**한다([04 §5.2](../04-compute-design.md)).

## 근거(Rationale)

NumPy/SciPy는 Python 수치연산의 사실상 표준이며, CuPy는 NumPy/SciPy API와 거의 1:1 호환(drop-in)을 목표로 설계되어 "단일 코드 디스패치"가 가장 자연스럽다. 동일 알고리즘·동일 `float64` dtype을 명시적으로 유지할 수 있어 "동일 로직·동일 수치, 속도만 차이"라는 격상 요구(`rtol < 1e-8` 수치 일치 계약 포함)를 최소 비용으로 충족한다. 아래 비교표가 채택 근거의 핵심이며, 기각 대안의 상세 논거는 [대안(Considered Alternatives)](#대안considered-alternatives)으로 분담한다.

대안 비교:

| 기준 | NumPy/SciPy + CuPy 디스패치 (채택) | PyTorch | JAX |
|---|---|---|---|
| CPU 단독 동작(GPU 미보장 환경) | 우수(NumPy 기본) | 가능하나 무거움 | 가능하나 무거움 |
| 기존 코드(np 기반) 마이그레이션 비용 | 낮음(API 호환) | 중간(텐서 의미론 차이) | 중간~높음(함수형·순수성 제약) |
| CPU/GPU 수치 결정성·동일성(`rtol<1e-8`) | 높음(동일 알고리즘, float64 기본) | XLA/누적순서 차이 가능 | XLA·기본 float32 등 차이 가능 |
| SciPy 생태계(Ledoit-Wolf, SLSQP, sklearn) 연계 | 직접 연계 | 별도 변환 필요 | 별도 변환 필요 |
| convex QP(cvxpy/OSQP) 연계 | 자연스러움 | 간접 | 간접 |
| 자동미분 필요성 | 본 프로젝트 불필요 | 강점(불필요한 기능) | 강점(불필요한 기능) |
| 학습난이도(비기술직 인근 운영) | 낮음 | 중간 | 높음(함수형 사고) |
| 의존성·배포 무게 | 가벼움 | 무거움(CUDA 런타임 동봉) | 무거움 |

요지: 본 프로젝트 연산은 닫힌형(closed-form) 선형대수와 convex QP가 핵심이라 자동미분이 불필요하고, NumPy↔CuPy는 동일 알고리즘·동일 dtype을 명시 유지하여 `rtol < 1e-8` 수치 일치 계약을 가장 쉽게 충족한다. PyTorch/JAX를 기각한 상세 사유는 다음 절에 정리한다.

## 결과(Consequences)

긍정적:
- FULL 공분산 복원이 자연스럽다. 메모리·속도 제약이 사라졌고, GPU가 있으면 **Cholesky 분해·삼각치환(solve)·행렬곱(GEMM)** 등 $O(N^3)$/$O(N^2T)$ 핫스팟을 가속해 대규모 유니버스에서도 전체 공분산 BL을 실시간에 가깝게 처리한다. 격상판은 **직접 역행렬을 금지**([03 §6.3](../03-bl-model-design.md)·[04 §6.3](../04-compute-design.md))하고 GPU는 분해/solve/GEMM 핫스팟만 가속한다(역행렬 생성 자체를 가속 대상으로 두지 않는다).
- 단일 코드베이스로 CPU/GPU를 모두 지원하여 분기 유지보수 비용이 낮다. 3단 자동감지·무중단 CPU 폴백으로 GPU 미보장 배포환경에서도 코드 변경 없이 동일 결과를 재현한다.
- float64 기본 + 수치 일치 계약(`rtol < 1e-8`)으로 검증·재현성이 확보된다(과거 "실행마다 결과가 달라지는" 류의 비결정 문제 차단에 기여).

부정적:
- CuPy는 CUDA 런타임/드라이버 버전에 민감하다. GPU 환경 구성 문서화와 버전 핀(pin)이 필요하다. 버전 미스매치는 3단 감지의 "테스트 연산" 단계에서 조기 검출되어 CPU 폴백으로 흡수된다([04 §3.1·§9](../04-compute-design.md)).
- 일부 SciPy 전용 루틴은 CuPy 대응이 없어 GPU 경로에서 CPU 폴백이 발생한다(해당 구간은 가속 이득이 없음). 어떤 연산이 GPU/CPU에 매핑되는지 명세가 필요하다([04 §2.3](../04-compute-design.md)).
- `xp` 추상화를 깨는 라이브러리 호출(예: numpy 전용 함수 직접 호출)이 섞이면 디스패치가 무너진다. 코드 규약·린트로 방지해야 한다.

후속작업:
- [ ] `compute` 백엔드 모듈에 `get_backend()`(3단 GPU 감지·폴백) 및 `asarray()/asnumpy()`(호스트↔디바이스 경계) 헬퍼 구현.
- [ ] FULL 공분산 + Ledoit-Wolf 수축 경로를 CPU/GPU 양쪽에서 구현하고 수치 일치 회귀 테스트 추가(`rtol < 1e-8`).
- [ ] CPU/GPU 동일 입력에 대한 BL 사후수익 $E[R]$·최적가중 $w^*$ 골든 테스트(golden test) 작성(`assert_allclose(..., rtol=1e-8)` — [04 §8.4](../04-compute-design.md)).
- [ ] GPU/CPU 매핑 표(어떤 연산이 어느 백엔드로 가는지)와 CUDA 버전 핀을 [연산 설계](../04-compute-design.md)에 문서화.
- [ ] dtype을 float64로 강제하는 가드와, 우발적 numpy 직접 호출을 잡는 정적 점검 도입.

## 대안(Considered Alternatives)

- **PyTorch 채택**: GPU 가속과 풍부한 생태계가 장점이나 자동미분이 불필요하고, 기본 dtype·연산 누적순서·XLA로 인해 CPU/GPU 간 비트수준에 가까운 일치(`rtol < 1e-8`)를 보장하기 어렵다. 텐서 의미론으로의 마이그레이션 비용과 CUDA 런타임 무게도 부담. 본 프로젝트의 "동일 로직·동일 수치, 속도만 차이" 원칙과 충돌하여 기각.
- **JAX 채택**: XLA 기반 고성능·함수형 변환(jit/vmap)이 매력적이나, 기본 float32·XLA 컴파일로 인한 결과 차이, 함수형 순수성 제약, 가파른 학습곡선이 운영·검증 비용을 높인다. closed-form BL 연산에 과한 도구로 판단하여 기각.
- **순수 NumPy만(가속 미지원)**: 단순하지만 FULL 공분산·대규모 유니버스에서 GPU 가속 기회를 포기. 격상 취지(충분한 하드웨어 활용)에 어긋나 기각. 단, CuPy 미설치 환경에서는 사실상 이 경로(CPU 폴백)로 동작하므로 호환은 유지된다.
