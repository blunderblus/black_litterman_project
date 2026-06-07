"""연산 백엔드 디스패치 테스트 — '동일 로직·동일 수치, 속도만 차이' 계약(ADR-0001)."""

from __future__ import annotations

import numpy as np
import pytest

from bl.common import compute


def test_cpu_backend_is_numpy() -> None:
    assert compute.get_array_module("cpu") is np
    assert compute.resolve_backend("cpu") == "cpu"


def test_auto_backend_valid() -> None:
    # GPU 유무와 무관하게 항상 cpu/gpu 중 하나로 결정되어야 한다.
    assert compute.resolve_backend("auto") in ("cpu", "gpu")
    # GPU 미설치 환경에서 'gpu' 요청은 안전하게 cpu로 폴백.
    if not compute.gpu_available():
        assert compute.resolve_backend("gpu") == "cpu"


def test_asnumpy_roundtrip() -> None:
    a = np.arange(6.0).reshape(2, 3)
    b = compute.asarray(a, "cpu")
    np.testing.assert_array_equal(compute.asnumpy(b), a)


def test_assert_parity_identical() -> None:
    a = np.linspace(0, 1, 50)
    compute.assert_parity(a, a.copy())  # rtol=1e-8 통과해야 함
    with pytest.raises(AssertionError):
        compute.assert_parity(a, a + 1e-3)


def test_backend_info_keys() -> None:
    info = compute.active_backend_info("cpu")
    assert info["backend"] == "cpu"
    assert info["xp"] == "numpy"
    assert info["parity_rtol"] == 1e-8
