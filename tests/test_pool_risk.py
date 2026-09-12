"""risk 纯函数(V8): 红黄旗分层(商誉/存贷双高代理/应收/并购代理) + 数据缺口诚实呈现。"""
from stockagent.pool import risk as rk

CFG = {"risk": {"goodwill_equity_red": 0.30, "dual_high_cash_pct": 0.15,
                "dual_high_debt_ratio": 0.40, "receivable_rev_yellow": 0.50,
                "goodwill_jump_yellow": 0.30}}


def _bal(cash=0.02, recv=0.1, ta=1.0, eq=0.5, dr=0.30):
    """合成 zcfz 行(总资产 1.0 亿归一)。"""
    return {"cash": cash * 1e8, "receivables": recv * 1e8, "inventory": 0.0,
            "total_assets": ta * 1e8, "total_liab": (ta - eq) * 1e8,
            "equity": eq * 1e8, "debt_ratio": dr}


# ---------- 红旗 ----------
def test_goodwill_over_equity_red():
    # 商誉 0.2 亿 / 净资产 0.5 亿 = 40% > 30% → 红旗
    r = rk.risk_flags(_bal(), goodwill_now=0.2e8, goodwill_series={},
                      rev_abs=1.0e8, period="20260630", cfg=CFG)
    assert "商誉/净资产过高" in r["red"]
    # 0.1/0.5 = 20% → 无红旗
    r2 = rk.risk_flags(_bal(), goodwill_now=0.1e8, goodwill_series={},
                       rev_abs=1.0e8, period="20260630", cfg=CFG)
    assert not r2["red"]


def test_dual_high_proxy_red():
    # 货币资金 20% 总资产 + 资产负债率 50% → 双高超阈 → 红旗
    r = rk.risk_flags(_bal(cash=0.20, dr=0.50), goodwill_now=None, goodwill_series={},
                      rev_abs=1.0e8, period="20260630", cfg=CFG)
    assert "存贷双高(代理口径)" in r["red"]
    # 现金高但负债率低(真有钱)→ 不触发
    r2 = rk.risk_flags(_bal(cash=0.20, dr=0.20), goodwill_now=None, goodwill_series={},
                       rev_abs=1.0e8, period="20260630", cfg=CFG)
    assert "存贷双高(代理口径)" not in r2["red"]
    # 负债率高但现金少(正常经营杠杆)→ 不触发
    r3 = rk.risk_flags(_bal(cash=0.05, dr=0.60), goodwill_now=None, goodwill_series={},
                       rev_abs=1.0e8, period="20260630", cfg=CFG)
    assert "存贷双高(代理口径)" not in r3["red"]


# ---------- 黄旗 ----------
def test_receivable_over_revenue_yellow():
    # 应收 0.6 / 营收 1.0 = 60% > 50% → 黄旗
    r = rk.risk_flags(_bal(recv=0.6), goodwill_now=None, goodwill_series={},
                      rev_abs=1.0e8, period="20260630", cfg=CFG)
    assert "应收/营收过高" in r["yellow"]
    # 0.4 → 无旗
    r2 = rk.risk_flags(_bal(recv=0.4), goodwill_now=None, goodwill_series={},
                       rev_abs=1.0e8, period="20260630", cfg=CFG)
    assert "应收/营收过高" not in r2["yellow"]


def test_goodwill_jump_mna_proxy_yellow():
    # 商誉 5 亿 → 8 亿(+60%)→ 并购代理黄旗
    r = rk.risk_flags(_bal(), goodwill_now=8e7,
                      goodwill_series={"20250630": 5e7, "20260630": 8e7},
                      rev_abs=1.0e8, period="20260630", cfg=CFG)
    assert "商誉激增(并购代理)" in r["yellow"]
    # +10% → 无旗
    r2 = rk.risk_flags(_bal(), goodwill_now=5.5e7,
                       goodwill_series={"20250630": 5e7, "20260630": 5.5e7},
                       rev_abs=1.0e8, period="20260630", cfg=CFG)
    assert "商誉激增(并购代理)" not in r2["yellow"]


def test_extra_yellow_flags_passthrough():
    r = rk.risk_flags(_bal(), goodwill_now=None, goodwill_series={},
                      rev_abs=None, period="20260630",
                      extra_yellow=["扭亏", "未精筛"], cfg=CFG)
    assert "扭亏" in r["yellow"] and "未精筛" in r["yellow"]


# ---------- 数据缺口(诚实呈现,不冒充安全) ----------
def test_data_gaps_reported():
    r = rk.risk_flags(None, goodwill_now=None, goodwill_series={},
                      rev_abs=None, period="20260630", cfg=CFG)
    assert not r["red"]
    assert "商誉未精筛" in r["data_gaps"]
    assert "资产负债表未披露" in r["data_gaps"]
    # 商誉有但报表缺 → 只报报表缺
    r2 = rk.risk_flags(None, goodwill_now=1e7, goodwill_series={},
                       rev_abs=None, period="20260630", cfg=CFG)
    assert "资产负债表未披露" in r2["data_gaps"]
    assert "商誉未精筛" not in r2["data_gaps"]
    # metrics 带原值
    r3 = rk.risk_flags(_bal(), goodwill_now=0.1e8, goodwill_series={},
                       rev_abs=1.0e8, period="20260630", cfg=CFG)
    assert abs(r3["metrics"]["goodwill_equity"] - 0.2) < 1e-9
    assert abs(r3["metrics"]["receivable_rev"] - 0.1) < 1e-9
