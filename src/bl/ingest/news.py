"""뉴스 수집(Track B/C) — Naver/BigKinds → RAW_NEWS(NEWS_HASH). keyset 페이지네이션(OFFSET 비결정 금지)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def collect_news(con: "duckdb.DuckDBPyConnection", settings: "Settings", source: str) -> int:
    """source in {"naver","bigkinds"}. 대상 키워드로 뉴스 수집·멱등 적재."""
    raise NotImplementedError("P1에서 구현 — 설계 문서 참조")
