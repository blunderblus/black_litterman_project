"""enrich.sentiment — 구조화 Gemini 채점 + 멱등 캐시 + 시점 컷오프 + 렉시콘 폴백의 결정성/정합 테스트.

네트워크/google-generativeai 없이 동작한다(_gemini_model monkeypatch). 핵심 검증:
- 키 없음(settings=None) → 렉시콘 경로, 결정적·정확.
- 구조화 JSON 파싱(범위 클립·ENUM 정규화·비유한 차단·비JSON/비dict 중립 폴백).
- 멱등 캐시: 2회 실행 시 추가 API 호출 0, 산출 동일(재현성). 캐시는 JSON(no-pickle). 결정성 노브(temp=0,
  response_mime_type=application/json)와 Gemini 경로 산출 '값'까지 단언(약한 오라클 방지).
- 시점 컷오프: base_ym 이후 발행 뉴스 제외(누수 차단).
- 집계: sentiment 평균·event_cnt·대표 event(salience 최대).
"""

from __future__ import annotations

import json

import pandas as pd

import bl.enrich.sentiment as s


def _news() -> pd.DataFrame:
    return pd.DataFrame({
        "corp_code": ["A", "A", "B"],
        "title": ["t1", "t2", "t3"],
        "description": ["d1", "d2", "d3"],
    })


def test_lexicon_score_signed_and_deterministic():
    assert s.lexicon_score("성장 수주 확대") == 1.0
    assert s.lexicon_score("적자 부도 소송") == -1.0
    assert s.lexicon_score("중립적 문장") == 0.0
    assert s.lexicon_score("성장 적자") == s.lexicon_score("성장 적자") == 0.0


def test_parse_sentiment_json_clip_enum_and_fallback():
    assert s.parse_sentiment_json('{"sentiment":0.7,"event_type":"M&A","salience":0.5}') == {
        "sentiment": 0.7, "event_type": "M&A", "salience": 0.5,
    }
    clamped = s.parse_sentiment_json('{"sentiment":5,"event_type":"funding","salience":9}')
    assert clamped["sentiment"] == 1.0 and clamped["salience"] == 1.0
    assert s.parse_sentiment_json('{"sentiment":0,"event_type":"weird","salience":0.1}')[
        "event_type"
    ] == "none"
    # 비JSON·비dict → 감성 날조 금지(중립 0/none/0)
    assert s.parse_sentiment_json("그냥 0.3 입니다")["sentiment"] == 0.0
    none_res = s.parse_sentiment_json("형식 위반 텍스트")
    assert none_res["sentiment"] == 0.0 and none_res["event_type"] == "none"


def test_parse_sentiment_json_non_finite_and_non_dict():
    # 유효 JSON이지만 비유한 값 → 차단(중립). json.loads는 NaN/Infinity를 기본 허용.
    assert s.parse_sentiment_json('{"sentiment": NaN, "event_type":"funding","salience":0.5}')[
        "sentiment"
    ] == 0.0
    assert s.parse_sentiment_json('{"sentiment": 0.4, "event_type":"none","salience": Infinity}')[
        "salience"
    ] == 0.0
    # 비감성 숫자(연도)·JSON 배열 → 극단 감성으로 오회수하지 않음(중립)
    assert s.parse_sentiment_json("2024년 매출 호조")["sentiment"] == 0.0
    assert s.parse_sentiment_json("[1,2,3]")["sentiment"] == 0.0


def test_norm_result_fills_and_clips():
    assert s._norm_result({"sentiment": 0.5}) == {
        "sentiment": 0.5, "event_type": "none", "salience": 0.0,
    }
    assert s._norm_result({"sentiment": 5, "event_type": "weird", "salience": -1}) == {
        "sentiment": 1.0, "event_type": "none", "salience": 0.0,
    }


def test_aggregate_dominant_event_and_confidence():
    scored = pd.DataFrame({
        "corp_code": ["A", "A", "A"],
        "score": [0.2, 0.4, 0.6],
        "event_type": ["none", "funding", "litigation"],
        "salience": [0.0, 0.9, 0.3],
    })
    out = s.aggregate_sentiment(scored)
    row = out.iloc[0]
    assert row["sentiment_score"] == round((0.2 + 0.4 + 0.6) / 3, 3)
    assert row["event_cnt"] == 3
    assert row["event_type"] == "funding"          # salience 최대(non-none)
    assert 0.05 <= row["confidence"] <= 0.99


def test_no_key_uses_lexicon_deterministic():
    news = pd.DataFrame({
        "corp_code": ["A", "A", "B"],
        "title": ["성장 수주 확대", "적자 부도 소송", "흑자 개선"],
        "description": ["", "", ""],
    })
    out1 = s.score_sentiment(news, settings=None)
    out2 = s.score_sentiment(news, settings=None)
    pd.testing.assert_frame_equal(out1, out2)                 # 결정적
    assert list(out1.columns) == s.OUTPUT_COLS
    by = out1.set_index("corp_code")["sentiment_score"]
    assert by["A"] == 0.0 and by["B"] == 1.0                  # (+1,-1)평균=0 / +1
    assert (out1["event_type"] == "none").all()              # 렉시콘 → 이벤트 없음


def test_news_pit_cutoff_blocks_future_articles():
    news = pd.DataFrame({
        "corp_code": ["A", "A"],
        "title": ["성장 수주 확대", "적자 부도 소송"],            # 과거=+1, 미래=-1
        "description": ["", ""],
        "pub_date": ["Mon, 06 Jan 2025 09:00:00 +0900",
                     "Wed, 10 Dec 2025 09:00:00 +0900"],
    })
    out = s.score_sentiment(news, settings=None, base_ym=202506)   # 2025-06 컷오프
    assert len(out) == 1
    assert out.iloc[0]["sentiment_score"] == 1.0             # 미래 부정기사 제외 → +1만
    assert out.iloc[0]["event_cnt"] == 1
    # base_ym 미지정 시 컷오프 없음(데모 호환): 두 기사 평균 0
    assert s.score_sentiment(news, settings=None).iloc[0]["sentiment_score"] == 0.0


def test_score_sentiment_empty():
    empty = pd.DataFrame(columns=["corp_code", "title", "description"])
    out = s.score_sentiment(empty, None)
    assert out.empty and list(out.columns) == s.OUTPUT_COLS
    assert s.score_sentiment(None, None).empty


def test_load_cache_non_dict_returns_empty(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")              # 유효 JSON이나 dict 아님
    assert s._load_cache(p) == {}
    assert s._load_cache(tmp_path / "missing.json") == {}


class _FakeModel:
    """generate_content 호출 수·마지막 generation_config를 기록하고 고정 JSON을 반환하는 가짜 모델."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_cfg: dict | None = None

    def generate_content(self, prompt, generation_config=None):
        self.calls += 1
        self.last_cfg = generation_config
        return type("R", (), {"text": '{"sentiment":0.5,"event_type":"funding","salience":0.9}'})()


def test_gemini_cache_idempotent_and_deterministic(tmp_path, monkeypatch):
    from bl.common.config import Settings

    settings = Settings(gemini_api_key="fake", data_root=tmp_path)
    fake = _FakeModel()
    monkeypatch.setattr(s, "_gemini_model", lambda _settings: fake)

    news = _news()
    out1 = s.score_sentiment(news, settings)
    assert fake.calls == 3                                    # 고유 기사 3건만 채점
    assert list(out1.columns) == s.OUTPUT_COLS
    # 결정성 노브가 실제로 전달됐는지 단언(약한 오라클 방지)
    assert fake.last_cfg["temperature"] == 0
    assert fake.last_cfg["response_mime_type"] == "application/json"
    # Gemini 경로 산출 '값'까지 단언(컬럼/호출수만 보던 빈틈 보강)
    by = out1.set_index("corp_code")
    assert (by["sentiment_score"] == 0.5).all() and (by["event_type"] == "funding").all()

    cache_file = tmp_path / "artifacts" / "enrich" / "gemini_sentiment_cache.json"
    assert cache_file.exists()
    cached = json.loads(cache_file.read_text(encoding="utf-8"))   # JSON(no-pickle)
    assert len(cached) == 3 and all(set(v) == {"sentiment", "event_type", "salience"}
                                    for v in cached.values())

    out2 = s.score_sentiment(news, settings)                 # 2회차 → 전부 캐시 적중
    assert fake.calls == 3                                    # 추가 API 호출 0
    pd.testing.assert_frame_equal(out1, out2)                # 재현성


def test_gemini_failure_falls_back_to_lexicon(tmp_path, monkeypatch):
    from bl.common.config import Settings

    settings = Settings(gemini_api_key="fake", data_root=tmp_path)

    class _Boom:
        def generate_content(self, *a, **k):
            raise RuntimeError("rate limit")

    monkeypatch.setattr(s, "_gemini_model", lambda _settings: _Boom())
    news = pd.DataFrame({"corp_code": ["A"], "title": ["성장 수주"], "description": [""]})
    out = s.score_sentiment(news, settings)
    assert out.iloc[0]["sentiment_score"] == 1.0             # 렉시콘 폴백
    # 실패는 캐시하지 않음(재시도 가능) → 캐시 파일 미생성
    cache_file = tmp_path / "artifacts" / "enrich" / "gemini_sentiment_cache.json"
    assert not cache_file.exists()
