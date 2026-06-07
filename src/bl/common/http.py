"""HTTP 헬퍼 — 공식 API 호출용 GET(JSON) + 지수 백오프 재시도 + 시크릿 마스킹.

설계: No-Crawl(공식 API만), 시크릿은 URL/로그에 평문 노출 금지(과거 ECOS 키 노출 교정).
"""

from __future__ import annotations

import time
from typing import Any

from bl.common.logging import get_logger, mask_secrets

log = get_logger(__name__)


def get_json(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    *,
    max_retries: int = 3,
    backoff: float = 0.5,
    timeout: float = 20.0,
) -> Any:
    """GET → JSON. 일시 오류는 지수 백오프 재시도. 로그에 키 마스킹.

    requests 미설치/네트워크 불가 환경에서는 호출 시점에 예외가 난다(라이브 전용).
    """
    import requests

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001 (재시도 목적의 광역 캐치)
            last_exc = e
            wait = backoff * (2**attempt)
            log.warning(
                mask_secrets(f"GET 실패({attempt + 1}/{max_retries}) {url}: {e} → {wait:.1f}s 재시도"),
                extra={"stage": "http.get_json"},
            )
            if attempt < max_retries - 1:
                time.sleep(wait)
    raise RuntimeError(mask_secrets(f"GET 최종 실패: {url} ({last_exc})"))
