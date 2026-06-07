"""연산 백엔드 디스패치 — NumPy/SciPy(CPU) ↔ CuPy(GPU).

핵심 원칙(설계: docs/design/04-compute-design.md, ADR-0001):
**"동일 로직·동일 수치, GPU 유무는 속도만 차이"**.

- ``xp = get_array_module()`` 로 NumPy 또는 CuPy 모듈을 얻어 동일 코드로 작성한다.
- GPU가 없거나 CuPy 미설치면 안전하게 CPU(NumPy)로 폴백한다.
- 모든 핵심 산출물(Σ, E[R], w*)은 CPU/GPU 간 상대오차 ``rtol < 1e-8`` 를
  ``assert_parity`` 회귀 테스트로 보장한다(반복형 QP 솔버 수렴오차는 별도).

GPU 가속이 큰 구간: FULL 공분산(N×T), (τΣ)^-1·posterior 선형해, 최적화 반복.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

import numpy as np

PARITY_RTOL = 1e-8  # CPU/GPU 수치 동치 계약(설계 표준)
Backend = Literal["cpu", "gpu"]


@lru_cache(maxsize=1)
def gpu_available() -> bool:
    """CuPy가 설치되어 있고 사용 가능한 CUDA 디바이스가 있으면 True."""
    try:
        import cupy  # type: ignore

        return cupy.cuda.runtime.getDeviceCount() > 0
    except Exception:
        return False


def resolve_backend(preference: str | None = None) -> Backend:
    """설정/인자(auto|cpu|gpu)와 실제 가용성으로 최종 백엔드를 결정한다.

    auto: GPU 가용 시 'gpu', 아니면 'cpu'. gpu 요청인데 미가용이면 'cpu'로 폴백.
    """
    pref = (preference or "auto").lower()
    if pref == "cpu":
        return "cpu"
    if pref == "gpu":
        return "gpu" if gpu_available() else "cpu"
    # auto
    return "gpu" if gpu_available() else "cpu"


def get_array_module(preference: str | None = None) -> Any:
    """활성 배열 모듈(xp)을 반환한다: CuPy(gpu) 또는 NumPy(cpu).

    >>> xp = get_array_module("cpu")  # -> numpy
    """
    if resolve_backend(preference) == "gpu":
        import cupy  # type: ignore

        return cupy
    return np


def get_scipy_module(preference: str | None = None) -> Any:
    """활성 SciPy 모듈을 반환한다: cupyx.scipy(gpu) 또는 scipy(cpu)."""
    if resolve_backend(preference) == "gpu":
        import cupyx.scipy as cpx  # type: ignore

        return cpx
    import scipy  # noqa: F401

    return scipy


def asarray(x: Any, preference: str | None = None) -> Any:
    """입력을 활성 백엔드 배열로 올린다(CPU면 np.asarray, GPU면 cupy.asarray)."""
    return get_array_module(preference).asarray(x)


def asnumpy(x: Any) -> np.ndarray:
    """배열을 NumPy로 내린다(CuPy면 cupy.asnumpy, 아니면 np.asarray)."""
    try:
        import cupy  # type: ignore

        if isinstance(x, cupy.ndarray):
            return cupy.asnumpy(x)
    except Exception:
        pass
    return np.asarray(x)


def assert_parity(a: Any, b: Any, rtol: float = PARITY_RTOL, atol: float = 0.0) -> None:
    """두 배열(백엔드 무관)이 ``rtol`` 내에서 동일한지 검증한다(회귀 테스트용)."""
    np.testing.assert_allclose(asnumpy(a), asnumpy(b), rtol=rtol, atol=atol)


def active_backend_info(preference: str | None = None) -> dict[str, Any]:
    """현재 백엔드 진단 정보(로깅/디버깅용)."""
    backend = resolve_backend(preference)
    return {
        "backend": backend,
        "gpu_available": gpu_available(),
        "xp": "cupy" if backend == "gpu" else "numpy",
        "parity_rtol": PARITY_RTOL,
    }
