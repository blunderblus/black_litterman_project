"""저장 I/O — DuckDB(수집/OLAP) + Parquet(분석/교환). pickle 폐기(ADR-0002).

- DuckDB: 원천 적재·조인·증분(upsert).
- Parquet: 단계 간 교환 산출물(train_set, inference_set, bl_input_data, bl_dashboard_mart 등).
- 멱등성: 키 기준 DELETE-then-INSERT(또는 INSERT OR REPLACE) + 원자적 교체.
설계: docs/design/02-data-pipeline.md §3, §6.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb
    import pandas as pd


def duckdb_connect(db_path: str | Path, read_only: bool = False) -> "duckdb.DuckDBPyConnection":
    """DuckDB 연결을 연다. 부모 디렉터리는 자동 생성한다."""
    import duckdb

    p = Path(db_path)
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


def upsert(
    con: "duckdb.DuckDBPyConnection",
    table: str,
    df: "pd.DataFrame",
    keys: Sequence[str],
) -> int:
    """키 기준 멱등 upsert(DELETE-then-INSERT). 적재 행 수 반환.

    OFFSET-without-ORDER-BY 류 비결정 적재를 금지하고, 키 기준 재실행 안전성을 보장한다.
    TODO(P1): 대용량/증분 최적화 및 트랜잭션 경계 정교화.
    """
    raise NotImplementedError("P1(데이터 레이어)에서 구현 — 설계 02-data-pipeline §6")
