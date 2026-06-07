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
    "demo",            # 합성 데이터로 전 파이프라인 실행 → site/ 대시보드(키 불필요)
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
    if args.stage == "demo":
        from bl.pipeline import run_demo

        r = run_demo(out_dir="site", base_ym=args.base_ym or 202510)
        m = r["mart"]
        log.info(
            "demo 완료",
            extra={
                "stage": "cli.demo",
                "assets": int(len(m)),
                "active_leads": int((m["marketing_score"] >= 80).sum()),
                "html": r.get("html_path"),
            },
        )
        return 0

    # TODO(P1+): 나머지 스테이지는 ingest/features/models 연동 후 디스패치
    raise SystemExit(f"stage '{args.stage}'는 아직 미연결. 데모는 `bl-run demo` 사용.")


if __name__ == "__main__":
    main()
