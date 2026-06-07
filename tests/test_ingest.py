"""ingest 파서 + 감성 + load_frames 키게이팅 테스트 (네트워크 불필요, 모의 페이로드)."""

from __future__ import annotations

import pandas as pd

from bl.enrich import sentiment as enr
from bl.ingest import financial as ing_fin
from bl.ingest import macro as ing_mac
from bl.ingest import news as ing_news


def test_parse_ecos() -> None:
    payload = {"StatisticSearch": {"row": [
        {"TIME": "202509", "DATA_VALUE": "3.50"},
        {"TIME": "202510", "DATA_VALUE": "3.25"},
        {"TIME": "bad", "DATA_VALUE": "x"},        # 무효 행은 스킵
    ]}}
    rows = ing_mac.parse_ecos(payload, "BASE_RATE")
    assert len(rows) == 2
    assert rows[0] == {"metric_code": "BASE_RATE", "base_ym": 202509, "value": 3.50}


def test_parse_dart_financial() -> None:
    payload = {"status": "000", "list": [
        {"account_id": "ifrs-full_Revenue", "thstrm_amount": "1,234,567"},
        {"account_id": "ifrs-full_Assets", "thstrm_amount": "9,000,000"},
        {"account_id": "기타", "thstrm_amount": "1"},
    ]}
    row = ing_fin.parse_fnlttSinglAcntAll(payload, "00126380", 202412)
    assert row["corp_code"] == "00126380" and row["base_ym"] == 202412
    assert row["revenue"] == 1234567.0 and row["total_assets"] == 9000000.0


def test_parse_dart_empty_returns_none() -> None:
    assert ing_fin.parse_fnlttSinglAcntAll({"status": "013", "list": []}, "x", 202412) is None
    assert ing_fin.parse_fnlttSinglAcntAll({"status": "000", "list": []}, "x", 202412) is None


def test_parse_naver() -> None:
    payload = {"items": [
        {"title": "<b>데모</b>법인 수주 &quot;최대&quot;", "description": "성장 확대", "pubDate": "Mon, 01"},
    ]}
    rows = ing_news.parse_naver(payload, "00000001")
    assert rows[0]["corp_code"] == "00000001"
    assert "<b>" not in rows[0]["title"] and "데모법인" in rows[0]["title"]


def test_sentiment_lexicon_and_aggregate() -> None:
    assert enr.lexicon_score("수주 성장 흑자") > 0
    assert enr.lexicon_score("적자 부도 소송") < 0
    news = pd.DataFrame({
        "corp_code": ["A", "A", "B"],
        "title": ["성장 수주", "흑자 확대", "적자 부도"],
        "description": ["", "", ""],
    })
    out = enr.score_sentiment(news, settings=None)   # 키 없음 → 규칙기반
    a = out[out["corp_code"] == "A"].iloc[0]
    assert a["sentiment_score"] > 0 and a["event_cnt"] == 2
    assert 0.0 <= a["confidence"] <= 1.0


def test_load_frames_demo_mode_uses_sample(tmp_path) -> None:
    from bl.common.config import Settings
    from bl.pipeline import load_frames
    from bl.synth.generate import generate_demo

    generate_demo(out_dir=tmp_path, seed=3)
    s = Settings(_env_file=None, env="demo")          # 키 없음/demo → sample 경로
    frames = load_frames(s, sample_dir=tmp_path)
    assert set(frames) == {"target_master", "post_data", "financial_wide", "company_sentiment", "macro"}
    assert len(frames["post_data"]) > 0
