"""Tests for 仓位管理看板(tracker/position.py · 第五看板 · 估值档×预案对照)。

覆盖:
- _rolling_pct: 滚动分位端点/min_periods 留白/NaN 当前值
- classify_zone: 四档四分支 + 跨中线 + PE-only fallback + NaN
- valuation_zone_series: 首 252 日留白 + 末值 parity(与 diagnose.diagnose_valuation 快照对齐)
- _zone_runs/zone_snapshot/recent_switches: 连续段/切换事件(空档不断段)/在档天数
- zone_stats: 占比/前向收益手算对照/末端不足窗口丢弃
- load_position_plan: 合法解析 + 非法键/缺键/min>max 报错
- render_position_report: smoke(合成 store,含 section 标记/错误横幅/只读声明)

No network — 合成 Series/DataFrame 喂入。
"""
import numpy as np
import pandas as pd
import pytest

from stockagent.tracker import diagnose as dz
from stockagent.tracker import position as p


def _dates(n, start="2025-12-31"):
    return pd.date_range(start, periods=n, freq="B").strftime("%Y-%m-%d")


# ---------- 滚动分位 ----------

def test_rolling_pct_endpoints_monotonic():
    n = 300
    s = pd.Series(np.arange(n, dtype=float), index=_dates(n))
    pct = p._rolling_pct(s, lookback_years=10, min_periods=252)
    assert pct.iloc[:251].isna().all()          # min_periods 前留白
    # 第 252 个点:窗口 252 个值,严格小于末值的有 251 个
    assert abs(pct.iloc[251] - 251 / 252) < 1e-12
    # 末点:窗口 = 全部 300 个(不足 10 年用可得历史),299/300
    assert abs(pct.iloc[-1] - 299 / 300) < 1e-12


def test_rolling_pct_nan_current_not_zero():
    n = 260
    s = pd.Series(np.arange(n, dtype=float), index=_dates(n))
    s.iloc[-1] = np.nan                          # 当前值 NaN → NaN(不误报 0)
    pct = p._rolling_pct(s, lookback_years=10, min_periods=252)
    assert np.isnan(pct.iloc[-1])


# ---------- 四档判定 ----------

def test_classify_zone_four_way_and_split():
    low, high = p.PE_PCT_LOW, p.PE_PCT_HIGH     # 0.20 / 0.80
    assert p.classify_zone(0.10, 0.15) == p.ZONE_KEYS["low"]        # 双低
    assert p.classify_zone(0.90, 0.85) == p.ZONE_KEYS["high"]       # 双高
    assert p.classify_zone(0.60, 0.40) == p.ZONE_KEYS["split"]      # 跨中线:PE 偏贵 PB 偏便宜
    assert p.classify_zone(0.40, 0.60) == p.ZONE_KEYS["split"]
    assert p.classify_zone(0.40, 0.45) == p.ZONE_KEYS["mid"]        # 同侧中间
    assert p.classify_zone(0.60, 0.65) == p.ZONE_KEYS["mid"]


def test_classify_zone_pe_only_fallback_and_nan():
    assert p.classify_zone(0.10, None) == p.ZONE_KEYS["low"]
    assert p.classify_zone(0.90, float("nan")) == p.ZONE_KEYS["high"]
    assert p.classify_zone(0.50, None) == p.ZONE_KEYS["mid"]
    assert p.classify_zone(float("nan"), 0.5) is None              # PE 缺 → 无档
    assert p.classify_zone(None, None) is None


# ---------- 逐日回放 + parity ----------

def _synth_pe_pb(n=600):
    idx = _dates(n)
    t = np.arange(n, dtype=float)
    pe = pd.DataFrame({"pe_ttm": 10 + 8 * np.sin(t / 90) + t / 200}, index=idx)
    pb = pd.DataFrame({"pb": 1.2 + 0.8 * np.cos(t / 130) + t / 500}, index=idx)
    pe.index.name = pb.index.name = "date"
    return pe, pb


class _StubStore:
    """diagnose_valuation / render 需要的最小 store 面。"""

    def __init__(self, pe=None, pb=None, daily=None):
        self.pe, self.pb, self.daily = pe, pb, daily or {}

    def get_index_pe_series(self, name):
        return self.pe if self.pe is not None else pd.DataFrame()

    def get_index_pb_series(self, name):
        return self.pb if self.pb is not None else pd.DataFrame()

    def get_index_daily_series(self, sym):
        return self.daily.get(sym, pd.DataFrame())

    def get_market_turnover_series(self):
        return pd.DataFrame()

    def get_market_pb_series(self):
        return pd.DataFrame()

    def get_market_margin_series(self):
        return pd.DataFrame()


def test_valuation_zone_series_head_blank():
    pe, pb = _synth_pe_pb()
    out = p.valuation_zone_series(pe, pb)
    assert len(out) == 600
    assert (out["zone"].iloc[:251] == "").all()     # 首 251 日留白(启动期分位不可靠)
    assert (out["zone"].iloc[251:] != "").all()     # 之后有档


def test_valuation_zone_series_parity_with_diagnose_snapshot():
    """末行 pe_pct/pb_pct/zone 必须与 diagnose_valuation 当前快照一致(同公式对齐)。"""
    pe, pb = _synth_pe_pb()
    store = _StubStore(pe, pb)
    snap = dz.diagnose_valuation(store)
    out = p.valuation_zone_series(pe, pb)
    last = out.iloc[-1]
    assert snap["valid"]
    assert abs(last["pe_pct"] - snap["pe_pct"]) < 1e-12
    assert abs(last["pb_pct"] - snap["pb_pct"]) < 1e-12
    assert last["zone"] == snap["zone"]


def test_valuation_zone_series_pe_only():
    pe, _ = _synth_pe_pb(300)
    out = p.valuation_zone_series(pe, pd.DataFrame())
    assert out["pb_pct"].isna().all()
    valid = out["zone"][out["zone"] != ""]
    assert len(valid) > 0
    assert set(valid) <= {p.ZONE_KEYS["low"], p.ZONE_KEYS["mid"], p.ZONE_KEYS["high"]}  # 三档 fallback


# ---------- 段/事件 ----------

def _zone_seq(labels):
    return pd.Series(labels, index=_dates(len(labels)), dtype=object)


def test_zone_snapshot_and_switches():
    z = _zone_seq([p.ZONE_KEYS["low"]] * 3 + [p.ZONE_KEYS["mid"]] * 2 + [""] +
                  [p.ZONE_KEYS["high"]] * 4)     # 空档夹在中间,不应把段并起来
    snap = p.zone_snapshot(z)
    assert snap["valid"] and snap["zone"] == p.ZONE_KEYS["high"]
    assert snap["days_in_zone"] == 4
    assert snap["prev_zone"] == p.ZONE_KEYS["mid"]
    assert snap["entered_on"] == str(z.index[6])

    sw = p.recent_switches(z)
    assert len(sw) == 2
    assert sw[0] == {"date": str(z.index[3]), "prev": p.ZONE_KEYS["low"],
                     "zone": p.ZONE_KEYS["mid"], "days": 2, "ongoing": False}
    assert sw[1] == {"date": str(z.index[6]), "prev": p.ZONE_KEYS["mid"],
                     "zone": p.ZONE_KEYS["high"], "days": 4, "ongoing": True}
    assert p.recent_switches(z, n=1) == [sw[1]]    # 取最近 n 条


def test_zone_snapshot_all_blank():
    snap = p.zone_snapshot(pd.Series(["", ""], index=_dates(2), dtype=object))
    assert snap["valid"] is False


# ---------- 档位统计 ----------

def test_zone_stats_hand_computed():
    n = 12
    close = pd.Series([100.0] * 4 + [200.0] * 8, index=_dates(n))
    zone = _zone_seq([p.ZONE_KEYS["low"]] * 4 + [p.ZONE_KEYS["high"]] * 8)
    stats = p.zone_stats(zone, close, fwd_windows=(3,))

    assert stats[p.ZONE_KEYS["low"]]["days"] == 4
    assert stats[p.ZONE_KEYS["low"]]["share"] == pytest.approx(4 / 12)
    f = stats[p.ZONE_KEYS["low"]]["fwd"][3]
    # low 日 idx0: close[3]/close[0]-1 = 0;idx1: close[4]/close[1]-1 = 1.0;idx2/3 窗口跨界也算
    rets = [close.iloc[i + 3] / close.iloc[i] - 1 for i in range(4)]
    assert f["n"] == 4
    assert f["median"] == pytest.approx(float(np.median(rets)))
    assert f["mean"] == pytest.approx(float(np.mean(rets)))
    assert np.isfinite(f["vol"])
    # high:末端 3 日(idx9-11)前向窗口不足 → 丢弃,n = 8-3 = 5
    assert stats[p.ZONE_KEYS["high"]]["fwd"][3]["n"] == 5


def test_zone_stats_empty():
    stats = p.zone_stats(pd.Series(dtype=object), pd.Series(dtype=float))
    assert all(v["days"] == 0 for v in stats.values())


# ---------- 预案表 ----------

def _ok_params():
    return {"position_plan": {
        "note": "测试",
        "zones": {"low": {"equity_min": 0.7, "equity_max": 0.9},
                  "mid": {"equity_min": 0.4, "equity_max": 0.6},
                  "split": {"equity_min": 0.4, "equity_max": 0.6},
                  "high": {"equity_min": 0.1, "equity_max": 0.3}}}}


def test_load_position_plan_ok():
    plan = p.load_position_plan(_ok_params())
    assert plan["note"] == "测试"
    assert plan["zones"][p.ZONE_KEYS["low"]] == {"min": 0.7, "max": 0.9}
    assert set(plan["zones"]) == set(p.ZONE_ORDER)


def test_load_position_plan_errors():
    with pytest.raises(ValueError, match="缺少键"):
        bad = _ok_params()
        del bad["position_plan"]["zones"]["split"]
        p.load_position_plan(bad)
    with pytest.raises(ValueError, match="未知键"):
        bad = _ok_params()
        bad["position_plan"]["zones"]["bull"] = {"equity_min": 0.5, "equity_max": 0.6}
        p.load_position_plan(bad)
    with pytest.raises(ValueError, match="0 ≤ equity_min ≤ equity_max ≤ 1"):
        bad = _ok_params()
        bad["position_plan"]["zones"]["low"] = {"equity_min": 0.8, "equity_max": 0.6}
        p.load_position_plan(bad)
    with pytest.raises(ValueError, match="缺 equity_min"):
        bad = _ok_params()
        bad["position_plan"]["zones"]["low"] = {"equity_min": 0.5}
        p.load_position_plan(bad)


# ---------- 渲染 smoke ----------

def _render_store(n=600):
    pe, pb = _synth_pe_pb(n)
    px = pd.DataFrame({"close": np.linspace(3000.0, 4500.0, n)}, index=pe.index)
    return _StubStore(pe, pb, {"000300": px})


def test_render_smoke_full(tmp_path):
    out = tmp_path / "position.html"
    path = p.render_position_report(_render_store(), _ok_params(), out)
    html = out.read_text(encoding="utf-8")
    assert path == str(out)
    for marker in ("当前档位 × 预案对照", "估值档历史回放", "预案表 × 档位统计",
                   "档位切换事件", "读图说明", "pos-fig", "温度计", "position_plan",
                   "永不喂交易引擎"):
        assert marker in html, f"缺 section 标记: {marker}"


def test_render_smoke_bad_plan_banner(tmp_path):
    out = tmp_path / "position.html"
    p.render_position_report(_render_store(), {}, out)      # 缺 position_plan 段
    html = out.read_text(encoding="utf-8")
    assert "position_plan 配置错误" in html                  # 红条不静默
    assert "缺少键" in html


def test_render_smoke_no_data(tmp_path):
    out = tmp_path / "position.html"
    p.render_position_report(_StubStore(), _ok_params(), out)
    html = out.read_text(encoding="utf-8")
    assert "backfill_index.py" in html                      # 数据不足提示
