"""횡단 공통 레이어(common) — 모든 레이어가 참조하는 기반 모듈.

- config:      pydantic-settings 기반 설정 로딩 (BL_ 프리픽스), get_settings()
- compute:     NumPy/SciPy ↔ CuPy 배열 백엔드 디스패치 (GPU 유무=속도만 차이)
- logging:     구조적(JSON) 로깅 + 시크릿 마스킹
- io:          DuckDB/Parquet 읽기·쓰기, 멱등 upsert
- identifiers: ID crosswalk (corp_code↔biz_reg_no↔jurir_no↔stock_code; 직접 조인 금지)
"""
