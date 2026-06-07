"""뉴스 정제 — Kiwi 키워드 백필 + Jaccard 근접중복 제거 → NEWS_REFINED. (과거: 05)

뉴스 소스는 Naver만 사용한다(BigKinds는 폐쇄적 API라 제외).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def dedup_news(con: "duckdb.DuckDBPyConnection", settings: "Settings") -> int:
    """RAW_NEWS를 윈도우 Jaccard로 dedup·키워드 채워 NEWS_REFINED 생성."""
    raise NotImplementedError("P2에서 구현 — 설계 문서 참조")
