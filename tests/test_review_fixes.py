"""적대적 리뷰 수정 회귀 테스트 — 누수/크래시/키노출/직렬화/look-ahead 재발 방지."""

from __future__ import annotations

import numpy as np
import pandas as pd


def test_walk_forward_embargo_excludes_horizon():
    from bl.models.validation import walk_forward_splits

    df = pd.DataFrame({"base_ym": sorted([202301 + i for i in range(12)] * 2)})
    no_emb = walk_forward_splits(df, "base_ym", n_splits=2, embargo=0)
    emb = walk_forward_splits(df, "base_ym", n_splits=2, embargo=3)
    # embargo 적용 시 각 분할 train 크기가 같거나 작아야(직전 호라이즌 제외)
    assert emb and no_emb
    assert all(len(e[0]) <= len(n[0]) for e, n in zip(emb, no_emb, strict=False))
    assert any(len(e[0]) < len(n[0]) for e, n in zip(emb, no_emb, strict=False))


def test_ecos_key_masked_in_path():
    from bl.common.logging import mask_secrets

    url = "https://ecos.bok.or.kr/api/StatisticSearch/MYSECRETKEY123/json/kr/1/1000/722Y001"
    assert "MYSECRETKEY123" not in mask_secrets(url)


def test_dashboard_clean_handles_pd_na():
    from bl.serve.dashboard_data import _clean

    assert _clean(pd.NA) is None
    assert _clean(pd.NaT) is None
    assert _clean(np.bool_(True)) is True
    assert _clean(float("nan")) is None
    assert _clean(np.float64(1.23456789)) == 1.234568


def test_returns_panel_robust_to_int_corp_and_zero_bal():
    from bl.pipeline import _returns_panel

    # corp_code 가 int 로 저장되어도(실DB export 흔함) 매칭되고, 0 잔액 자산은 제외
    post = pd.DataFrame({
        "corp_code": [1, 1, 1, 2, 2, 2],
        "base_ym": [202301, 202302, 202303] * 2,
        "bal": [100.0, 110.0, 121.0, 0.0, 50.0, 60.0],   # corp 2 에 0 잔액 → 비유효
    })
    panel, valid = _returns_panel(post, ["1", "2"], 202303)
    assert valid == ["1"]                                 # 0 잔액 corp 제외
    assert np.isfinite(panel).all() and panel.shape[1] == 1


def test_financial_paren_negative():
    from bl.ingest.financial import _to_num

    assert _to_num("(1,234)") == -1234.0
    assert _to_num("1,234") == 1234.0
    assert _to_num("x") is None


def test_financial_features_no_lookahead():
    # 공시 가용월 이전(base_ym < fin_ym+lag)에는 재무 피처가 비어 있어야(look-ahead 차단)
    from bl.common.dates import ym_add
    from bl.features.builder import FIN_DISCLOSURE_LAG, build_features_from_frames

    post = pd.DataFrame({
        "corp_code": ["00000001"] * 6,
        "base_ym": [202401, 202402, 202403, 202404, 202405, 202406],
        "bal": [100, 110, 120, 130, 140, 150.0],
        "trx_cnt_in_6m": [5] * 6, "trx_cnt_out_6m": [4] * 6,
        "payroll_yn": [1] * 6, "main_bank_yn": [0] * 6,
    })
    fin = pd.DataFrame({"corp_code": ["00000001"], "base_ym": [202403],
                        "revenue": [1e9], "operating_profit": [1e8], "net_income": [5e7],
                        "total_assets": [2e9], "total_liabilities": [1e9], "total_equity": [1e9],
                        "cash_amount": [3e8]})
    macro = pd.DataFrame({"metric_code": ["BASE_RATE"], "base_ym": [202403], "value": [3.5]})
    tm = pd.DataFrame({"corp_code": ["00000001"], "TIER": ["T1"], "sector_code": ["x"], "stock_code": ["005930"]})
    feat = build_features_from_frames(post, fin, macro, tm, 202406)
    avail = ym_add(202403, FIN_DISCLOSURE_LAG)            # 202406
    tr = feat["train"]
    before = tr[tr["base_ym"] < avail]
    assert before["revenue"].isna().all()                 # 공시 전 재무 비어있음
