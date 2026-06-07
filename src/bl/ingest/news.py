"""뉴스 수집(Track B) — Naver 뉴스 API → news 프레임(corp_code, title, description, pub_date).

설계 02 §1(No-Crawl 공식 API). 키 게이팅(BL_NAVER_CLIENT_ID/SECRET). 파싱(parse_naver) 분리.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pandas as pd

from bl.common.logging import get_logger

if TYPE_CHECKING:
    from bl.common.config import Settings

log = get_logger(__name__)

NAVER_URL = "https://openapi.naver.com/v1/search/news.json"
_TAG = re.compile(r"<[^>]+>")


def _strip(s: str) -> str:
    return _TAG.sub("", str(s)).replace("&quot;", '"').replace("&amp;", "&").strip()


def parse_naver(payload: dict, corp_code: str) -> list[dict]:
    """Naver 뉴스 응답 → [{corp_code, title, description, pub_date}] (순수·테스트 가능)."""
    out: list[dict] = []
    for it in (payload or {}).get("items", []):
        out.append({
            "corp_code": corp_code,
            "title": _strip(it.get("title", "")),
            "description": _strip(it.get("description", "")),
            "pub_date": it.get("pubDate", ""),
        })
    return out


def collect_news(settings: "Settings", targets: list[tuple[str, str]], display: int = 20) -> pd.DataFrame:
    """targets=[(corp_code, query)] 로 Naver 뉴스를 수집해 news 프레임 반환(라이브, 키 필요)."""
    from bl.common.http import get_json

    cid = settings.naver_client_id.get_secret_value() if settings.naver_client_id else None
    csec = settings.naver_client_secret.get_secret_value() if settings.naver_client_secret else None
    if not (cid and csec):
        raise ValueError("BL_NAVER_CLIENT_ID/SECRET 미설정 — 뉴스 수집 불가(데모는 sample 사용).")
    headers = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec}
    display = max(1, min(int(display), 100))           # Naver display 상한 100
    rows: list[dict] = []
    for cc, query in targets:
        payload = get_json(NAVER_URL, params={"query": query, "display": display, "sort": "date"},
                           headers=headers)
        rows.extend(parse_naver(payload, cc))
    log.info("Naver 뉴스 수집", extra={"stage": "ingest.news", "targets": len(targets), "rows": len(rows)})
    return pd.DataFrame(rows, columns=["corp_code", "title", "description", "pub_date"])
