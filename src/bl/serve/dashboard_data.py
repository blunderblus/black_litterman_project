"""대시보드 데이터 추출 — 외부 JSON(데이터/HTML 분리, 상위 N, 크기 상한).

설계 §5-6. 과거 토이 결함 교정: 246MB 인라인 JSON 폐기 → 경량 외부 JSON(상위 N).
공개 데모는 합성 데이터만 사용(PII 분리). marketing_score 내림차순 정렬.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    pass

# 대시보드에 노출할 컬럼(존재하는 것만 출력)
EXPORT_COLS = [
    "corp_code", "corp_name", "tier", "sector_code", "region", "current_bal",
    "bl_return", "current_weight", "market_weight", "target_weight", "weight_diff",
    "marketing_score", "action_guide", "funding_gap",
    "prob_growth_raw", "prob_churn_raw", "anomaly_score_raw", "news_sentiment",
    "pi", "q", "omega",
]


def _clean(v):
    """JSON 직렬화용 정리: bool→bool, pd.NA/NaT/NaN/Inf→None, numpy 스칼라→파이썬 스칼라."""
    if isinstance(v, (bool, np.bool_)):              # bool 먼저(np.bool_/bool은 int 서브타입)
        return bool(v)
    try:
        na = pd.isna(v)                              # pd.NA, pd.NaT, NaN 등(배열은 ndim>0로 제외)
        if np.ndim(na) == 0 and bool(na):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else round(f, 6)
    if isinstance(v, (np.integer, int)):
        return int(v)
    return v


def export_dashboard_json(
    mart: "pd.DataFrame",
    out_dir: str | Path,
    top_n: int = 200,
    metadata: dict | None = None,
) -> str:
    """마트를 대시보드용 경량 JSON(data.json)으로 추출한다. 파일 경로 반환."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    df = mart.copy()
    if "marketing_score" in df.columns:
        df = df.sort_values("marketing_score", ascending=False)
    df = df.head(top_n)

    cols = [c for c in EXPORT_COLS if c in df.columns]
    records = [{c: _clean(row[c]) for c in cols} for _, row in df.iterrows()]

    # 요약 KPI
    score = mart["marketing_score"] if "marketing_score" in mart.columns else None
    summary = {
        "n": int(len(mart)),
        "active_leads": int((score >= 80).sum()) if score is not None else 0,
        "avg_score": round(float(score.mean()), 2) if score is not None else None,
        "total_aum": round(float(np.nansum(mart["current_bal"])), 0) if "current_bal" in mart else None,
        "total_funding_gap": round(float(np.nansum(mart["funding_gap"])), 0)
        if "funding_gap" in mart.columns else None,
    }
    payload = {"meta": metadata or {}, "summary": summary, "columns": cols, "rows": records}
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path = out / "data.json"
    path.write_text(blob, encoding="utf-8")
    # data.js: window 전역으로도 노출(파일 직접 열람·오프라인 시 fetch/CORS 회피)
    (out / "data.js").write_text(f"window.BL_DATA={blob};", encoding="utf-8")
    return str(path)
