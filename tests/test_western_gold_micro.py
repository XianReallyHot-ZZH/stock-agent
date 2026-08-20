"""黄金微观紧缺数据单测: 解析 helper + store 往返。"""
import datetime

import pandas as pd
import pytest

from stockagent.data import fetcher
from stockagent.data.store import Store


def _store(tmp_path):
    return Store(tmp_path / "t.sqlite")


# ---- COMEX 库存解析 ----
def test_parse_comex_inventory():
    df = pd.DataFrame([
        {"序号": 1, "日期": datetime.date(2026, 8, 5), "COMEX黄金库存量-吨": 838.0, "COMEX黄金库存量-盎司": 2.69e7},
        {"序号": 2, "日期": datetime.date(2026, 8, 6), "COMEX黄金库存量-吨": 835.5, "COMEX黄金库存量-盎司": 2.68e7},
    ])
    rows = fetcher.parse_comex_inventory(df, "黄金")
    assert len(rows) == 2
    assert rows[0] == {"symbol": "GC", "date": "2026-08-05", "tonnes": 838.0, "ounces": 2.69e7}
    assert rows[1]["symbol"] == "GC" and rows[1]["date"] == "2026-08-06"


def test_parse_comex_empty():
    assert fetcher.parse_comex_inventory(None, "黄金") == []
    assert fetcher.parse_comex_inventory(pd.DataFrame(), "黄金") == []


# ---- CFTC 非商业(投机)持仓解析 ----
def test_parse_cftc_speculative():
    df = pd.DataFrame([
        {"日期": "2026-08-04", "黄金-多头仓位": 250000, "黄金-空头仓位": 52000, "黄金-净仓位": 198000,
         "白银-多头仓位": 60000, "白银-空头仓位": 30000, "白银-净仓位": 30000},
    ])
    rows = fetcher.parse_cftc_speculative(df)
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["GC"]["net_pos"] == 198000 and by_sym["GC"]["date"] == "2026-08-04"
    assert by_sym["SI"]["net_pos"] == 30000


def test_parse_cftc_missing_col_skipped():
    # 无黄金列 → 该 symbol 跳过(不崩)
    df = pd.DataFrame([{"日期": "2026-08-04", "白银-多头仓位": 1, "白银-空头仓位": 1, "白银-净仓位": 0}])
    rows = fetcher.parse_cftc_speculative(df, cn_assets=("黄金", "白银"))
    assert all(r["symbol"] == "SI" for r in rows) and len(rows) == 1


def test_parse_cftc_commercial():
    # macro_usa_cftc_merchant_goods_holding: 商业持仓, 宽表同 c_holding; symbol 加 _M 后缀
    df = pd.DataFrame([
        {"日期": "2026-08-04", "黄金-多头仓位": 50000, "黄金-空头仓位": 276491, "黄金-净仓位": -226491,
         "白银-多头仓位": 10, "白银-空头仓位": 20, "白银-净仓位": -10},
    ])
    rows = fetcher.parse_cftc_commercial(df)
    by = {r["symbol"]: r for r in rows}
    assert by["GC_M"]["net_pos"] == -226491 and by["GC_M"]["date"] == "2026-08-04"
    assert by["SI_M"]["net_pos"] == -10


# ---- 央行黄金储备解析 ----
def test_parse_cb_gold():
    # macro_china_foreign_exchange_gold: 统计时间 'YYYY.M', 黄金储备=实物万盎司
    df = pd.DataFrame([
        {"统计时间": "2026.6", "黄金储备": 7544.0, "国家外汇储备": 34162.62},
        {"统计时间": "2026.7", "黄金储备": 7608.0, "国家外汇储备": 34187.76},
    ])
    rows = fetcher.parse_cb_gold(df)
    assert len(rows) == 2
    assert rows[1] == {"country": "CN", "date": "2026-07-01", "value": 7608.0, "yoy": None, "mom": None}


def test_norm_date_variants():
    assert fetcher._norm_date(datetime.date(2026, 8, 5)) == "2026-08-05"
    assert fetcher._norm_date("2026-08-04") == "2026-08-04"
    assert fetcher._norm_date("junk") is None


# ---- store 往返 ----
def test_store_roundtrip(tmp_path):
    st = _store(tmp_path)
    st.upsert_comex_inventory([{"symbol": "GC", "date": "2026-08-05", "tonnes": 838.0, "ounces": 2.69e7},
                               {"symbol": "GC", "date": "2026-08-06", "tonnes": 835.5, "ounces": 2.68e7}])
    ci = st.get_comex_inventory("GC")
    assert list(ci.index) == ["2026-08-05", "2026-08-06"]
    assert ci.iloc[-1] == 835.5

    st.upsert_cftc_position([{"symbol": "GC", "date": "2026-08-04", "long_pos": 250000,
                              "short_pos": 52000, "net_pos": 198000}])
    cf = st.get_cftc_position("GC")
    assert cf.loc["2026-08-04", "net_pos"] == 198000.0

    st.upsert_cb_gold([{"country": "CN", "date": "2026-07-01", "value": 3064.0, "yoy": 8.6, "mom": 0.3}])
    cb = st.get_cb_gold("CN")
    assert cb.loc["2026-07-01", "value"] == 3064.0


def test_store_empty_gets(tmp_path):
    st = _store(tmp_path)
    assert st.get_comex_inventory("GC").empty
    assert st.get_cftc_position("GC").empty
    assert st.get_cb_gold("CN").empty


# ---- 经济日历 ----
def test_parse_economic_calendar():
    df = pd.DataFrame([
        {"日期": "2026-08-12", "时间": "20:30", "地区": "美国", "事件": "美国7月CPI年率",
         "公布": 3.4, "预期": 3.5, "前值": 3.5, "重要性": 3},
        {"日期": "2026-08-12", "时间": "00:00", "地区": "美国", "事件": "美国8月EIA天然气产量",
         "公布": None, "预期": None, "前值": 1112.0, "重要性": 1},   # 低重要性被筛
    ])
    rows = fetcher.parse_economic_calendar(df, min_importance=2)
    assert len(rows) == 1
    assert rows[0]["event"] == "美国7月CPI年率" and rows[0]["actual"] == 3.4 and rows[0]["importance"] == 3


def test_store_economic_calendar(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    st.upsert_economic_calendar([
        {"date": "2026-08-12", "time": "20:30", "region": "美国", "event": "美国7月CPI年率",
         "actual": 3.4, "forecast": 3.5, "previous": 3.5, "importance": 3},
        {"date": "2026-09-17", "time": "02:00", "region": "美国", "event": "美联储公布利率决议",
         "actual": None, "forecast": None, "previous": 5.5, "importance": 3},
    ])
    df = st.get_economic_calendar(region="美国", min_importance=2)
    assert len(df) == 2
    rel = df[df["actual"].notna()]
    assert len(rel) == 1 and rel.iloc[0]["event"] == "美国7月CPI年率"
    assert st.get_economic_calendar(region="欧元区").empty


# ---- fetch_cb_gold 重试韧性(sina jsonp 分页端点偶发被拦) ----
def test_fetch_cb_gold_retry_then_ok(monkeypatch):
    """前两次被拦(JSONDecodeError), 第三次返回 → 退避重试后成功, 返回解析行。"""
    calls = {"n": 0}
    good = pd.DataFrame([{"统计时间": "2026.7", "黄金储备": 7608.0, "国家外汇储备": 34187.76}])

    def fake():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("No value to decode")   # 仿 sina 返回非 JSON
        return good

    monkeypatch.setattr(fetcher.ak, "macro_china_foreign_exchange_gold", fake)
    monkeypatch.setattr(fetcher.time, "sleep", lambda *_, **__: None)   # 不真睡
    rows = fetcher.fetch_cb_gold(retries=4)
    assert calls["n"] == 3            # 第三次成功, 没白跑
    assert len(rows) == 1 and rows[0]["value"] == 7608.0


def test_fetch_cb_gold_exhausts_raises(monkeypatch):
    """持续被拦 → 重试耗尽抛 FetchError(实物口径不变, 不静默吞)。"""
    monkeypatch.setattr(fetcher.ak, "macro_china_foreign_exchange_gold",
                        lambda *_, **__: (_ for _ in ()).throw(ValueError("No value to decode")))
    monkeypatch.setattr(fetcher.time, "sleep", lambda *_, **__: None)
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_cb_gold(retries=1)


# ---- _gold_micro_block: cb 空时占位(不静默漏图) ----
def test_gold_micro_block_cb_empty_placeholder(tmp_path):
    """有 COMEX 数据但无 cb → 第三张图走占位标题+说明, 不再消失。"""
    from stockagent.western_macro.dashboard import _LIGHT
    from stockagent.western_macro.framework import _gold_micro_block

    st = Store(tmp_path / "t.sqlite")
    st.upsert_comex_inventory([{"symbol": "GC", "date": "2026-08-07", "tonnes": 838.0, "ounces": 2.69e7}])
    html = _gold_micro_block(st, _LIGHT, first=[False])
    assert "中国央行黄金储备" in html          # 标题仍在(第三张图位不空)
    assert "央行购金数据暂缺" in html           # 占位说明
    assert "micro_cb" not in html               # 无 cb 图(plotly div 未生成)


def test_framework_calendar_upcoming_not_truncated(tmp_path):
    """未来排期表不得截断(2026-08 修复: head(15) 曾把 FOMC/非农/CPI 砍掉), 全量展示到源排期上限。"""
    from stockagent.western_macro import framework
    st = Store(tmp_path / "t.sqlite")
    rows = [
        {"date": "2026-08-19", "time": "20:30", "region": "美国", "event": "美国7月CPI年率(%)",
         "actual": 3.0, "forecast": 3.1, "previous": 3.2, "importance": 3},
    ]
    # 19 条未来事件(超过旧 head(15)) + 最远一条 FOMC 利率决议
    for i in range(19):
        d = (datetime.date(2026, 8, 20) + datetime.timedelta(days=i)).isoformat()
        rows.append({"date": d, "time": "20:30", "region": "美国",
                     "event": "美国当周初请失业金人数(万)",
                     "actual": None, "forecast": None, "previous": 22.0, "importance": 2})
    rows.append({"date": "2026-09-17", "time": "02:00", "region": "美国",
                 "event": "美国9月联邦基金利率目标上限(%)",
                 "actual": None, "forecast": None, "previous": 4.0, "importance": 3})
    st.upsert_economic_calendar(rows)
    c = {"miss": "#c00", "edge": "#080", "ink2": "#666"}
    html = framework._economic_calendar_block(st, c, "2026-08-20")
    assert html.count("初请失业金") == 19          # 无截断: 19 条全在
    assert "2026-09-17" in html                    # 最远催化剂(FOMC)在表内
    assert "排期至 2026-09-17" in html             # 标注实际排期上限
