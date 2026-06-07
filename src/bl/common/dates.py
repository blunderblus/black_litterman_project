"""연월(YYYYMM) 산술 헬퍼 — 시점분리(embargo)·공시지연(lag) 정렬용."""

from __future__ import annotations


def ym_add(ym: int, months: int) -> int:
    """YYYYMM 에 months(±)를 더한 YYYYMM 반환."""
    y, m = divmod(int(ym), 100)
    total = (y * 12 + (m - 1)) + int(months)
    ny, nm = divmod(total, 12)
    return ny * 100 + (nm + 1)
