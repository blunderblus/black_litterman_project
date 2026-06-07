- 문서명: ADR-0002 저장 포맷 결정
- 번호: ADR-0002
- 제목: 저장을 DuckDB + Parquet로 표준화하고 pickle 폐기
- 버전: v0.3
- 작성일: 2026-06-07
- 상태: Accepted
- 결정자: BL 아키텍트
- 관련문서: [시스템 아키텍처](../01-system-architecture.md), [데이터 파이프라인](../02-data-pipeline.md), [ADR-0001 연산 백엔드](./ADR-0001-compute-backend.md), [ADR-0003 식별자 매핑](./ADR-0003-identifier-mapping.md), [ADR-0004 누수금지 학습](./ADR-0004-leakage-free-training.md)

## 맥락(Context)

BL의 데이터 파이프라인은 universe(TARGET_MASTER) → ingest(재무 DART / macro ECOS / news Naver) → refine → enrich(Gemini 감성) → features → models → bl_inputs → bl_optimize → serve의 단계로 구성된다. 각 단계는 정형 테이블(재무·매크로·피처·BL 입력)과 중간 산출물(학습된 스케일러·모델·캘리브레이션 파라미터)을 저장·교환해야 한다.

과거 토이 프로젝트는 Colab/Drive 환경에서 다음과 같이 저장을 수행했고, 여러 결함이 누적되었다.

- 수집·적재는 DuckDB를 이미 사용했고(`raw_collection.duckdb`에 TARGET_MASTER/RAW_FINANCIAL/FINANCIAL_WIDE), 이는 ASOF JOIN·멱등 upsert 등 강점이 있었다. 이 강점은 보존 대상이다.
- 그러나 단계 간 중간 산출물(전처리 결과, 스케일러, 모델 등)을 **pickle(.pkl)** 로 직렬화해 Drive에 흩뿌리는 패턴이 있었다. pickle은 다음 문제를 갖는다.
  - **보안**: pickle 역직렬화는 임의 코드 실행(arbitrary code execution)이 가능하다. 신뢰되지 않은 .pkl 로드는 원격코드실행 위험을 그대로 노출한다.
  - **버전 취약성**: pickle은 Python/라이브러리(예: scikit-learn, numpy) 버전에 강결합된다. 버전이 바뀌면 로드가 깨지거나 조용히 잘못 복원되어, 재현성과 장기 보존이 보장되지 않는다.
  - **상호운용성 결여**: pickle은 Python 전용이라 DuckDB/대시보드/외부 도구가 직접 읽을 수 없다.
  - **운영 산란·버전 깨짐**: `.pkl`을 Drive 곳곳에 흩뿌리는 패턴은 쓰기 자체는 한 줄로 쉬웠으나, 산출물이 출처·버전·스키마 추적 없이 산란되어 운영 지속성(어떤 .pkl이 최신·유효한지 식별, 재현·승계)이 사실상 무너졌다. 즉 "쓰기 편의"가 높았을 뿐 "운영 지속성"은 낮았다.
- 또한 추론배치에서 **min/max로 [-1,1] 정규화**(학습 기준이 아니라 실행 시점 데이터로) 하는 누수가 있었는데, 이는 스케일러를 "안전하고 버전 안정적으로 저장·재사용"하는 수단이 없었던 것과도 맞물린다. 단, 이 누수를 실제로 차단하는 규약(고정 스케일러 fit-on-train + 추론 재계산 금지 가드)은 [ADR-0004 누수금지 학습](./ADR-0004-leakage-free-training.md)의 책임이며, 본 ADR은 그 규약이 의존하는 "안전·버전안정 저장 수단"을 제공한다.

격상판은 클라우드 기반·헤드리스 실행·재현성·보안을 요구하므로, 저장 포맷을 표준화하고 pickle을 제거할 필요가 있다. 더하여 본 프로젝트가 다루는 데이터는 소매금융기관이 보유한 **법인 고객(예금) 데이터**로 기밀·규제 민감도가 높아, 데이터의 외부 반출(egress)·관할/규제 측면도 저장·인프라 선택의 1급 제약이다.

## 결정(Decision)

저장 계층을 다음으로 표준화한다.

1. **DuckDB**: 수집/적재/OLAP의 운영 저장소. RAW 적재, ASOF JOIN, 멱등 upsert, ID crosswalk(별도 ADR-0003), 마트성 쿼리를 담당한다. 멱등 적재는 추상어가 아니라 구체 패턴 `INSERT OR REPLACE`(테이블·키별 매핑은 [02 데이터 파이프라인 §6.2](../02-data-pipeline.md) 준수, 본 ADR이 아닌 02가 단일 진실원천)를 사용한다.
2. **Parquet**: 분석·교환·단계 간 산출물의 표준 컬럼형 포맷. 피처 테이블, BL 입력($\Sigma$, $\Pi$, $P$, $Q$, $\Omega$, $w$), 대시보드용 데이터셋을 Parquet으로 직렬화한다. 행렬형 산출물($\Sigma$/$P$/$\Omega$)은 자산순서 인덱스와 함께 사이드카(`bl_sigma.parquet`·`bl_P.parquet`·`bl_omega.parquet`)로 저장한다([02 §3.2.2](../02-data-pipeline.md)). 스키마·dtype이 파일에 내장되어 버전 안정적이고, DuckDB가 네이티브로 직접 쿼리한다.
3. **pickle 전면 폐기**: 어떤 단계에서도 `.pkl`을 산출물·교환 포맷으로 사용하지 않는다. 이진 직렬화 경로를 기본 산출물로 다시 들이지 않는 것이 본 결정의 핵심이다.
   - 데이터프레임/배열 → Parquet.
   - 스케일러·캘리브레이션 등 "파라미터로 표현 가능한" 객체 → 파라미터를 JSON/Parquet로 명시 저장하고 변환은 코드로 재구성. 본 프로젝트의 표준 스케일러는 **train 구간 fit한 StandardScaler**(표준화)이며, 저장 스키마는 다음을 명시한다.
     - StandardScaler: `{ "scaler_type": "standard", "feature_order": [...], "mean": [...], "scale": [...], "clip": [low, high]?, "scaler_version": "..." }`
     - (불가피하게 MinMax가 필요한 피처는 명시 예외로만) MinMaxScaler: `{ "scaler_type": "minmax", "feature_order": [...], "data_min": [...], "data_max": [...], "feature_range": [-1, 1], "scaler_version": "..." }`
     - `scaler_version`은 [02 §5.3](../02-data-pipeline.md)의 `scaler_version` 컬럼과 정합되며, 학습/추론이 동일 파라미터를 참조함을 추적한다. 과거 누수였던 "추론 시점 min/max 재계산"은 여기서 금지되며(저장된 파라미터를 그대로 적용), 적용·차단 가드의 의무화는 [ADR-0004 §4·§6](./ADR-0004-leakage-free-training.md)이 규정한다.
   - ML 모델(XGBoost) → 라이브러리 네이티브·이식 가능 포맷(예: XGBoost JSON/UBJ 부스터 덤프)로 저장.
   - scikit-learn IsolationForest 등 native 직렬화가 마땅치 않은 경우: **1차 권장은 재학습 재현**(모델 재현 파라미터 + 학습 데이터 스냅샷 Parquet)으로 산출물 자체를 재생성 가능하게 보장한다. 재학습이 비현실적인 경우에 한해, **신뢰된 산출물에 한정**하여 `skops`(`skops.io`) 류 직렬화를 조건부 fallback으로 허용한다. 다만 `skops`는 "안전 직렬화"가 아니라 **신뢰 타입 allowlist + `get_untrusted_types` 검사에 의존해 pickle 대비 위험을 줄이는** 부분적 안전장치이며, 커스텀 추정기·신뢰되지 않은 객체 로드 시 코드실행 위험이 잔존한다. 따라서 `skops` 사용은 (a) 자체 생성·서명·통제된 산출물에만, (b) 로드 전 `get_untrusted_types()` 검사 통과를 게이트로 두고, (c) 신뢰되지 않은 출처의 로드는 금지하는 조건에서만 인정한다.

원자성·재현성을 위해 파일 쓰기는 임시파일 후 원자적 교체(atomic rename) 패턴을, 테이블 적재는 `INSERT OR REPLACE` 기반 멱등 upsert([02 §6.2](../02-data-pipeline.md))를 사용한다.

## 근거(Rationale)

DuckDB는 단일 파일·임베디드 OLAP으로 운영 단순성과 강력한 분석 쿼리를 동시에 제공하며, Parquet을 1급 시민으로 직접 읽고 쓴다. Parquet은 컬럼형·스키마 내장·압축·언어중립으로 분석/교환/장기보존에 적합하다. 둘의 결합은 "운영(DuckDB) + 교환·분석(Parquet)"이라는 역할분담을 자연스럽게 만든다.

또한 임베디드(DuckDB) 선택은 **데이터 거버넌스** 측면에서 강화된다: 소매금융기관 법인 고객(예금) 데이터를 외부 SaaS DW로 반출하면 데이터 egress·관할/규제(금융 데이터 보관 위치·접근통제) 리스크가 커지는데, 임베디드 저장은 데이터가 통제된 경계 내에 머물게 하여 이 리스크를 회피한다.

대안 비교(아래 "보안" 축은 **역직렬화 RCE 위험**에 한정하며, 데이터 반출/규제 리스크는 별도 행으로 분리):

| 기준 | DuckDB + Parquet (채택) | pickle 유지 | CSV | 외부 DW(예: BigQuery/Snowflake) |
|---|---|---|---|---|
| 보안: 역직렬화 RCE 위험 | 없음 | 높음(역직렬화 RCE) | 없음 | 없음 |
| 데이터 반출·규제(법인 금융데이터 egress) | 낮음(임베디드, 경계 내) | 낮음(로컬) | 낮음(로컬) | **높음(외부 반출·관할/규제)** |
| 버전 안정성·장기보존 | 높음(스키마 내장) | 낮음(런타임 강결합) | 중간(타입 손실) | 높음 |
| 스키마·dtype 보존 | 우수 | 우수하나 위험 | 약함(타입 메타 미보존→소비측 재추론, float 정밀도·날짜/정수 라운드트립 손실) | 우수 |
| OLAP·조인(ASOF/upsert) | 네이티브 | 불가 | 불가 | 우수 |
| 언어중립·도구 상호운용 | 높음 | 없음(Python 전용) | 높음 | 높음 |
| 쓰기 편의 | 높음 | 높음 | 높음 | 중간(클라이언트 필요) |
| 운영 지속성(버전·산란 관리) | 높음 | **낮음(버전 강결합·.pkl 산란)** | 중간(스키마 부재) | 높음 |
| 인프라·무서버 운영 단순성 | 높음(임베디드) | 높음 | 높음 | 낮음(인프라·비용) |
| 비용·종속성 | 낮음 | 낮음 | 낮음 | 높음(벤더 종속) |
| 대용량·압축 효율 | 높음(컬럼형) | 낮음 | 낮음 | 높음 |

pickle은 쓰기 편의 외에는 보안·버전·상호운용·운영 지속성 모두에서 열위이며, 본 프로젝트가 명시적으로 제거를 요구한 항목이다. CSV는 단순하나 타입 메타 미보존(소비측 재추론 필요)·float 정밀도·날짜/정수 라운드트립 손실과 대용량 비효율로 BL 입력 행렬·재무 수치 저장에 부적합하다. 외부 DW는 분석력은 강하나 인프라·비용·벤더 종속에 더해, 법인 고객 금융데이터의 외부 반출에 따른 보안·규제 리스크가 커서 합성 샘플데이터로 GitHub Pages에 데모를 배포하는 본 프로젝트의 경량성·기밀성 요구에 과하다(향후 운영 규모 확장·온프레미스/전용 환경 확보 시 재평가 여지).

## 결과(Consequences)

긍정적:
- 역직렬화 RCE 위험 제거 및 버전 안정적 재현성 확보.
- 법인 금융데이터를 임베디드 경계 내에 유지하여 외부 반출·규제 리스크 최소화.
- DuckDB가 Parquet을 직접 쿼리하므로 적재→분석 경로가 단순하고, ASOF JOIN·`INSERT OR REPLACE` 멱등 upsert·이중적재 lineage 같은 기존 강점을 보존·강화.
- 학습 기준 고정 스케일러를 파라미터(StandardScaler `mean`/`scale` 등)로 **안전·버전안정적으로 저장할 수단을 제공**하여, [ADR-0004 누수금지 학습](./ADR-0004-leakage-free-training.md)의 "고정 스케일러 + 추론 재계산 금지"에 의한 재정규화 누수 차단을 **가능케 한다**(차단 규약 자체는 ADR-0004의 책임이며, 본 ADR은 저장 수단만 보장한다).
- 컬럼형·압축으로 대시보드 데이터 분리(HTML에서 외부 JSON/Parquet 분리)와 빌드 산출물 경량화에 기여.

부정적:
- 기존 `.pkl` 산출물에 의존하던 노트북·코드를 마이그레이션해야 한다(일회성 비용).
- IsolationForest처럼 native 직렬화가 빈약한 모델은 "재학습 재현"(1차) 또는 통제 환경의 조건부 `skops` fallback(2차)을 설계해야 해 추가 비용이 든다.
- Parquet 스키마 진화(컬럼 추가/타입 변경) 관리 규약이 필요하다(전반 규약은 [02 §6.4](../02-data-pipeline.md)에 정의됨).
- **DuckDB 동시성 한계**: 단일 파일 DuckDB는 쓰기 동시성(단일 writer 잠금)에 제약이 있어, 다중 프로세스·동시 서빙 시 읽기전용 서버 모드 또는 읽기 복제본 전략이 필요할 수 있다([01 시스템 아키텍처 §12 오픈이슈 3](../01-system-architecture.md): "DuckDB 단일 파일 vs 읽기전용 서버 모드"와 연계).

후속작업:
- [ ] 파이프라인 전 단계에서 `.pkl` 입출력 제거 및 Parquet/DuckDB로 치환.
- [ ] 스케일러·캘리브레이션 파라미터의 JSON/Parquet 스키마(StandardScaler `mean`/`scale`/`feature_order`/`scaler_version`) 정의 및 학습 시 저장·추론 시 로드 경로 구현(고정 스케일러).
- [ ] XGBoost 모델 JSON/UBJ 저장·로드 표준화. IsolationForest는 재학습 재현(1차) 또는 통제 환경 한정 조건부 `skops` fallback(`get_untrusted_types` 게이트) 설계.
- [ ] 원자적 파일 쓰기 + `INSERT OR REPLACE` 멱등 upsert 유틸리티 정비, 이중적재 lineage(RAW_FINANCIAL + FINANCIAL_WIDE) 규칙 문서화.
- [x] Parquet 스키마 버전·진화 일반 규약은 [02 §6.4](../02-data-pipeline.md)에 정의됨(`schema_version` 메타키·nullable 추가·버전 디렉터리 분리). 잔여: BL 사이드카 행렬 Parquet(`bl_sigma`/`bl_P`/`bl_omega`)의 **자산순서 인덱스 진화 규약**(자산 추가/제거·정렬 변경 시 행·열 정합)만 별도 정의 필요.
- [ ] DuckDB 단일 파일의 쓰기 동시성 한계 검토 및 읽기전용 서버 모드/복제본 전략 결정([01 §12 오픈이슈 3](../01-system-architecture.md) 연계).

## 대안(Considered Alternatives)

- **pickle 유지**: 가장 쉬운 경로이나 임의코드실행·버전 강결합·Python 전용·.pkl 산란이라는 결함이 격상판의 보안·재현성·운영지속성 요구와 정면 충돌. 명시적 폐기 대상이므로 기각.
- **CSV 표준화**: 사람이 읽기 쉽고 범용적이나 타입 메타 미보존(소비측 재추론), float 정밀도·날짜/정수 라운드트립 손실, 대용량 비효율, 스키마 부재로 재무·BL 행렬 저장에 부적합. 보조 내보내기 용도로만 한정 허용.
- **외부 데이터웨어하우스(BigQuery/Snowflake 등)**: 강력한 분석·확장성이 있으나 인프라·비용·벤더 종속이 크고, 무엇보다 **법인 고객 금융데이터의 외부 SaaS 반출에 따른 보안·규제(관할) 리스크**가 커서 기밀성·정적 배포 중심의 현 단계에는 과도. 운영 규모 확장·전용/온프레미스 환경 확보 시 ADR 갱신으로 재검토.
