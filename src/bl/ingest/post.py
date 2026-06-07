"""내부 거래/잔액/관계 적재(접근통제) — post_data/POST_OWNED_CORPS. 공개 데모엔 미포함(합성 대체)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

    from bl.common.config import Settings


def ingest_post_data(con: duckdb.DuckDBPyConnection, settings: Settings) -> int:
    """내부 거래·잔액·관계를 corp_code로 정규화해 적재."""
    raise NotImplementedError("P1에서 구현 — 설계 문서 참조")
