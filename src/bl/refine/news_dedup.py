"""뉴스 정제 — Kiwi 키워드 백필 + Jaccard 근접중복 제거(BigKinds 우선) → NEWS_REFINED. (과거: 05)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def dedup_news(con: "duckdb.DuckDBPyConnection", settings: "Settings") -> int:
    """RAW_NEWS를 윈도우 Jaccard로 dedup·키워드 채워 NEWS_REFINED 생성."""
    raise NotImplementedError("P2에서 구현 — 설계 문서 참조")
