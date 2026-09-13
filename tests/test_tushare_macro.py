"""tushare 迁移 1.2-1.6 金十宏观族: 映射/keep_null 保留/回退链 (ADR-0002)。无网络。"""
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from stockagent.data import fetcher, manager as mgr
from stockagent.data import tushare_client as tc
from stockagent.data.store import Store


# ---------- fetcher 映射 ----------

def _shibor_raw() -> pd.DataFrame:
    return pd.DataFrame({"date": ["20260911", "20260910"],
                         "on": [1.417, 1.404], "1w": [1.42, 1.41], "2w": [1.407, 1.406],
                         "1m": [1.4189, 1.4189], "3m": [1.43, 1.43], "6m": [1.452, 1.451],
                         "9m": [1.47, 1.47], "1y": [1.48, 1.48]})


def test_fetch_shibor_tushare_maps_terms(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: _shibor_raw())
    rows = fetcher.fetch_shibor_tushare()
    assert rows[0]["date"] == "2026-09-11"   # 忠实保留 tushare 源序(新→旧)
    assert rows[0]["overnight"] == 1.417 and rows[0]["y1"] == 1.48
    assert set(rows[0]) == {"date", "overnight", "w1", "w2", "m1", "m3", "m6", "m9", "y1"}


def _lpr_raw(cols=("lpr_1y", "lpr_5y")) -> pd.DataFrame:
    d = {"trade_date": ["20260820", "20260720"]}
    d[cols[0]] = [3.0, 3.0]
    d[cols[1]] = [3.5, 3.5]
    return pd.DataFrame(d)


def test_fetch_lpr_tushare_col_variants(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: _lpr_raw())
    rows = fetcher.fetch_lpr_tushare()
    assert rows[0] == {"date": "2026-08-20", "lpr1y": 3.0, "lpr5y": 3.5,
                       "base1y": None, "base5y": None}
    monkeypatch.setattr(tc, "query", lambda api, **kw: _lpr_raw(("lpr1y", "lpr5y")))
    assert fetcher.fetch_lpr_tushare()[0]["lpr5y"] == 3.5


def test_fetch_lpr_tushare_bad_cols_raise(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: pd.DataFrame({"x": [1]}))
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_lpr_tushare()


def test_fetch_money_supply_tushare_maps(monkeypatch):
    raw = pd.DataFrame({"month": ["202607", "202606"], "m0": [148202.86, 147364.79],
                        "m0_yoy": [11.6, 11.8], "m1": [1154623.0, 1184775.53],
                        "m1_yoy": [4.0, 4.0], "m2": [3555077.24, 3567108.43],
                        "m2_yoy": [7.7, 8.0]})
    monkeypatch.setattr(tc, "query", lambda api, **kw: raw)
    rows = fetcher.fetch_money_supply_tushare()
    assert rows[0]["month"] == "2026-07-01"   # 忠实保留 tushare 源序(新→旧)
    assert abs(rows[0]["m2_amt"] - 3555077.24) < 1e-6 and rows[0]["m2_yoy"] == 7.7
    assert set(rows[0]) == {"month", "m2_amt", "m2_yoy", "m1_amt", "m1_yoy",
                            "m0_amt", "m0_yoy"}


def test_fetch_cpi_ppi_tushare_maps(monkeypatch):
    cpi = pd.DataFrame({"month": ["202607"], "nt_yoy": [0.5]})
    ppi = pd.DataFrame({"month": ["202607"], "ppi_yoy": [3.5]})
    monkeypatch.setattr(tc, "query", lambda api, **kw: cpi if api == "cn_cpi" else ppi)
    out = fetcher.fetch_cpi_ppi_tushare()
    assert out["cpi_yoy"] == [{"month": "2026-07-01", "value": 0.5}]
    assert out["ppi_yoy"] == [{"month": "2026-07-01", "value": 3.5}]


# ---------- store keep_null ----------

def _store() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


def test_upsert_china_tsf_keep_null_preserves_fenxiang():
    """tushare 应急腿(仅 tsf_inc)不得抹金十分项存量。"""
    s = _store()
    s.upsert_china_tsf([{"month": "2026-07-01", "tsf_inc": 14000.0,
                         "rmb_loans": 8000.0, "corp_bond": 2000.0, "equity_fin": 500.0}])
    s.upsert_china_tsf([{"month": "2026-07-01", "tsf_inc": 14017.0,
                         "rmb_loans": None, "corp_bond": None, "equity_fin": None}])
    df = s.get_china_tsf_series()
    assert abs(float(df.loc["2026-07-01", "tsf_inc"]) - 14017.0) < 1e-6   # 更新
    assert abs(float(df.loc["2026-07-01", "rmb_loans"]) - 8000.0) < 1e-6  # 保留
    assert abs(float(df.loc["2026-07-01", "corp_bond"]) - 2000.0) < 1e-6


def test_upsert_lpr_keep_null_preserves_base():
    s = _store()
    s.upsert_lpr([{"date": "2015-05-11", "lpr1y": None, "lpr5y": None,
                   "base1y": 5.1, "base5y": 5.65}])
    s.upsert_lpr([{"date": "2015-05-11", "lpr1y": 4.55, "lpr5y": 5.05,
                   "base1y": None, "base5y": None}])
    df = s.get_lpr_series()
    assert abs(float(df.loc["2015-05-11", "base1y"]) - 5.1) < 1e-6   # 保留
    assert abs(float(df.loc["2015-05-11", "lpr1y"]) - 4.55) < 1e-6   # 写入


# ---------- manager 回退链 ----------

def _dm() -> tuple[mgr.DataManager, Store]:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    st = Store(Path(f.name))
    return mgr.DataManager(store=st), st


def test_ts_first_no_token_uses_jin10(monkeypatch):
    dm, st = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: False)
    calls = {"ts": 0, "j10": 0}

    def ts():
        calls["ts"] += 1
        raise AssertionError("不应走 tushare(无 token)")
    rows = dm._ts_first(ts, lambda: (calls.__setitem__("j10", calls["j10"] + 1) or
                                     [{"month": "2026-07-01"}]))
    assert rows and calls == {"ts": 0, "j10": 1}


def test_ts_first_falls_back_on_failure(monkeypatch):
    dm, st = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: True)

    def ts():
        raise fetcher.TushareError("限频")
    rows = dm._ts_first(ts, lambda: [{"month": "2026-07-01"}])
    assert rows == [{"month": "2026-07-01"}]


def test_ts_first_prefers_tushare(monkeypatch):
    dm, st = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: True)

    def j10():
        raise AssertionError("金十不应被调用(tushare 优先)")
    rows = dm._ts_first(lambda: [{"month": "2026-08-01"}], j10)
    assert rows == [{"month": "2026-08-01"}]


def test_update_china_real_tushare_skips_jin10_cpi_ppi(monkeypatch):
    """CPI/PPI tushare 命中 → 金十这两腿不再调用(skip 生效),PMI 等仍走金十。"""
    dm, st = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_cpi_ppi_tushare",
                        lambda: {"cpi_yoy": [{"month": "2026-07-01", "value": 0.5}],
                                 "ppi_yoy": [{"month": "2026-07-01", "value": 3.5}]})
    seen_skip = {}

    def fake_real(retries=2, skip=()):
        seen_skip["skip"] = skip
        return {"pmi": [{"month": "2026-07-01", "value": 49.3}],
                "cpi_yoy": None, "ppi_yoy": None, "retail_yoy": None, "ind_yoy": None}
    monkeypatch.setattr(fetcher, "fetch_china_real", fake_real)
    out = dm.update_china_real()
    assert seen_skip["skip"] == ("cpi_yoy", "ppi_yoy")
    assert out["cpi_yoy"] > 0 and out["pmi"] > 0
    assert float(st.get_macro_monthly("cpi_yoy").iloc[-1]) == 0.5
    assert float(st.get_macro_monthly("ppi_yoy").iloc[-1]) == 3.5


def test_fetch_china_real_skip_param(monkeypatch):
    res = fetcher.fetch_china_real(skip=("cpi_yoy", "ppi_yoy", "pmi",
                                         "retail_yoy", "ind_yoy"))
    assert all(v is None for v in res.values())


def test_j10_fallback_uses_tushare_on_jin10_failure(monkeypatch):
    """shibor 接线: 金十主源,挂了才 tushare(对账判金十优)。"""
    dm, st = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: True)

    def j10():
        raise fetcher.FetchError("金十被拦")
    rows = dm._j10_fallback(j10, lambda: [{"date": "2026-09-11"}])
    assert rows == [{"date": "2026-09-11"}]


def test_j10_fallback_no_token_reraises(monkeypatch):
    dm, st = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: False)
    with pytest.raises(fetcher.FetchError):
        dm._j10_fallback(lambda: (_ for _ in ()).throw(fetcher.FetchError("拦")), None)


def test_j10_fallback_jin10_ok_no_tushare(monkeypatch):
    dm, st = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: True)

    def ts():
        raise AssertionError("tushare 不应被调用(金十优先)")
    assert dm._j10_fallback(lambda: [{"date": "2026-09-11"}], ts) == [{"date": "2026-09-11"}]
