"""설정 로딩 — pydantic-settings 기반, 환경변수 프리픽스 ``BL_``.

권위 소스: docs/design/01-system-architecture.md §8.1 (설정 키 표).
모든 경로/파라미터/시크릿은 여기서 단일화하며, Colab/Drive 하드코딩 경로를 대체한다.

사용:
    from bl.common.config import get_settings
    settings = get_settings()
    settings.duckdb_path  # -> Path
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ComputeBackend = Literal["auto", "cpu", "gpu"]
RunEnv = Literal["dev", "operational", "demo"]


class Settings(BaseSettings):
    """프로젝트 전역 설정. ``.env`` 또는 ``BL_*`` 환경변수에서 로딩한다."""

    model_config = SettingsConfigDict(
        env_prefix="BL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 실행/경로 ----
    env: RunEnv = "dev"
    data_root: Path = Path("data")
    artifacts_dir: Path | None = None  # 미지정 시 data_root/artifacts 로 파생
    duckdb_path: Path | None = None    # 미지정 시 data_root/raw_collection.duckdb 로 파생

    # ---- 연산 백엔드 ----
    compute_backend: ComputeBackend = "auto"
    seed: int = 42

    # ---- 수집 동작 ----
    full_rebuild: bool = False
    only_empty: bool = True

    # ---- 외부 API 시크릿(선택; 미설정 시 해당 수집 단계에서만 필요) ----
    dart_api_key: SecretStr | None = None
    ecos_api_key: SecretStr | None = None
    naver_client_id: SecretStr | None = None
    naver_client_secret: SecretStr | None = None
    bigkinds_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = Field(default=None)

    @model_validator(mode="after")
    def _derive_paths(self) -> Settings:
        """미지정 경로를 data_root 기준으로 파생한다(설계 §8.1 기본값)."""
        if self.artifacts_dir is None:
            self.artifacts_dir = self.data_root / "artifacts"
        if self.duckdb_path is None:
            self.duckdb_path = self.data_root / "raw_collection.duckdb"
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스 단위로 캐시된 Settings 인스턴스를 반환한다.

    테스트에서 환경변수를 바꿔 재로딩하려면 ``get_settings.cache_clear()`` 호출.
    """
    return Settings()
