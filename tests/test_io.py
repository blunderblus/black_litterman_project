"""io.upsert 멱등성·계약 테스트 (DuckDB in-memory)."""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from bl.common.io import upsert


def _con():
    return duckdb.connect(":memory:")


def test_upsert_creates_and_inserts() -> None:
    con = _con()
    n = upsert(con, "T", pd.DataFrame({"k": ["a", "b"], "v": [1, 2]}), keys=["k"])
    assert n == 2
    assert con.execute('SELECT count(*) FROM "T"').fetchone()[0] == 2


def test_upsert_idempotent_rerun() -> None:
    con = _con()
    df = pd.DataFrame({"k": ["a", "b"], "v": [1, 2]})
    upsert(con, "T", df, keys=["k"])
    upsert(con, "T", df, keys=["k"])
    assert con.execute('SELECT count(*) FROM "T"').fetchone()[0] == 2


def test_upsert_updates_existing_key() -> None:
    con = _con()
    upsert(con, "T", pd.DataFrame({"k": ["a"], "v": [1]}), keys=["k"])
    upsert(con, "T", pd.DataFrame({"k": ["a"], "v": [99]}), keys=["k"])
    assert con.execute('SELECT v FROM "T" WHERE k = ?', ["a"]).fetchall() == [(99,)]


def test_upsert_composite_key() -> None:
    con = _con()
    upsert(con, "T", pd.DataFrame({"a": [1, 1], "b": ["x", "y"], "v": [10, 20]}), keys=["a", "b"])
    upsert(con, "T", pd.DataFrame({"a": [1], "b": ["x"], "v": [11]}), keys=["a", "b"])
    assert dict(con.execute('SELECT b, v FROM "T" ORDER BY b').fetchall()) == {"x": 11, "y": 20}


def test_upsert_empty_df_noop() -> None:
    assert upsert(_con(), "T", pd.DataFrame({"k": [], "v": []}), keys=["k"]) == 0


def test_upsert_missing_key_raises() -> None:
    with pytest.raises(ValueError):
        upsert(_con(), "T", pd.DataFrame({"v": [1]}), keys=["k"])


def test_upsert_rejects_null_key() -> None:
    # NULL 키는 EXISTS 매칭 실패로 멱등성을 깨므로 거부(리뷰 #1)
    with pytest.raises(ValueError):
        upsert(_con(), "T", pd.DataFrame({"k": ["a", None], "v": [1, 2]}), keys=["k"])


def test_upsert_rejects_intra_df_duplicate_key() -> None:
    # df 내부 중복 키는 PK 유일성을 깨므로 거부(리뷰 #2)
    with pytest.raises(ValueError):
        upsert(_con(), "T", pd.DataFrame({"k": ["a", "a"], "v": [1, 2]}), keys=["k"])


def test_upsert_within_caller_transaction_preserves_other_data() -> None:
    # 외부 tx 안에서 호출해도 호출자의 기존 적재를 abort시키지 않음(리뷰 #3)
    con = _con()
    con.execute("BEGIN TRANSACTION")
    con.execute("CREATE TABLE other(x INTEGER)")
    con.execute("INSERT INTO other VALUES (42)")
    upsert(con, "T", pd.DataFrame({"k": ["a"], "v": [1]}), keys=["k"], in_transaction=True)
    con.execute("COMMIT")
    assert con.execute("SELECT x FROM other").fetchall() == [(42,)]
    assert con.execute('SELECT v FROM "T"').fetchall() == [(1,)]


def test_upsert_allna_object_column_preserves_string_ids() -> None:
    # 최초 전부-NA object 컬럼이 INTEGER로 추론되어 식별자가 손상되지 않아야 함(리뷰 #4)
    con = _con()
    upsert(con, "T", pd.DataFrame({"k": ["a"], "stock_code": [None]}), keys=["k"])
    upsert(con, "T", pd.DataFrame({"k": ["b"], "stock_code": ["005930"]}), keys=["k"])
    got = con.execute('SELECT stock_code FROM "T" WHERE k = ?', ["b"]).fetchone()[0]
    assert got == "005930"  # 선행 0 보존(정수 5930으로 코어션 안 됨)


def test_upsert_new_column_raises() -> None:
    # 스키마 진화 미지원: 신규 컬럼은 명확한 ValueError(리뷰 #12)
    con = _con()
    upsert(con, "T", pd.DataFrame({"k": ["a"], "v": [1]}), keys=["k"])
    with pytest.raises(ValueError):
        upsert(con, "T", pd.DataFrame({"k": ["b"], "v": [2], "w": [3]}), keys=["k"])
