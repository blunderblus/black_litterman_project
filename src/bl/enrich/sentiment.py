"""뉴스 감성 → company_sentiment(corp_code, sentiment_score[-1,1], confidence, event_cnt, event_type).

설계 02 §1·§3.1.6, 06. 두 경로:
- **Gemini(키 있으면)**: 기사별 **구조화 출력**(JSON: sentiment·event_type·salience)을
  ``temperature=0`` 으로 채점하고, (모델·프롬프트·본문) 콘텐츠 해시로 **멱등 캐시**(JSON, pickle 금지)에
  적재한다 → 재실행 시 결정적·무비용(BL 뷰 Q 입력의 재현성 보존).
- **규칙기반 렉시콘(키 없거나 호출 실패)**: 키 없이도 동작(데모/오프라인 폴백).

confidence 는 하드코딩이 아니라 기사 수·부호 일관성에서 산출한다(과거 결함 R-06 교정).
누수 차단: 감성은 BL 뷰 'news' 축 입력이므로 추론월(base_ym) 이후 발행 기사는 시점 컷오프로 제외한다.
LLM은 신호 *생산* 단계의 단발 구조화 호출로 한정한다(자율 에이전트 아님): No-Crawl·결정성·
confidence 외부산출 규약과 충돌하지 않도록 도구사용·멀티스텝을 두지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from bl.common.logging import get_logger

if TYPE_CHECKING:
    from bl.common.config import Settings

log = get_logger(__name__)

MODEL_ID = "gemini-2.5-flash-lite"
# 프롬프트/스키마/모델을 바꾸면 이 토큰을 올려 콘텐츠 캐시를 자연 무효화한다.
PROMPT_VERSION = "v2-structured"
PROMPT_CHARS = 300                      # 프롬프트·캐시키 공통 본문 절단 길이(둘을 일치시켜 캐시정합)
EVENT_TYPES = ("funding", "M&A", "litigation", "regulatory", "leadership", "none")

OUTPUT_COLS = ["corp_code", "sentiment_score", "confidence", "event_cnt", "event_type"]

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


def _clip_float(v: object, lo: float, hi: float) -> float:
    """v를 **유한** float로 변환해 [lo,hi]로 클립. 변환불가·비유한(NaN/Inf)은 중립값(0 또는 lo)."""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        f = float("nan")
    if not np.isfinite(f):                      # NaN/Inf 차단(캐시·집계·BL Q 오염 방지)
        return 0.0 if lo <= 0.0 <= hi else lo
    return float(np.clip(f, lo, hi))


def _norm_event(v: object) -> str:
    """event_type을 허용 ENUM으로 정규화(미허용·결측은 'none')."""
    e = str(v)
    return e if e in EVENT_TYPES else "none"


def _norm_result(d: dict) -> dict:
    """원시 dict → 검증된 {sentiment[-1,1], event_type∈ENUM, salience[0,1]}.

    신규 채점·캐시 적중 양쪽에 동일 적용해, 손상/구버전 캐시 엔트리(키 누락·범위 위반)도 안전화한다.
    """
    return {"sentiment": _clip_float(d.get("sentiment"), -1.0, 1.0),
            "event_type": _norm_event(d.get("event_type", "none")),
            "salience": _clip_float(d.get("salience"), 0.0, 1.0)}


def parse_sentiment_json(raw: str) -> dict:
    """Gemini JSON 응답 → {sentiment[-1,1], event_type∈ENUM, salience[0,1]} (방어적 파싱).

    유효 JSON 객체가 아니면 감성을 **날조하지 않고 중립(0/none/0)** 으로 둔다(response_mime_type=json이
    보장될 때만 정상 경로). 범위/도메인/비유한 위반은 _norm_result가 클립·정규화한다.
    """
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("not a JSON object")
    except (json.JSONDecodeError, ValueError, TypeError):
        return {"sentiment": 0.0, "event_type": "none", "salience": 0.0}
    return _norm_result(obj)


# --- 멱등 캐시(JSON, pickle 금지 — ADR-0002) ---------------------------------

def _cache_path(settings: Settings | None) -> Path | None:
    adir = getattr(settings, "artifacts_dir", None) if settings is not None else None
    return Path(adir) / "enrich" / "gemini_sentiment_cache.json" if adir is not None else None


def _load_cache(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return obj if isinstance(obj, dict) else {}   # 유효하나 dict 아닌 캐시 → 무시(크래시 방지)


def _save_cache(path: Path | None, cache: dict) -> None:
    if path is None:
        return
    try:
        blob = json.dumps(cache, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except ValueError as e:   # 비유한 값(이론상 차단됨) → 캐시 저장만 건너뜀(파이프라인 유지)
        log.warning(f"캐시 직렬화 스킵(비유한 값): {e}", extra={"stage": "enrich.sentiment"})
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(path.parent))
    os.close(fd)
    try:
        Path(tmp).write_text(blob, encoding="utf-8")
        os.replace(tmp, path)  # 원자적 교체
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _cache_key(text: str) -> str:
    """(모델·프롬프트버전·실제 전송 본문) 콘텐츠 해시 — 프롬프트와 동일한 절단 본문으로 키 정합."""
    payload = f"{MODEL_ID}|{PROMPT_VERSION}|{text[:PROMPT_CHARS]}"
    return hashlib.sha256(payload.encode()).hexdigest()


# --- Gemini 단건 구조화 호출 -------------------------------------------------

def _gemini_model(settings: Settings | None):
    """키가 있으면 GenerativeModel, 없으면 None. (테스트 monkeypatch 지점)"""
    key = settings.gemini_api_key.get_secret_value() if (
        settings is not None and settings.gemini_api_key is not None
    ) else None
    if not key:
        return None
    try:
        import google.generativeai as genai

        genai.configure(api_key=key)
        return genai.GenerativeModel(MODEL_ID)
    except Exception as e:  # noqa: BLE001
        log.warning(f"Gemini 초기화 실패 → 규칙기반 폴백: {e}", extra={"stage": "enrich.sentiment"})
        return None


def _gemini_one(model: object, text: str) -> dict | None:
    """기사 1건 구조화 채점(temperature=0, JSON 출력). 실패 시 None(→ 렉시콘 폴백)."""
    schema = ('{"sentiment": -1~1 실수(기업가치 관점 부정~긍정), '
              f'"event_type": {list(EVENT_TYPES)} 중 하나, '
              '"salience": 0~1 실수(기사 중요도·관련도)}')
    body = text[:PROMPT_CHARS]
    prompt = f"다음 기업 뉴스를 분석해 JSON 객체로만 답하라(설명 금지). 스키마:\n{schema}\n기사: {body}"
    try:
        cfg = {"temperature": 0, "response_mime_type": "application/json"}
        r = model.generate_content(prompt, generation_config=cfg)  # type: ignore[attr-defined]
        return parse_sentiment_json(str(getattr(r, "text", "")))
    except Exception as e:  # noqa: BLE001
        log.warning(f"Gemini 채점 실패 → 규칙기반 폴백: {e}", extra={"stage": "enrich.sentiment"})
        return None


def _lexicon_result(text: str) -> dict:
    return {"sentiment": lexicon_score(text), "event_type": "none", "salience": 0.0}


def _score_texts(texts: list[str], settings: Settings | None) -> list[dict]:
    """기사 텍스트 → [{sentiment,event_type,salience}]. Gemini(키)+멱등 캐시, 아니면 렉시콘 폴백."""
    model = _gemini_model(settings)
    if model is None:
        return [_lexicon_result(t) for t in texts]
    path = _cache_path(settings)
    cache = _load_cache(path)
    out: list[dict] = []
    dirty = False
    for t in texts:
        key = _cache_key(t)
        hit = cache.get(key)
        if hit is not None:                       # 손상/구버전 엔트리도 _norm_result로 안전화
            out.append(_norm_result(hit))
            continue
        res = _gemini_one(model, t)
        if res is None:  # 일시 실패 → 캐시하지 않고 렉시콘 폴백(다음 실행에서 재시도)
            out.append(_lexicon_result(t))
            continue
        cache[key] = res
        dirty = True
        out.append(res)
    if dirty:
        _save_cache(path, cache)
    return out


# --- 시점 컷오프 & 기업 단위 집계 --------------------------------------------

def _cutoff_news(news: pd.DataFrame, base_ym: int | None) -> pd.DataFrame:
    """point-in-time 컷오프: base_ym 이후·발행시점 미상(NaT) 기사를 제외(look-ahead 누수 차단).

    감성은 BL 뷰 'news' 축 입력이므로 추론월 이후 기사 유입은 누수다. pub_date 파싱 불가도
    시점 검증 불가이므로 보수적으로 제외한다. base_ym/pub_date 부재 시 컷오프 없음(데모 호환).
    """
    if base_ym is None or "pub_date" not in news.columns:
        return news
    dt = pd.to_datetime(news["pub_date"], errors="coerce", utc=True)
    ym = dt.dt.year.to_numpy(dtype="float64") * 100 + dt.dt.month.to_numpy(dtype="float64")
    keep = np.isfinite(ym) & (ym <= float(base_ym))
    dropped = int((~keep).sum())
    if dropped:
        log.info(f"뉴스 시점 컷오프 base_ym={base_ym}: {dropped}건 제외(누수 차단)",
                 extra={"stage": "enrich.sentiment", "dropped": dropped})
    return news[keep]


def _dominant_event(g: pd.DataFrame) -> str:
    """기사 그룹 → 대표 event_type. salience 최대(동률은 event_type ASCII 정렬)로 결정적 선택."""
    if "event_type" not in g.columns:
        return "none"
    cand = g[g["event_type"] != "none"]
    if cand.empty:
        return "none"
    sal = cand["salience"] if "salience" in cand.columns else 0.0
    ranked = cand.assign(_sal=sal).sort_values(["_sal", "event_type"], ascending=[False, True])
    return str(ranked.iloc[0]["event_type"])


def aggregate_sentiment(scored: pd.DataFrame) -> pd.DataFrame:
    """기사별 점수 → 기업 단위 company_sentiment(평균·기사수·일관성 기반 confidence·대표 event)."""
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
                     "confidence": conf, "event_cnt": cnt, "event_type": _dominant_event(g)})
    return pd.DataFrame(rows, columns=OUTPUT_COLS)


def score_sentiment(
    news: pd.DataFrame, settings: Settings | None = None, base_ym: int | None = None,
) -> pd.DataFrame:
    """news 프레임 → company_sentiment. Gemini 구조화 채점(키)+멱등 캐시 또는 규칙기반(폴백).

    base_ym 지정 시 발행시점(pub_date) 컷오프로 추론월 이후 기사를 제외한다(누수 차단).
    """
    if news is None or news.empty:
        return pd.DataFrame(columns=OUTPUT_COLS)
    news = _cutoff_news(news, base_ym)
    if news.empty:
        return pd.DataFrame(columns=OUTPUT_COLS)
    n = len(news)
    title = (news["title"].fillna("").astype(str) if "title" in news.columns
             else pd.Series([""] * n, index=news.index))
    desc = (news["description"].fillna("").astype(str) if "description" in news.columns
            else pd.Series([""] * n, index=news.index))
    texts = (title + " " + desc).tolist()
    results = _score_texts(texts, settings)
    scored = news[["corp_code"]].copy()
    scored["score"] = [r["sentiment"] for r in results]
    scored["event_type"] = [r["event_type"] for r in results]
    scored["salience"] = [r["salience"] for r in results]
    return aggregate_sentiment(scored)
