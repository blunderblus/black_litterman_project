"""ID crosswalk — 식별자 정규화. canonical key = ``corp_code`` (ADR-0003).

과거 토이 치명 결함: ``biz_reg_no``(사업자등록번호)를 ``jurir_no``(법인등록번호)와
직접 조인 → 추정 99.4% 데이터 소실(tier=UNKNOWN). 격상판은 **모든 결합을 crosswalk
경유로 corp_code 로 정규화한 뒤에만** 수행하며, biz_reg_no↔jurir_no 직접 조인을 금지한다.

P1 코드리뷰 반영:
- 숫자형(int/float) ID의 '.0' 접미사·선행 0 손실 방지(_clean_id), 대소문자 무시 NA 토큰.
- corp_code↔링크키 **양방향 1:1 위반을 탐지해 빌드 실패**(silent merge/오귀속 금지).
- 결정성(정렬 후 coalesce), 커버리지 게이트(assert_coverage), 형식 폭 가드(check_formats, warn).

설계: docs/design/02-data-pipeline.md §4, docs/planning/04-glossary.md.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import pandas as pd

from bl.common.logging import get_logger

log = get_logger(__name__)

# crosswalk 표준 컬럼. corp_code 가 canonical(허브) 키.
CROSSWALK_COLUMNS: tuple[str, ...] = ("corp_code", "biz_reg_no", "jurir_no", "stock_code")
CANONICAL_KEY = "corp_code"
LINK_KEYS: tuple[str, ...] = ("biz_reg_no", "jurir_no", "stock_code")

# 직접 조인이 금지된 위험 키 쌍(정적 점검/리뷰용).
FORBIDDEN_DIRECT_JOINS: tuple[tuple[str, str], ...] = (
    ("biz_reg_no", "jurir_no"),
    ("jurir_no", "biz_reg_no"),
)

# 식별자 표준 자릿수(형식 가드용; 체크섬 검증은 후속).
ID_DIGIT_WIDTHS: dict[str, int] = {
    "corp_code": 8,
    "biz_reg_no": 10,
    "jurir_no": 13,
    "stock_code": 6,
}

_NA_TOKENS = {"", "nan", "none", "<na>", "null", "na", "n/a", "-"}


def _clean_id(series: pd.Series) -> pd.Series:
    """식별자 컬럼을 문자열로 정규화한다.

    - 숫자형(int/float)은 정수화 후 문자열로(부동소수 '.0' 접미사 제거; 단 선행 0은 소스가
      문자열이 아니면 복원 불가하므로 **입력 계약은 문자열 권장**).
    - 공백/대소문자 무시 NA 토큰을 결측(NA)으로 만든다.
    """
    if pd.api.types.is_numeric_dtype(series):
        s = series.astype("Int64").astype("string")
    else:
        s = series.astype("string").str.strip()
        s = s.str.replace(r"\.0$", "", regex=True)  # 문자열화된 float의 '.0' 제거
    return s.mask(s.str.lower().isin(_NA_TOKENS))


def check_formats(xwalk: pd.DataFrame, on_fail: str = "warn") -> list[str]:
    """식별자 자릿수 형식을 점검한다. on_fail='raise'면 위반 시 예외, 'warn'이면 경고만.

    숫자형 소스에서 선행 0이 소실되면 자릿수가 어긋날 수 있어 기본은 경고(캘리브레이션 전).
    """
    issues: list[str] = []
    for col, width in ID_DIGIT_WIDTHS.items():
        if col not in xwalk.columns:
            continue
        vals = xwalk[col].dropna().astype("string")
        bad = vals[~vals.str.fullmatch(rf"[0-9]{{{width}}}")]
        if len(bad):
            issues.append(f"{col}: {len(bad)}건이 {width}자리 숫자 형식 위반(예: {list(bad[:3])})")
    if issues:
        msg = "식별자 형식 위반 — " + " | ".join(issues)
        if on_fail == "raise":
            raise ValueError(msg)
        log.warning(msg, extra={"stage": "identifiers.check_formats"})
    return issues


def build_crosswalk(sources: "Sequence[pd.DataFrame]") -> pd.DataFrame:
    """여러 소스에서 corp_code 중심의 ID crosswalk 테이블을 구성한다.

    corp_code 를 가진 소스만 앵커로 사용한다. corp_code 별로 링크 키의 비결측값을 coalesce하되,
    **양방향 1:1 위반(같은 corp_code에 복수 링크값 / 같은 링크값에 복수 corp_code)은 빌드 실패**.

    Returns: 컬럼 = CROSSWALK_COLUMNS, corp_code NOT NULL·UNIQUE.
    Raises: ValueError (앵커 소스 없음 / 1:1 위반).
    """
    frames: list[pd.DataFrame] = []
    for src in sources:
        if CANONICAL_KEY not in src.columns:
            continue
        present = [c for c in CROSSWALK_COLUMNS if c in src.columns]
        f = src[present].copy()
        for c in present:
            f[c] = _clean_id(f[c])
        frames.append(f)
    if not frames:
        raise ValueError(f"crosswalk 구성에는 '{CANONICAL_KEY}'를 가진 소스가 최소 1개 필요합니다.")

    allrows = pd.concat(frames, ignore_index=True).dropna(subset=[CANONICAL_KEY])
    # 결정성: 모든 컬럼 기준 정렬 후 coalesce
    allrows = allrows.sort_values(list(allrows.columns), na_position="last").reset_index(drop=True)

    # forward 충돌: 같은 corp_code에 서로 다른 비결측 링크값
    forward = []
    for c in LINK_KEYS:
        if c in allrows.columns:
            g = allrows.dropna(subset=[c]).groupby(CANONICAL_KEY)[c].nunique()
            bad = g[g > 1]
            if len(bad):
                forward.append(f"{c}: {len(bad)}개 corp_code(예 {list(bad.index[:3])})")
    if forward:
        raise ValueError("corp_code 1:N 링크 충돌 — " + " | ".join(forward))

    xwalk = allrows.groupby(CANONICAL_KEY, as_index=False).first()

    # reverse 충돌: 같은 링크값이 복수 corp_code (99.4% 소실 유형)
    reverse = []
    for c in LINK_KEYS:
        if c in xwalk.columns:
            g = xwalk.dropna(subset=[c]).groupby(c)[CANONICAL_KEY].nunique()
            bad = g[g > 1]
            if len(bad):
                reverse.append(f"{c}→복수 corp_code: {len(bad)}건(예 {list(bad.index[:3])})")
    if reverse:
        raise ValueError("역방향 ID 충돌 — " + " | ".join(reverse))

    for c in CROSSWALK_COLUMNS:
        if c not in xwalk.columns:
            xwalk[c] = pd.array([pd.NA] * len(xwalk), dtype="string")
    out = xwalk[list(CROSSWALK_COLUMNS)].reset_index(drop=True)
    check_formats(out, on_fail="warn")
    return out


def normalize_to_corp_code(
    df: "pd.DataFrame",
    on: str,
    crosswalk: "pd.DataFrame",
) -> pd.DataFrame:
    """``on`` 키(biz_reg_no/jurir_no/stock_code)를 crosswalk로 corp_code 로 정규화한다.

    crosswalk를 경유하므로 직접 조인 금지 규약을 위반하지 않는다.
    매칭 실패 행의 corp_code 는 NA(UNKNOWN)로 남기고 커버리지는 호출부에서 게이트한다.

    Raises: ValueError (on 부적합 / crosswalk가 on→corp_code 1:1 아님).
    """
    if on == CANONICAL_KEY:
        return df.copy()
    if on not in LINK_KEYS:
        raise ValueError(f"on 은 {LINK_KEYS} 중 하나여야 합니다(받은 값: {on!r}).")
    if on not in crosswalk.columns:
        raise ValueError(f"crosswalk 에 '{on}' 컬럼이 없습니다.")

    mapping = crosswalk[[on, CANONICAL_KEY]].copy()
    mapping[on] = _clean_id(mapping[on])
    mapping = mapping.dropna(subset=[on])
    multi = mapping.groupby(on)[CANONICAL_KEY].nunique()
    if (multi > 1).any():
        raise ValueError(
            f"crosswalk가 {on}→corp_code 1:1이 아닙니다(예 {list(multi[multi > 1].index[:3])})."
        )
    mapping = mapping.drop_duplicates(subset=[on])

    out = df.copy()
    out[on] = _clean_id(out[on])
    return out.merge(mapping, on=on, how="left")


def unknown_ratio(df: "pd.DataFrame", key: str = CANONICAL_KEY) -> float:
    """정규화 실패(키 결측) 비율을 반환한다(0.0~1.0). 빈 df면 0.0."""
    if len(df) == 0:
        return 0.0
    return float(df[key].isna().mean())


def assert_coverage(
    df: "pd.DataFrame",
    max_unknown_ratio: float,
    key: str = CANONICAL_KEY,
    on_fail: str = "raise",
) -> float:
    """식별자 정규화 커버리지 게이트(ADR-0003 회귀 가드). unknown 비율 반환.

    unknown 비율이 임계를 넘으면 on_fail='raise'면 예외, 'warn'이면 경고만(캘리브레이션 전).
    """
    r = unknown_ratio(df, key)
    if r > max_unknown_ratio:
        msg = f"식별자 커버리지 미달: unknown {r:.2%} > 임계 {max_unknown_ratio:.2%} (key={key})"
        if on_fail == "raise":
            raise ValueError(msg)
        log.warning(msg, extra={"stage": "identifiers.assert_coverage"})
    return r
