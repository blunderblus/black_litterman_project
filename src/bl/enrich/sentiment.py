"""뉴스 감성 → company_sentiment(corp_code, sentiment_score[-1,1], confidence, event_cnt).

설계 02 §1, 06. Gemini(키 있으면) 또는 규칙기반 렉시콘(키 없어도 동작) 두 경로.
confidence 는 하드코딩이 아니라 기사 수/일관성에서 산출(과거 결함 교정).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from bl.common.logging import get_logger

if TYPE_CHECKING:
    from bl.common.config import Settings

log = get_logger(__name__)

# 규칙기반 폴백용 한국어 감성 렉시콘(소형). Gemini 키 없을 때 사용.
POS_WORDS = ["성장", "흑자", "수주", "최대", "확대", "호조", "신규", "개선", "상승", "달성", "수출", "투자유치"]
NEG_WORDS = ["적자", "부도", "감원", "소송", "리콜", "하락", "부진", "위기", "축소", "철수", "연체", "횡령", "파산"]


def lexicon_score(text: str) -> float:
    """텍스트 → [-1,1] 규칙기반 감성(양성-음성 키워드 비율)."""
    t = str(text)
    p = sum(t.count(w) for w in POS_WORDS)
    n = sum(t.count(w) for w in NEG_WORDS)
    if p + n == 0:
        return 0.0
    return (p - n) / (p + n)


def aggregate_sentiment(scored: pd.DataFrame) -> pd.DataFrame:
    """기사별 점수 → 기업 단위 company_sentiment(평균·기사수·일관성 기반 confidence)."""
    rows = []
    for cc, g in scored.groupby("corp_code"):
        s = g["score"].to_numpy(dtype="float64")
        mean = float(np.mean(s))
        cnt = int(len(s))
        # confidence: 기사 수(많을수록↑) × 부호 일관성(분산 낮을수록↑), [0,1]
        consistency = 1.0 - min(float(np.std(s)), 1.0)
        volume = min(cnt / 10.0, 1.0)
        conf = round(float(np.clip(0.5 * consistency + 0.5 * volume, 0.05, 0.99)), 3)
        rows.append({"corp_code": cc, "sentiment_score": round(mean, 3),
                     "confidence": conf, "event_cnt": cnt})
    return pd.DataFrame(rows, columns=["corp_code", "sentiment_score", "confidence", "event_cnt"])


def _gemini_score(texts: list[str], settings: "Settings") -> list[float] | None:
    """Gemini로 기사 감성 배치 채점(키 있을 때). 실패/미설치 시 None → 폴백."""
    key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None
    if not key:
        return None
    try:
        import google.generativeai as genai

        genai.configure(api_key=key)
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        import re

        scores: list[float] = []
        for t in texts:
            prompt = f"다음 기업 뉴스의 감성을 -1(매우 부정)~1(매우 긍정) 실수 하나로만 답하라:\n{t[:200]}"
            r = model.generate_content(prompt)
            mobj = re.search(r"-?\d+(?:\.\d+)?", str(getattr(r, "text", "")))
            scores.append(float(np.clip(float(mobj.group()), -1.0, 1.0)) if mobj else 0.0)
        return scores
    except Exception as e:  # noqa: BLE001
        log.warning(f"Gemini 채점 실패 → 규칙기반 폴백: {e}", extra={"stage": "enrich.sentiment"})
        return None


def score_sentiment(news: pd.DataFrame, settings: "Settings | None" = None) -> pd.DataFrame:
    """news 프레임 → company_sentiment. Gemini(키) 또는 규칙기반(폴백)."""
    if news.empty:
        return pd.DataFrame(columns=["corp_code", "sentiment_score", "confidence", "event_cnt"])
    texts = (news["title"].fillna("") + " " + news.get("description", "").fillna("")).tolist()
    gem = _gemini_score(texts, settings) if settings is not None else None
    scored = news[["corp_code"]].copy()
    scored["score"] = gem if gem is not None else [lexicon_score(t) for t in texts]
    return aggregate_sentiment(scored)
