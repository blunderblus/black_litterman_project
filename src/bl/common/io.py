"""저장 I/O — DuckDB(수집/OLAP) + Parquet(분석/교환). pickle 폐기(ADR-0002).

- DuckDB: 원천 적재·조인·증분(upsert).
- Parquet: 단계 간 교환 산출물(train_set, inference_set, bl_input_data, bl_dashboard_mart 등).
- 멱등성: 키 기준 DELETE-then-INSERT(재실행 안전). 비결정 OFFSET 적재 금지.
설계: docs/design/02-data-pipeline.md §3, §6.

upsert 계약(P1 코드리뷰 반영):
- 키 컬럼 NULL 금지(PK NOT NULL). df 내부 중복 키 금지(1키=1행). → 위반 시 ValueError.
- 외부 트랜잭션 안에서 호출 가능(중첩 BEGIN을 시도하지 않고 호출자 tx에 참여).
- 동일 스키마 재실행만 안전(신규 컬럼은 ValueError; 스키마 진화 미지원).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd


def _q(name: str) -> str:
    """SQL 식별자를 큰따옴표로 안전하게 인용한다(내부 따옴표 이스케이프)."""
    return '"' + str(name).replace('"', '""') + '"'


def duckdb_connect(db_path: str | Path, read_only: bool = False) -> "duckdb.DuckDBPyConnection":
    """DuckDB 연결을 연다. 부모 디렉터리는 자동 생성한다."""
    import duckdb

    p = Path(db_path)
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(p), read_only=read_only)


def read_parquet(path: str | Path, columns: Sequence[str] | None = None) -> "pd.DataFrame":
    """Parquet → DataFrame."""
    import pandas as pd

    return pd.read_parquet(path, columns=list(columns) if columns else None)


def write_parquet(df: "pd.DataFrame", path: str | Path) -> Path:
    """DataFrame → Parquet (원자적 쓰기: temp 파일 작성 후 rename)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".parquet", dir=str(out.parent))
    os.close(fd)
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, out)  # 원자적 교체
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return out


def _table_columns(con: "duckdb.DuckDBPyConnection", table: str) -> list[str] | None:
    """테이블이 존재하면 컬럼명 리스트, 없으면 None."""
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    return [r[0] for r in rows] if rows else None


def upsert(
    con: "duckdb.DuckDBPyConnection",
    table: str,
    df: "pd.DataFrame",
    keys: Sequence[str],
    *,
    in_transaction: bool = False,
) -> int:
    """키 기준 멱등 upsert(DELETE-then-INSERT). 적재(삽입) 행 수를 반환한다.

    - 테이블이 없으면 df 스키마로 생성한다(전부-NA object 컬럼은 문자열로 고정해 타입 오추론 방지).
    - df의 키 튜플과 일치하는 기존 행을 삭제한 뒤 df 전체를 삽입한다.
    - 같은 df로 재실행해도 결과가 동일하다(멱등).
    - 트랜잭션: 기본(in_transaction=False)은 자체 BEGIN/COMMIT으로 단일 호출을 원자화한다.
      호출자가 이미 트랜잭션을 열고 다중 테이블을 묶는 경우 in_transaction=True로 호출하면
      자체 BEGIN을 생략하고 호출자 tx에 참여한다(DuckDB는 중첩 BEGIN 미지원·실패 시 tx abort).

    Raises:
        ValueError: 빈 keys / 키·컬럼 누락 / **키 NULL** / **df 내부 중복 키** / 기존 테이블에 없는 신규 컬럼.
    """
    keys = list(keys)
    if not keys:
        raise ValueError("upsert에는 최소 1개의 키 컬럼이 필요합니다.")
    if df is None or len(df) == 0:
        return 0
    cols = list(df.columns)
    missing = [k for k in keys if k not in cols]
    if missing:
        raise ValueError(f"키 컬럼이 df에 없습니다: {missing}")
    # PK NOT NULL: 키 NULL은 EXISTS 매칭 실패로 멱등성을 깨므로 금지
    if bool(df[keys].isna().any().any()):
        raise ValueError(f"키 컬럼에 NULL이 있습니다(멱등성 위반): {keys}")
    # 1키=1행: df 내부 중복 키는 PK 유일성을 깨므로 금지(호출부에서 사전 dedup)
    ndup = int(df.duplicated(subset=keys).sum())
    if ndup:
        raise ValueError(f"df 내부 중복 키 {ndup}건(키 {keys}). 적재 전 dedup 필요")

    # 전부-NA object 컬럼은 DuckDB가 INTEGER로 오추론 → 문자열로 고정(식별자 손상 방지)
    src = df.copy()
    for c in src.columns:
        if src[c].isna().all():
            src[c] = src[c].astype("string")

    qtable = _q(table)
    col_list = ", ".join(_q(c) for c in cols)
    cond = " AND ".join(f"{qtable}.{_q(k)} = s.{_q(k)}" for k in keys)
    view = "__bl_upsert_" + uuid.uuid4().hex

    con.register(view, src)
    own_tx = not in_transaction
    try:
        if own_tx:
            con.execute("BEGIN TRANSACTION")
        try:
            existing = _table_columns(con, table)
            if existing is None:
                con.execute(f"CREATE TABLE {qtable} AS SELECT * FROM {view} WHERE 1=0")
            else:
                extra = [c for c in cols if c not in existing]
                if extra:
                    raise ValueError(
                        f"기존 테이블 '{table}'에 없는 신규 컬럼 {extra} (스키마 진화 미지원)"
                    )
            con.execute(f"DELETE FROM {qtable} WHERE EXISTS (SELECT 1 FROM {view} s WHERE {cond})")
            con.execute(f"INSERT INTO {qtable} ({col_list}) SELECT {col_list} FROM {view}")
            if own_tx:
                con.execute("COMMIT")
        except Exception:
            if own_tx:
                con.execute("ROLLBACK")
            raise
    finally:
        con.unregister(view)
    return len(df)
