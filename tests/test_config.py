"""설정 로딩 테스트 — BL_ 프리픽스, 경로 파생, 기본값(설계 01 §8.1)."""

from __future__ import annotations

from pathlib import Path

from bl.common.config import Settings, get_settings


def _fresh(**env: str) -> Settings:
    """캐시/환경 영향 없이 명시 값으로 Settings를 만든다."""
    get_settings.cache_clear()
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


def test_defaults_and_derived_paths() -> None:
    s = _fresh(data_root="data")
    assert s.env == "dev"
    assert s.compute_backend == "auto"
    assert s.seed == 42
    assert s.full_rebuild is False and s.only_empty is True
    # 파생 경로
    assert s.artifacts_dir == Path("data") / "artifacts"
    assert s.duckdb_path == Path("data") / "raw_collection.duckdb"


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("BL_SEED", "123")
    monkeypatch.setenv("BL_COMPUTE_BACKEND", "cpu")
    monkeypatch.setenv("BL_DATA_ROOT", "/tmp/blroot")
    get_settings.cache_clear()
    s = Settings(_env_file=None)
    assert s.seed == 123
    assert s.compute_backend == "cpu"
    assert s.duckdb_path == Path("/tmp/blroot") / "raw_collection.duckdb"
    get_settings.cache_clear()


def test_secrets_optional_and_masked() -> None:
    s = _fresh(data_root="data", dart_api_key="SECRET123")
    # SecretStr 는 repr에 값이 노출되지 않아야 한다.
    assert "SECRET123" not in repr(s)
    assert s.dart_api_key is not None
    assert s.dart_api_key.get_secret_value() == "SECRET123"
