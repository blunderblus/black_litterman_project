"""P0 스모크 테스트 — 패키지 import 및 레이아웃 무결성(설계 01 §6과 일치)."""

from __future__ import annotations

import importlib

import bl


def test_version() -> None:
    assert isinstance(bl.__version__, str)
    assert bl.__version__.count(".") >= 1


def test_layer_packages_importable() -> None:
    """선언된 모든 레이어 패키지가 import 가능해야 한다(스텁 포함)."""
    for pkg in [
        "bl.common",
        "bl.universe",
        "bl.ingest",
        "bl.refine",
        "bl.enrich",
        "bl.features",
        "bl.models",
        "bl.engine",
        "bl.serve",
        "bl.cli",
    ]:
        assert importlib.import_module(pkg) is not None


def test_cli_stage_list() -> None:
    from bl.cli import STAGES

    assert "bl-optimize" in STAGES
    assert "universe" in STAGES
