"""ID crosswalk 테스트 — canonical=corp_code, 충돌 탐지·정규화(ADR-0003)."""

from __future__ import annotations

import pandas as pd
import pytest

from bl.common import identifiers as ids


def test_build_crosswalk_coalesces() -> None:
    s1 = pd.DataFrame({"corp_code": ["00000001", "00000002"], "biz_reg_no": ["1112233334", None]})
    s2 = pd.DataFrame({
        "corp_code": ["00000001", "00000002"],
        "jurir_no": ["1111111111111", "2222222222222"],
        "stock_code": ["005930", None],
    })
    xw = ids.build_crosswalk([s1, s2])
    assert set(xw.columns) == set(ids.CROSSWALK_COLUMNS)
    assert xw["corp_code"].is_unique
    row1 = xw[xw["corp_code"] == "00000001"].iloc[0]
    assert row1["biz_reg_no"] == "1112233334"
    assert row1["jurir_no"] == "1111111111111"
    assert row1["stock_code"] == "005930"


def test_build_crosswalk_requires_corp_code() -> None:
    with pytest.raises(ValueError):
        ids.build_crosswalk([pd.DataFrame({"biz_reg_no": ["1112233334"]})])


def test_build_crosswalk_forward_conflict_raises() -> None:
    # 같은 corp_code에 서로 다른 jurir_no → 1:N 충돌 빌드 실패(리뷰 #5)
    s = pd.DataFrame({"corp_code": ["00000001", "00000001"], "jurir_no": ["1111111111111", "9999999999999"]})
    with pytest.raises(ValueError):
        ids.build_crosswalk([s])


def test_build_crosswalk_reverse_conflict_raises() -> None:
    # 같은 biz_reg_no가 복수 corp_code → 역방향 충돌(99.4% 소실 유형) 빌드 실패(리뷰 #6)
    s = pd.DataFrame({"corp_code": ["00000001", "00000002"], "biz_reg_no": ["1112233334", "1112233334"]})
    with pytest.raises(ValueError):
        ids.build_crosswalk([s])


def test_clean_id_numeric_strips_dot_zero() -> None:
    # float ID의 '.0' 접미사 제거(리뷰 #7)
    out = ids._clean_id(pd.Series([126380.0, None]))
    assert out.iloc[0] == "126380"
    assert pd.isna(out.iloc[1])


def test_clean_id_na_tokens_case_insensitive() -> None:
    # 대소문자 무시 확장 NA 토큰(리뷰 #10)
    out = ids._clean_id(pd.Series(["NaN", "NULL", "N/A", "-", "00000001"]))
    assert list(out.isna()) == [True, True, True, True, False]


def test_normalize_to_corp_code_maps_and_marks_unknown() -> None:
    xw = pd.DataFrame({
        "corp_code": ["00000001", "00000002"],
        "biz_reg_no": ["1112233334", "2223344445"],
        "jurir_no": [None, None], "stock_code": [None, None],
    })
    df = pd.DataFrame({"biz_reg_no": ["1112233334", "9999999999"], "amount": [10, 20]})
    out = ids.normalize_to_corp_code(df, on="biz_reg_no", crosswalk=xw)
    assert out.loc[out["biz_reg_no"] == "1112233334", "corp_code"].iloc[0] == "00000001"
    assert pd.isna(out.loc[out["biz_reg_no"] == "9999999999", "corp_code"].iloc[0])
    assert ids.unknown_ratio(out) == 0.5


def test_normalize_rejects_non_link_key() -> None:
    xw = pd.DataFrame({"corp_code": ["00000001"], "biz_reg_no": ["1112233334"]})
    with pytest.raises(ValueError):
        ids.normalize_to_corp_code(pd.DataFrame({"x": [1]}), on="x", crosswalk=xw)


def test_normalize_on_canonical_is_noop() -> None:
    xw = pd.DataFrame({"corp_code": ["00000001"], "biz_reg_no": ["1112233334"]})
    df = pd.DataFrame({"corp_code": ["00000001"], "v": [1]})
    out = ids.normalize_to_corp_code(df, on="corp_code", crosswalk=xw)
    assert list(out["corp_code"]) == ["00000001"]


def test_assert_coverage_gate() -> None:
    df_ok = pd.DataFrame({"corp_code": ["00000001", "00000002"]})
    assert ids.assert_coverage(df_ok, 0.05) == 0.0
    df_bad = pd.DataFrame({"corp_code": ["00000001", None]})
    with pytest.raises(ValueError):
        ids.assert_coverage(df_bad, 0.05)
    # warn 모드는 예외 없이 비율 반환
    assert ids.assert_coverage(df_bad, 0.05, on_fail="warn") == 0.5
