"""universe.master 테스트 — Tier 부여(숫자형/전각 안전) + TARGET_MASTER 멱등 조립."""

from __future__ import annotations

import duckdb
import pandas as pd

from bl.universe import master


def test_assign_tier_basic() -> None:
    assert master.assign_tier("005930") == "T1"
    assert master.assign_tier(None) == "T2"
    assert master.assign_tier("ABC123") == "T2"
    assert master.assign_tier("005930", is_virtual=True) == "T3"  # 가상 우선
    assert master.assign_tier("nan") == "T2"


def test_assign_tier_numeric_recovery() -> None:
    # 숫자형 종목코드의 선행 0 손실 복원: 5930 → '005930' → T1(리뷰 #8)
    assert master.assign_tier(5930) == "T1"
    assert master.assign_tier(5930.0) == "T1"
    assert master.assign_tier(660) == "T1"


def test_assign_tier_rejects_overlong_and_fullwidth() -> None:
    assert master.assign_tier("1234567") == "T2"      # 7자리(>6) 비-종목코드
    assert master.assign_tier("００５９３０") == "T2"   # 전각 숫자(리뷰 #13)


def test_build_master_frame_tiers_and_name() -> None:
    listed = pd.DataFrame({"corp_code": ["00000001"], "stock_code": ["005930"],
                           "TARGET_NAME": ["삼성전자"]})
    unlisted = pd.DataFrame({"corp_code": ["00000002"], "jurir_no": ["2222222222222"]})
    virtual = pd.DataFrame({"corp_code": ["SECTOR_G47"], "IS_VIRTUAL": [True]})
    m = master.build_master_frame([listed, unlisted, virtual])
    tiers = dict(zip(m["TARGET_ID"], m["TIER"], strict=True))
    assert tiers == {"00000001": "T1", "00000002": "T2", "SECTOR_G47": "T3"}
    assert m["TARGET_ID"].is_unique
    names = dict(zip(m["TARGET_ID"], m["TARGET_NAME"], strict=True))
    assert names["00000001"] == "삼성전자"
    assert names["00000002"] == "00000002"  # enrich 전 corp_code 대체


def test_build_target_master_idempotent() -> None:
    con = duckdb.connect(":memory:")
    cands = [
        pd.DataFrame({"corp_code": ["00000001", "00000002"], "stock_code": ["005930", None]}),
        pd.DataFrame({"corp_code": ["SECTOR_G47"], "IS_VIRTUAL": [True]}),
    ]
    n1 = master.build_target_master(con, settings=None, candidates=cands)
    n2 = master.build_target_master(con, settings=None, candidates=cands)
    assert n1 == 3 and n2 == 3
    assert con.execute('SELECT count(*) FROM "TARGET_MASTER"').fetchone()[0] == 3
    t1 = con.execute("SELECT TIER FROM \"TARGET_MASTER\" WHERE TARGET_ID = '00000001'").fetchone()[0]
    assert t1 == "T1"
