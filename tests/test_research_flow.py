"""Tests for stockagent.research.flow (板块资金流向 · 跨 ETF 横截面纯函数).

Covers: etf_flow_yi (Δ份额×净值, ffill 对齐), flow_panel (无历史排除/拆分前复权),
group_rolling_flow (滚动求和/NaN 头), pool_flow_state (增量vs存量 标签树),
group_monthly_matrix (月度 ROC/月中上市剔除), group_aum_yi.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockagent.research import flow as fl


def _idx(n: int, start: str = "2024-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="D")


def _series_map(n: int, shares: np.ndarray | None, nav_val: float = 2.0,
                start: str = "2024-01-01") -> dict:
    """单 ETF 的 series_map 条目：shares None → {"shares": None}；nav 双列齐全。"""
    idx = _idx(n, start)
    sh_arr = shares if shares is not None else None
    return {"shares": (None if sh_arr is None else pd.DataFrame({"shares": sh_arr}, index=idx)),
            "nav": pd.DataFrame({"unit_nav": np.full(n, nav_val),
                                 "acc_nav": np.full(n, nav_val)}, index=idx)}


# ---------------- etf_flow_yi / flow_panel ----------------

def test_etf_flow_yi_step_and_ffill():
    # 稀疏份额（第0/3/7日有值 1e9/1.025e9/1e9），nav=2.0 每日 → ffill 对齐后单日 ±0.5亿
    m = _series_map(10, None)
    idx = _idx(10)
    m["shares"] = pd.DataFrame(
        {"shares": [1e9, np.nan, np.nan, 1.025e9, np.nan, np.nan, np.nan, 1e9, np.nan, np.nan]},
        index=idx)
    panel, excluded = fl.flow_panel({"A": m})
    assert excluded == [] and "A" in panel
    f = panel["A"]["flow"]
    assert np.isnan(f.iloc[0])                       # 首观测无 diff
    assert abs(f.iloc[3] - 0.5) < 1e-9               # +2.5e7份 × 2.0 / 1e8 = +0.5亿
    assert abs(f.iloc[7] + 0.5) < 1e-9               # 回落 -0.5亿
    assert f.iloc[[1, 2, 4, 5, 6, 8, 9]].fillna(0).abs().max() == 0.0


def test_flow_panel_excludes_no_history():
    good = _series_map(30, np.full(30, 1e9))
    none_sh = _series_map(30, None); none_sh["shares"] = None
    one_row = _series_map(30, None)
    one_row["shares"] = pd.DataFrame({"shares": [1e9]}, index=_idx(1))
    spot_only = _series_map(30, None); spot_only["shares"] = None
    spot_only["current_shares"] = 1e9                # spot-only 标的：必须被忽略
    panel, excluded = fl.flow_panel({"A": good, "B": none_sh, "C": one_row, "D": spot_only})
    assert set(panel) == {"A"}
    assert set(excluded) == {"B", "C", "D"}


def test_flow_panel_split_day_yields_no_fake_flow():
    # 拆分日：份额 ×2、unit_nav ÷2 → 前复权后 flow≈0（原始口径会是 +100%×AUM 假流入）
    n, at = 60, 30
    unit = np.full(n, 2.0); unit[at:] = 1.0
    sh = np.full(n, 1e9); sh[at:] = 2e9
    idx = _idx(n)
    m = {"shares": pd.DataFrame({"shares": sh}, index=idx),
         "nav": pd.DataFrame({"unit_nav": unit, "acc_nav": np.linspace(1.0, 1.2, n)}, index=idx)}
    panel, _ = fl.flow_panel({"A": m})
    f = panel["A"]["flow"].dropna()
    assert f.abs().max() < 1e-6                      # 拆分不产生任何"流入"


# ---------------- group_rolling_flow ----------------

def test_group_rolling_window_math():
    # A：每日 Δ份额 5e7 × nav 2.0 = +1亿/日线性申购；B：平坦 0 流入
    n = 30
    sh_a = 1e9 + np.arange(n) * 5e7
    smap = {"A": _series_map(n, sh_a), "B": _series_map(n, np.full(n, 2e9))}
    panel, _ = fl.flow_panel(smap)
    roll = fl.group_rolling_flow(panel, {"G": ["A", "B"], "H": ["B"]}, window=5)
    assert list(roll.columns) == ["G", "H"]
    # 馺成员 flow 缺数补 0 后滚动：首窗(0+1×4)=4亿，其后恒 = 5×1亿
    assert abs(roll["G"].iloc[4] - 4.0) < 1e-9
    assert abs(roll["G"].iloc[5] - 5.0) < 1e-9
    assert abs(roll["G"].iloc[-1] - 5.0) < 1e-9
    assert roll["H"].dropna().abs().max() == 0.0     # 平坦成员组 0 流入


def test_group_rolling_single_member_short_calendar():
    """回归：单成员组的净值日历短于全池（如 159941 净值晚一天）→ 对齐全池日历，
    末值不得 NaN（组内对齐会在 DataFrame 拼接时让该组尾部缺行）。"""
    n = 30
    a = _series_map(n, np.full(n, 1e9), start="2024-01-01")            # A 到 01-30
    b = _series_map(n - 1, np.full(n - 1, 1e9), start="2024-01-01")    # B 日历少一天
    b["shares"] = pd.DataFrame({"shares": np.full(n - 1, 1e9)},
                               index=_idx(n - 1, "2024-01-01"))
    panel, _ = fl.flow_panel({"A": a, "B": b})
    roll = fl.group_rolling_flow(panel, {"G": ["A"], "H": ["B"]}, window=5)
    assert not roll["H"].iloc[-1] != roll["H"].iloc[-1]   # 末值非 NaN
    assert abs(roll["H"].iloc[-1]) < 1e-9                 # B 平坦 → 0 流入


def test_group_aum_yi_latest():
    n = 10
    sh_a = np.linspace(1e9, 2e9, n)
    sh_b = np.full(n, 5e9)
    smap = {"A": _series_map(n, sh_a, nav_val=2.0), "B": _series_map(n, sh_b, nav_val=1.0)}
    panel, _ = fl.flow_panel(smap)
    aum = fl.group_aum_yi(panel, {"G": ["A", "B"]})
    # 末段: 2e9×2.0 + 5e9×1.0 = 9e9 / 1e8 = 90亿
    assert abs(aum["G"] - 90.0) < 1e-9


def test_group_aum_series_split_unwind_restores_real_aum():
    # 拆分前复权回退：原始份额 1e9→2e9(×2)、nav 1.0→0.5(÷2)——真实 AUM 拆分前后
    # 连续 = 10亿。panel 份额为前复权口径(2e9 平坦)；不回退则前半被高估成 20亿
    n, at = 20, 10
    unit = np.full(n, 1.0); unit[at:] = 0.5
    sh = np.full(n, 1e9); sh[at:] = 2e9              # 原始口径（有跳变→检测器识别）
    idx = _idx(n)
    m = {"shares": pd.DataFrame({"shares": sh}, index=idx),
         "nav": pd.DataFrame({"unit_nav": unit, "acc_nav": np.linspace(1, 1.2, n)}, index=idx)}
    panel, _ = fl.flow_panel({"A": m})
    assert len(panel["A"]["splits"]) == 1            # 拆分被识别、adj=2e9 平坦
    assert abs(panel["A"]["shares"].iloc[0] - 2e9) < 1e6
    s = fl.group_aum_series(panel, {"G": ["A"]})
    assert list(s.columns) == ["G"]
    assert abs(s["G"].iloc[0] - 10.0) < 1e-9         # 回退后真实 AUM：1e9×1.0/1e8
    assert abs(s["G"].iloc[-1] - 10.0) < 1e-9        # 2e9×0.5/1e8（拆分保值·连续）
    assert abs(s["G"].max() - 10.0) < 1e-9           # 全程 10亿（无回退则前半=20亿）


def test_group_aum_series_unlisted_member_zero():
    # 成员中途上市：上市前贡献 0（组 AUM 随成员上市跳增——真实含义）
    n = 20
    a = _series_map(n, np.full(n, 1e9), nav_val=1.0)
    b = _series_map(n, None, nav_val=1.0)
    idx = _idx(n)
    b["shares"] = pd.DataFrame({"shares": np.r_[np.full(10, np.nan), np.full(10, 2e9)]}, index=idx)
    panel, _ = fl.flow_panel({"A": a, "B": b})
    s = fl.group_aum_series(panel, {"G": ["A", "B"]})
    assert abs(s["G"].iloc[0] - 10.0) < 1e-9         # 仅 A：1e9×1.0
    assert abs(s["G"].iloc[-1] - 30.0) < 1e-9        # A+B：1e9+2e9（×1.0）



# ---------------- daily_flow_events（申赎异动 · 顶部横幅数据） ----------------

import pytest  # noqa: E402  （本段断言用 approx）


def test_daily_flow_events_thresholds():
    # A：300日历史、3天前单日+30%（×nav2.0=6亿 ≥ 地板）→ 命中；
    # S：同幅+30% 但净值0.001（flow=0.003亿 < 地板）→ 金额地板滤掉；
    # Y：仅60日历史 → min_history 跳过；O：巨幅在 -200日（scan_days=250 窗内）→ 也命中（台账含历史）
    n = 300
    sh_a = np.full(n, 1e9); sh_a[-3:] = 1.3e9        # 跳变后保持（否则回落日也成事件）
    a = _series_map(n, sh_a, nav_val=2.0)
    sh_s = np.full(n, 1e9); sh_s[-2:] = 1.3e9
    s = _series_map(n, sh_s, nav_val=0.001)
    y = _series_map(60, np.full(60, 1e9))
    sh_o = np.full(n, 1e9); sh_o[-200:] = 1.4e9
    o = _series_map(n, sh_o, nav_val=2.0)
    panel, _ = fl.flow_panel({"A": a, "S": s, "Y": y, "O": o})
    evs = fl.daily_flow_events(panel, pctile=0.99, floor_yi=1.0,
                               scan_days=250, min_history=250)
    assert [e["symbol"] for e in evs] == ["A", "O"]   # 日期降序（A 最近）
    assert evs[0]["side"] == "in"
    assert evs[0]["pctile"] >= 0.99
    assert evs[0]["flow_yi"] == pytest.approx(6.0, abs=1e-9)   # 3e8份×2.0/1e8


def test_daily_flow_events_scan_window():
    # 扫描窗口（交易日）：-20日与-5日两事件，scan_days=10 → 只剩 -5日；250 → 两条
    n = 300
    sh = np.full(n, 1e9); sh[-20:-5] = 1.4e9; sh[-5:] = 1.8e9   # -20日+40%、-5日+28.6%
    m = _series_map(n, sh, nav_val=2.0)
    panel, _ = fl.flow_panel({"A": m})
    newest = str(_idx(n)[-5])[:10]
    evs10 = fl.daily_flow_events(panel, scan_days=10)
    assert len(evs10) == 1 and str(evs10[0]["date"])[:10] == newest
    evs250 = fl.daily_flow_events(panel, scan_days=250)
    assert len(evs250) == 2
    assert str(evs250[0]["date"])[:10] == newest                 # 最新在前


def test_daily_flow_events_empty():
    assert fl.daily_flow_events({}) == []
    panel, _ = fl.flow_panel({"A": _series_map(300, np.full(300, 1e9))})
    assert fl.daily_flow_events(panel) == []                    # 无变化日 → 无事件


# ---------------- pool_flow_state（增量vs存量 标签树） ----------------

def _roll(**cols) -> pd.DataFrame:
    idx = _idx(len(next(iter(cols.values()))))
    return pd.DataFrame({k: list(v) for k, v in cols.items()}, index=idx)


def test_pool_state_broad_inflow():
    r = fl.pool_flow_state(_roll(G1=[5, 5], G2=[4, 4], G3=[3, 3], G4=[2, 2], G5=[1, 1]),
                           window=20, in_yi=10, out_yi=-10, gross_floor_yi=15,
                           breadth_floor_yi=1.0, breadth_min=0.5)
    assert r["label_key"] == "broad_in" and r["label"] == "增量普涨"
    assert abs(r["pool_net_yi"] - 15) < 1e-9 and abs(r["intensity"] - 1) < 1e-9
    assert abs(r["breadth"] - 1.0) < 1e-9
    assert [g["group"] for g in r["group_flows"]] == ["G1", "G2", "G3", "G4", "G5"]


def test_pool_state_focused_inflow():
    r = fl.pool_flow_state(_roll(G1=[12, 12], G2=[0.2, 0.2], G3=[-0.2, -0.2]),
                           in_yi=10, breadth_floor_yi=1.0, breadth_min=0.5)
    assert r["label_key"] == "focused_in" and r["label"] == "增量聚焦"
    assert r["pool_net_yi"] >= 10 and r["breadth"] < 0.5


def test_pool_state_rotation_offsetting():
    r = fl.pool_flow_state(_roll(G1=[10, 10], G2=[-9.5, -9.5], G3=[0.5, 0.5], G4=[-1, -1]),
                           in_yi=10, gross_floor_yi=15)
    assert r["label_key"] == "rotation" and r["label"] == "存量轮动"
    assert abs(r["pool_net_yi"]) < 10 and r["pool_gross_yi"] >= 15
    assert r["intensity"] < 0.1                     # 净/毛比≈0 = 纯对冲


def test_pool_state_net_redemption():
    r = fl.pool_flow_state(_roll(G1=[-3, -3], G2=[-4, -4], G3=[-5, -5]), out_yi=-10)
    assert r["label_key"] == "net_out" and r["label"] == "净赎回"
    assert r["pool_net_yi"] <= -10


def test_pool_state_quiet_fallback():
    r = fl.pool_flow_state(_roll(G1=[0.5, 0.5], G2=[-0.4, -0.4]),
                           in_yi=10, gross_floor_yi=15)
    assert r["label_key"] == "quiet" and r["label"] == "缩量观望"


def test_pool_state_insufficient():
    r = fl.pool_flow_state(pd.DataFrame())
    assert r["label_key"] == "insufficient" and r["label"] == "数据不足"
    assert np.isnan(r["pool_net_yi"])


# ---------------- group_monthly_matrix ----------------

def _three_month_panel():
    """2024-01..03：A 全程在场（份额按月台阶）；B 2024-02-15 中途上市（当月必须剔除）。"""
    idx = pd.date_range("2024-01-01", "2024-03-31", freq="D")
    n = len(idx)
    sh_a = np.concatenate([np.linspace(1e9, 1.1e9, 31),                     # 1月 +10%
                           np.full(29, 1.2e9),                              # 2月 平
                           np.full(31, 1.3e9)])                             # 3月 平
    sh_b = np.full(n, np.nan)
    sh_b[idx >= pd.Timestamp("2024-02-15")] = 5e8
    nav_df = pd.DataFrame({"unit_nav": np.full(n, 1.0), "acc_nav": np.full(n, 1.0)}, index=idx)
    smap = {"A": {"shares": pd.DataFrame({"shares": sh_a}, index=idx), "nav": nav_df},
            "B": {"shares": pd.DataFrame({"shares": sh_b}, index=idx), "nav": nav_df}}
    return fl.flow_panel(smap)[0]


def test_monthly_matrix_values_and_midmonth_listing():
    panel = _three_month_panel()
    m = fl.group_monthly_matrix(panel, {"G": ["A", "B"]}, start_month="2024-01")
    assert list(m.columns) == ["2024-01", "2024-02", "2024-03"]
    assert list(m.index) == ["G"]
    # 月末 vs 上月末（首月基点=序列首日）：A 1月 +10%（1.1/1.0）
    assert abs(m.loc["G", "2024-01"] - 0.10) < 1e-9
    # 2月：A 1.2/1.1（B 2月中旬上市 → 上月末无值 → 当月剔除）
    assert abs(m.loc["G", "2024-02"] - (1.2e9 / 1.1e9 - 1)) < 1e-9
    # 3月：A+B 都在场 (1.3e9+5e8)/(1.2e9+5e8)
    assert abs(m.loc["G", "2024-03"] - (1.8e9 / 1.7e9 - 1)) < 1e-9


def test_monthly_matrix_respects_start_month_and_nan_head():
    panel = _three_month_panel()
    m = fl.group_monthly_matrix(panel, {"G": ["A", "B"]}, start_month="2023-11")
    assert list(m.columns) == ["2023-11", "2023-12", "2024-01", "2024-02", "2024-03"]
    assert np.isnan(m.loc["G", "2023-11"]) and np.isnan(m.loc["G", "2023-12"])


def test_monthly_matrix_all_nan_month():
    # 组内成员当月两端都无数据 → NaN（空白=无数据）
    m = fl.group_monthly_matrix({}, {"G": ["A"]}, start_month="2024-01")
    assert m.empty or np.isnan(m.loc["G", "2024-01"])


def test_daily_flow_events_side_pctile_skewed_history():
    """方向内分位检测（2026-08 与排名表「日申赎」列统一）：申购端尾巴肥的历史里，
    史上最大赎回 赎回向≈100% 命中——绝对值口径会被 +7% 申购日压到 99% 以下漏掉；
    反向：常规量级申购(与既往申购同量级) 赎回向排名不极端 → 不上榜。"""
    n = 300
    rng = np.random.default_rng(3)
    chg = rng.normal(0.0, 0.008, n)
    chg[[50, 80, 110, 140]] = 0.07                   # 4 个 +7% 肥申购日（申购端尾巴）
    chg[-1] = -0.05                                  # 末日：史上最大赎回
    sh = 1e9 * np.cumprod(1 + chg)
    m = {"shares": pd.Series(sh, index=_idx(n)),
         "nav": pd.Series(2.0, index=_idx(n))}
    panel = {"A": {**m, "flow": fl.etf_flow_yi(m["shares"], m["nav"])}}
    evs = fl.daily_flow_events(panel, pctile=0.99, floor_yi=0.1, scan_days=5)
    assert len(evs) == 1 and evs[0]["side"] == "out"
    assert evs[0]["pctile_kind"] == "side" and evs[0]["pctile"] == 1.0
    # 对照：绝对值口径下末日 |−5%| 排在 +7% 之后 <99% → 旧口径会漏（口径差回归锚）
    pct = m["shares"].pct_change().dropna()
    assert float(pct.abs().rank(method="average", pct=True).iloc[-1]) < 0.99


def test_daily_flow_events_sparse_side_falls_back_to_abs():
    """赎回侧样本不足(< side_min_obs) → 该方向退绝对值双向分位（kind='abs'）。"""
    n = 300
    rng = np.random.default_rng(5)
    chg = np.abs(rng.normal(0.004, 0.004, n))        # 几乎全申购
    chg[-1] = -0.03                                  # 罕见赎回：赎回侧样本=1
    sh = 1e9 * np.cumprod(1 + chg)
    m = {"shares": pd.Series(sh, index=_idx(n)),
         "nav": pd.Series(2.0, index=_idx(n))}
    panel = {"A": {**m, "flow": fl.etf_flow_yi(m["shares"], m["nav"])}}
    evs = fl.daily_flow_events(panel, pctile=0.99, floor_yi=0.1, scan_days=5)
    assert len(evs) == 1 and evs[0]["pctile_kind"] == "abs"
