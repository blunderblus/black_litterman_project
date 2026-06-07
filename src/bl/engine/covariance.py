"""공분산 — 잔액 log-return 패널의 FULL 공분산 + Ledoit-Wolf 수축(대각 사용 금지). PSD·고유값바닥·조건수·Cholesky 보장. (03 §3)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def shrunk_covariance(returns, preference: str | None = None):
    """returns(TxN) -> 수축 FULL 공분산 Sigma. xp 디스패치(CPU/GPU 동일 수치)."""
    raise NotImplementedError("P4에서 구현 — 설계 문서 참조")
