"""뉴스 감성(Gemini 2.5 Flash-Lite) → COMPANY_SENTIMENT(score in [-1,1]+confidence). confidence 하드코딩 금지."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def score_sentiment(con: "duckdb.DuckDBPyConnection", settings: "Settings") -> int:
    """NEWS_REFINED 헤드라인을 기업 단위 감성으로 점수화. NEWS_HASH 멱등."""
    raise NotImplementedError("P3에서 구현 — 설계 문서 참조")
