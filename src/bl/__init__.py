"""BL — AI 기반 Black-Litterman 법인 마케팅 최적화 시스템.

자산=법인 고객, 기대수익=예금유치·유지 가치(CLV proxy), 시장가중치=지갑(예금) 규모 비중,
투자자 전망(View)=AI 3축 신호(news=Gemini 감성·pattern=XGBoost 성장/이탈·relationship=거래관계),
전망 불확실성 Ω=데이터신뢰도(DRI)·모델 confidence·이상도(IsolationForest anomaly).

설계 문서: docs/design/01-system-architecture.md (패키지 레이아웃 권위 소스).

패키지 import 시 무거운 의존성(pydantic-settings, numpy 등)을 끌어오지 않도록
최상위 __init__ 은 버전만 노출한다. 설정/연산은 명시적으로 하위 모듈에서 가져온다:

    from bl.common.config import get_settings
    from bl.common.compute import get_array_module
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
