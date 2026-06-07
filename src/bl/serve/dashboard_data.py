"""대시보드 데이터 추출 — 외부 JSON(데이터/HTML 분리, 상위 N, 크기상한). PII 분리(공개 데모=합성). (05 §5-6)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def export_dashboard_json(mart: "pd.DataFrame", out_dir: str, top_n: int = 200) -> str:
    """마트를 대시보드용 경량 JSON으로 추출(상위 N, 사이드카 Pi/Q/Omega)."""
    raise NotImplementedError("P5에서 구현 — 설계 문서 참조")
