"""얇은 CLI 엔트리포인트 — 로직은 각 레이어 모듈에 위임한다.

예: ``bl-run universe`` / ``python -m bl.cli ingest-financial``
실제 디스패치는 P1+ 단계에서 각 스테이지 함수에 연결한다(현재 P0 스캐폴드).
"""

from __future__ import annotations

import argparse

from bl.common.compute import active_backend_info
from bl.common.config import get_settings
from bl.common.logging import configure_logging, get_logger

STAGES = [
    "universe",
    "ingest-financial",
    "ingest-macro",
    "ingest-news",
    "ingest-post",
    "refine",
    "enrich",
    "features",
    "models",
    "bl-inputs",
    "bl-optimize",
    "serve",
]


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    log = get_logger("bl.cli")
    parser = argparse.ArgumentParser(prog="bl-run", description="BL 파이프라인 실행기")
    parser.add_argument("stage", choices=STAGES, help="실행할 파이프라인 스테이지")
    parser.add_argument("--base-ym", type=int, default=None, help="기준 연월(YYYYMM)")
    args = parser.parse_args(argv)

    settings = get_settings()
    log.info(
        f"selected stage: {args.stage}",
        extra={
            "stage": args.stage,
            "env": settings.env,
            **active_backend_info(settings.compute_backend),
        },
    )
    # TODO(P1+): args.stage -> 각 레이어 함수 디스패치
    raise SystemExit(f"stage '{args.stage}'는 아직 미구현(P0 스캐폴드). 로드맵 P1+에서 연결.")


if __name__ == "__main__":
    main()
