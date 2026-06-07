"""BL 최적화 — full 사후수익 + 제약 QP(cvxpy/SLSQP) -> 최적가중·marketing_score. 폭주/퇴화 금지, 부호정합. (과거: 10, 03 §6-8)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd
    from bl.common.config import Settings


def posterior_expected_return(inputs: dict, preference: str | None = None):
    """E[R] 정칙형(Cholesky solve, full 행렬). xp 디스패치."""
    raise NotImplementedError("P4에서 구현 — 설계 문서 참조")


def optimize_weights(er, cov, settings: "Settings", preference: str | None = None):
    """제약(sum w=1, 0<=w<=w_max, 제외집합) 하 QP 최적가중 w* 산출."""
    raise NotImplementedError("P4에서 구현 — 설계 문서 참조")
