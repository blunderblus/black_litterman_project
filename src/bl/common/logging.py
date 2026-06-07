"""구조적(JSON) 로깅 + 시크릿 마스킹.

설계: docs/design/01-system-architecture.md §8.2.
각 스테이지는 ``stage, input_rows, output_rows, as_of, duration_ms, backend`` 필드를
구조적으로 남긴다(과거 토이의 평문 로그·API키 노출 결함 차단).

사용:
    from bl.common.logging import configure_logging, get_logger, log_stage
    configure_logging()
    log = get_logger(__name__)
    log_stage(log, "ingest.financial", input_rows=0, output_rows=2753, backend="cpu")
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

# 로그 문자열에서 마스킹할 시크릿 흔적(키=값/URL 파라미터). 과거 ECOS 키 노출 방지.
_SECRET_PATTERNS = [
    re.compile(r"(crtfc_key|api[_-]?key|client[_-]?secret|authkey|serviceKey)=([^&\s\"]+)", re.I),
    re.compile(r"(Bearer)\s+([A-Za-z0-9\.\-_]+)", re.I),
]


def mask_secrets(text: str) -> str:
    """문자열 내 키=값/토큰을 ``***`` 로 마스킹한다."""
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(r"\1=***", out)
    return out


class JsonFormatter(logging.Formatter):
    """레코드를 1줄 JSON으로 직렬화한다. extra 필드를 그대로 포함한다."""

    _RESERVED = set(
        vars(logging.makeLogRecord({})).keys()
    ) | {"message", "asctime", "taskName"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": mask_secrets(record.getMessage()),
        }
        # log_stage 등으로 들어온 구조적 extra 필드 병합(문자열 값도 시크릿 마스킹)
        for k, v in record.__dict__.items():
            if k not in self._RESERVED and not k.startswith("_"):
                payload[k] = mask_secrets(v) if isinstance(v, str) else v
        if record.exc_info:
            payload["exc"] = mask_secrets(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int | str = logging.INFO) -> None:
    """루트 로거를 JSON 포매터로 1회 구성한다(중복 핸들러 방지)."""
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """이름 있는 로거를 반환한다."""
    return logging.getLogger(name)


def log_stage(logger: logging.Logger, stage: str, **fields: Any) -> None:
    """파이프라인 스테이지 1건을 구조적으로 기록한다.

    권장 필드: input_rows, output_rows, as_of, duration_ms, backend.
    """
    logger.info(f"stage:{stage}", extra={"stage": stage, **fields})
