"""指数择时层交互式 HTML 看板(plotly,离线自包含)— 十件套(V4 tracker)。

① 偏离极值曲线(close+MA60 主图 / 偏离度副图+历史极值线+历史极值事件标注「第k」)
② 7宽基趋势状态表(60日线上下/均线趋势/突破跌破档位/震荡市)
③ 估值开关 stat tile(沪深300 PE 分位 + 全市场 PB 分位 + zone)
④ 蓝筹 vs 成长 仓位倾向 lean 指标卡
⑤ 当前有效突破/跌破信号列表
⑥ 市场温度·大小盘温差(全市场 PB 分位 vs 沪深300 PB 分位)
⑦ 相对周期律·沪深成长温差(创业板 vs 上证 点差:5年包络位置 → 极点/中枢 + 历史稀有度 + 漂移)
⑧ 成交量地量监测(两市成交额/MA250 → 地量 flag + 量价 event-study 时效/胜率)
⑨ 恐惧贪婪指数(动量/流动性/波动/估值/杠杆 5成分 → 0-100 复合;市场情绪温度计,只读不喂引擎)
⑩ 关键位监测(平台顶+前低规则选位 → 支撑测试状态机 + 下/上第一档;实证:破位后20日波动抬升,
   回撤中位/胜率无 edge —— 温度计不是开关,永不喂引擎)

配色遵循 dataviz skill 中性参考调色板:文字用 ink token 不穿 series 色;状态用 status
chip(icon+label,不单靠色);A股语义下正偏离(超买)暖红、负偏离(超卖)冷蓝。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import diagnose as dz
from . import fear_greed as fg
from . import indicators as ti
from . import support_levels as slv

# ---- palette (dataviz reference, light mode) ----
_PAL = {
    "surface": "#fcfcfb", "plane": "#f9f9f7", "ink": "#0b0b0b",
    "ink_sec": "#52514e", "muted": "#898781", "grid": "#e1e0d9", "baseline": "#c3c2b7",
    "series_1": "#2a78d6",    # blue — close line / 蓝筹
    "series_2": "#008300",    # green — 成长
    "pos_extreme": "#d03b3b",  # red — 正极值(超买/涨)
    "neg_extreme": "#1c5cab",  # blue-dark — 负极值(超卖/跌)
    "good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
}


def _chip(label: str, color: str) -> str:
    """status chip — icon+label paired, never color-alone."""
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:8px;'
            f'background:{color}22;color:{color};font-size:12px;font-weight:600;'
            f'border:1px solid {color}55">{label}</span>')


# ---- ① 偏离极值曲线 ----
def _deviation_figure(sym: str, name: str, df: pd.DataFrame, period: int = ti.MA_PERIOD) -> go.Figure:
    close = df["close"]
    ma = ti.ma_series(close, period)
    dev = ti.deviation_series(close, period)
    idx = pd.to_datetime(close.index)   # 转 date 类型 → hover 显示完整日期
    dc = dev.dropna()
    mx = float(dc.max()) if len(dc) else float("nan")
    mn = float(dc.min()) if len(dc) else float("nan")
    # 历史极值事件(全历史口径,纯观察): ≤5%/>95% 区间内最深处的谷/峰,标「第k低/高」(1=史上最极端)
    events = ti.deviation_extreme_events(close, period)
    lows = [e for e in events if e["side"] == "low"]
    highs = [e for e in events if e["side"] == "high"]
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4], vertical_spacing=0.10,
        subplot_titles=(f"{name}({sym}) 收盘价 vs {period}日线", "偏离度 (价格−均线)÷均线"))
    fig.add_trace(go.Scatter(x=idx, y=close, name="收盘",
                             line=dict(color=_PAL["series_1"], width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=idx, y=ma, name=f"MA{period}",
                             line=dict(color=_PAL["muted"], width=1.5, dash="dash")), row=1, col=1)
    fig.add_trace(go.Scatter(x=idx, y=dev, name="偏离度",
                             line=dict(color=_PAL["ink_sec"], width=1.5)), row=2, col=1)
    if not pd.isna(mx):
        fig.add_hline(y=mx, row=2, col=1, line=dict(color=_PAL["pos_extreme"], width=1, dash="dot"),
                      annotation_text=f"正极值 +{mx:.0%}", annotation_position="top right")
    if not pd.isna(mn):
        fig.add_hline(y=mn, row=2, col=1, line=dict(color=_PAL["neg_extreme"], width=1, dash="dot"),
                      annotation_text=f"负极值 {mn:.0%}", annotation_position="bottom right")
    # 历史低点(▼蓝,第k低) — 第几=全历史绝对排名,1=史上最深谷(唯一)
    if lows:
        fig.add_trace(go.Scatter(
            x=pd.to_datetime([e["date"] for e in lows]), y=[e["dev"] for e in lows],
            name="历史低点", mode="markers+text",
            marker=dict(symbol="triangle-down", size=11, color=_PAL["neg_extreme"],
                        line=dict(color=_PAL["ink"], width=0.5)),
            text=[f"第{e['rank']}低 {e['dev']:+.1%}" for e in lows],
            customdata=[[e["rank"]] for e in lows],
            textposition="bottom center", textfont=dict(size=9), cliponaxis=False,
            hovertemplate="<b>%{x|%Y-%m-%d}</b> 超卖极值 第%{customdata[0]}低<br>偏离 %{y:.1%}<extra></extra>"),
            row=2, col=1)
    # 历史高点(▲红,第k高) — 1=史上最高峰(唯一)
    if highs:
        fig.add_trace(go.Scatter(
            x=pd.to_datetime([e["date"] for e in highs]), y=[e["dev"] for e in highs],
            name="历史高点", mode="markers+text",
            marker=dict(symbol="triangle-up", size=11, color=_PAL["pos_extreme"],
                        line=dict(color=_PAL["ink"], width=0.5)),
            text=[f"第{e['rank']}高 {e['dev']:+.1%}" for e in highs],
            customdata=[[e["rank"]] for e in highs],
            textposition="top center", textfont=dict(size=9), cliponaxis=False,
            hovertemplate="<b>%{x|%Y-%m-%d}</b> 超买极值 第%{customdata[0]}高<br>偏离 %{y:.1%}<extra></extra>"),
            row=2, col=1)
    # 当前点: 落在 ≤5%/≥95% 全历史区附 chip(纯观察提示)
    if not pd.isna(dev.iloc[-1]):
        cur_dev = float(dev.iloc[-1])
        cur_pct = (dc < cur_dev).sum() / len(dc) if len(dc) else float("nan")
        chip = ""
        if not pd.isna(cur_pct):
            if cur_pct <= 0.05:
                chip = f" ·超卖区({cur_pct:.1%})"
            elif cur_pct >= 0.95:
                chip = f" ·超买区({cur_pct:.1%})"
        fig.add_trace(go.Scatter(x=[idx[-1]], y=[cur_dev], mode="markers+text",
                                 marker=dict(size=11, color=_PAL["ink"],
                                             line=dict(color=_PAL["surface"], width=1)),
                                 text=[f"现在 {cur_dev:.1%}{chip}"], textposition="top center",
                                 showlegend=False), row=2, col=1)
    fig.update_layout(
        height=540, margin=dict(l=50, r=20, t=50, b=30),
        paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
        font=dict(color=_PAL["ink"], family="system-ui, sans-serif"), showlegend=False)
    fig.update_xaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["grid"],
                     type="date", hoverformat="%Y-%m-%d")
    fig.update_yaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["baseline"])
    fig.update_xaxes(
        rangeslider_visible=True,
        rangeselector=dict(buttons=[
            dict(count=1, label="1月", step="month", stepmode="backward"),
            dict(count=6, label="6月", step="month", stepmode="backward"),
            dict(count=1, label="1年", step="year", stepmode="backward"),
            dict(count=3, label="3年", step="year", stepmode="backward"),
            dict(label="全部", step="all"),
        ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]),
        row=2, col=1)
    return fig


# ---- ② 趋势状态表 ----
def _trend_table_html(diag: dict) -> str:
    rows = []
    for sym, info in diag["indices"].items():
        if not info.get("valid"):
            rows.append(f"<tr><td>{info['name']}</td><td colspan='5' style='color:{_PAL['muted']}'>"
                        f"{info.get('reason','—')}</td></tr>")
            continue
        dg = info["diagnosis"]; t = dg["trend"]; bo = dg["breakout"]; dev = dg["deviation"]
        above = "线上▲" if t["above_ma"] else "线下▼"
        above_c = _PAL["good"] if t["above_ma"] else _PAL["critical"]
        mtrend = "↑" if t["ma_trend_up"] else ("↓" if t["ma_trend_up"] is False else "—")
        # 突破/跌破 只在「近期真穿越」时标;仅在线上/下但无穿越 → 中性(不再误称突破)
        fresh = ti.fresh_cross_direction(dg.get("cross"))
        if fresh == "up":
            bo_label, bo_c = f"突破 g{bo['grade']}", _PAL["good"]
        elif fresh == "down":
            bo_label, bo_c = f"跌破 g{bo['grade']}", _PAL["critical"]
        else:
            bo_label, bo_c = "中性", _PAL["muted"]
        choppy = f'<span style="color:{_PAL["warning"]}">⚠震荡</span>' if dg["choppy"] else "趋势"
        dev_txt = (f"{dev['cur_dev']:+.1%} / {dev['pct']:.1%}位"
                   if not pd.isna(dev["pct"]) and not pd.isna(dev["cur_dev"]) else "—")
        rows.append(
            f"<tr><td>{info['name']}</td>"
            f"<td>{_chip(above, above_c)}</td><td style='text-align:center'>{mtrend}</td>"
            f"<td>{_chip(bo_label, bo_c)}</td><td>{choppy}</td><td style='font-variant-numeric:tabular-nums'>{dev_txt}</td></tr>")
    return ("<table class='stat'><thead><tr>"
            "<th>指数</th><th>60日线</th><th>均线趋势</th><th>突破跌破</th><th>市态</th><th>偏离度</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            "<div class='hint' style='margin-top:8px'>列定义:60日线=价 vs 均线(线上/下) · "
            "均线趋势=均线升降 · 突破跌破=近5日真穿越才标(无穿越=中性;gN=偏离≥2%/3% 强度) · "
            "市态=趋势/震荡(震荡时信号谨慎) · 偏离度=(价格−均线)÷均线%(绝对值 / 历史分位)</div>")


# ---- ③ 估值开关 stat tile ----
def _valuation_figure(val: dict, pe_df: pd.DataFrame, pb_df: pd.DataFrame) -> go.Figure:
    """沪深300 PE-TTM(上行) / PB(下行) + 20%/50%/80% 分位线 + 便宜/贵区阴影 + 当前点。
    分位线/阴影/默认显示窗口统一近 10 年口径(与 zone 标签一致;短历史自动取全部),消除
    「图全历史、tile 10年」的分歧;2005-2010 泡沫时代的 PE/PB 与现体制不可比,若全历史
    展示会把 y 轴拉到 50+,近 10 年估值带被压成细条 → 默认窗口 10 年,全历史留给
    rangeslider + 5年/10年/全部 快捷按钮(泡沫史一键可达,不删数据)。当前点分位直接用
    val 里 diagnose_valuation 算好的 10 年口径值。"""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.55, 0.45],
                        vertical_spacing=0.12, subplot_titles=("沪深300 PE-TTM", "沪深300 PB"))
    last_dt = None
    y10_rng: dict[int, tuple[float, float]] = {}   # 每行 10 年口径 y 范围(默认视图用)
    for row, df, col, color, label, pkey in [(1, pe_df, "pe_ttm", _PAL["series_1"], "PE", "pe_pct"),
                                             (2, pb_df, "pb", _PAL["series_2"], "PB", "pb_pct")]:
        if df is None or len(df) == 0 or col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(s) < 20:
            continue
        idx = pd.to_datetime(s.index)
        if last_dt is None or idx[-1] > last_dt:
            last_dt = idx[-1]
        fig.add_trace(go.Scatter(x=idx, y=s.to_numpy(), name=label,
                                 line=dict(color=color, width=1.6)), row=row, col=1)
        s10 = s.iloc[-252 * 10:]                      # 近 10 年口径(对齐 zone;越界自动取全部)
        y10_rng[row] = (float(s10.min()), float(s10.max()))
        lo = float(s10.quantile(0.20))
        hi = float(s10.quantile(0.80))
        mid = float(s10.quantile(0.50))
        cur = float(s.iloc[-1])
        pct = val.get(pkey)                           # diagnose_valuation 的 10 年分位,缺失才回退现算
        if pd.isna(pct):
            pct = float((s10 < cur).sum()) / len(s10)
        fig.add_hrect(y0=float(s10.min()), y1=lo, row=row, col=1,
                      fillcolor=_PAL["good"], opacity=0.08, line_width=0)
        fig.add_hrect(y0=hi, y1=float(s10.max()), row=row, col=1,
                      fillcolor=_PAL["critical"], opacity=0.08, line_width=0)
        fig.add_hline(y=lo, row=row, col=1, line=dict(color=_PAL["good"], width=1, dash="dot"),
                      annotation_text=f"20% {lo:.1f}", annotation_position="bottom left")
        fig.add_hline(y=hi, row=row, col=1, line=dict(color=_PAL["critical"], width=1, dash="dot"),
                      annotation_text=f"80% {hi:.1f}", annotation_position="top left")
        fig.add_hline(y=mid, row=row, col=1, line=dict(color=_PAL["muted"], width=1, dash="dot"),
                      annotation_text=f"50% 中位 {mid:.1f}", annotation_position="top right")
        fig.add_trace(go.Scatter(x=[idx[-1]], y=[cur], mode="markers+text",
                                 marker=dict(size=10, color=_PAL["ink"]),
                                 text=[f"现在 {cur:.1f} ({pct * 100:.0f}%)"],
                                 textposition="top center", showlegend=False),
                      row=row, col=1)
    fig.update_layout(height=480, margin=dict(l=50, r=20, t=50, b=30),
                      paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                      font=dict(color=_PAL["ink"], family="system-ui, sans-serif"), showlegend=False)
    # 默认窗口=近10年(对齐分位口径);全历史交 rangeslider(下)+快捷按钮(上);y 轴按可见段自适应
    if last_dt is not None:
        start10 = last_dt - pd.DateOffset(years=10)
        fig.update_xaxes(range=[start10, last_dt])
    fig.update_xaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["grid"], type="date",
                     hoverformat="%Y-%m-%d", row=1, col=1,
                     rangeselector=dict(buttons=[
                         dict(count=5, label="5年", step="year", stepmode="backward"),
                         dict(count=10, label="10年", step="year", stepmode="backward"),
                         dict(label="全部", step="all"),
                     ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]))
    fig.update_xaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["grid"], type="date",
                     hoverformat="%Y-%m-%d", rangeslider_visible=True, row=2, col=1)
    # y 轴显式设 10 年口径范围:plotly 的 autorange 对全量数据算、不随 x 窗口收缩
    # (切「全部」/拖滑块后由页面 _yfit 监听按可见段动态重算)
    for row, (lo_, hi_) in y10_rng.items():
        pad = (hi_ - lo_) * 0.08 or hi_ * 0.05 or 1.0
        fig.update_yaxes(range=[lo_ - pad, hi_ + pad], row=row, col=1)
    fig.update_yaxes(gridcolor=_PAL["grid"])
    return fig


def _pivot_line(close: pd.Series, pivot_mask, fit_start: str,
                shift_sigma: float = 0.0) -> pd.Series:
    """Thin alias for the shared ti.pivot_line (kept so ③ _valuation_price_figure's call sites
    read naturally). Logic lives in stockagent.tracker.indicators.pivot_line."""
    return ti.pivot_line(close, pivot_mask, fit_start, shift_sigma=shift_sigma)


def _valuation_price_figure(daily_df: pd.DataFrame, years: int = 5,
                            fit_start: str = "2009-01-01",
                            support_shift_sigma: float = 1.0) -> go.Figure:
    """沪深300 收盘价 + 顶/底/中位 直线趋势(支撑/阻力参考,粗略)。

    顶/底 = {years}年滚动 max/min 的 OLS 直线;中位=(顶+底)/2。**自 fit_start(默认 2009)起拟合**:
    价格是 24 年长牛(800→4600),整段 OLS 会让左端外推到低于历史最低的无意义位(曾现 667<818),
    且 2009 前(818↔5877 巨震)会严重扭曲拟合;自 2009(金融危机后)起已含完整牛熊周期,直线
    粗略表达当前通道即可。

    下沿用「连接主要低点」: 取 ±1 年窗口的 swing low(局部最低), 过这些低点拟合直线,再整体下移
    support_shift_sigma(默认 1σ)个残差标准差 → 落到低点下方做支撑(OLS 默认平分低点簇会偏高)。
    上沿用滚动最高 OLS 直线。"""
    close = pd.to_numeric(daily_df["close"], errors="coerce").dropna()
    fig = go.Figure()
    if len(close) < 2:
        return fig
    idx = pd.to_datetime(close.index)
    win = 252 * years
    seg_max = close.rolling(win).max().loc[fit_start:]   # 上沿: 自 fit_start 起的滚动最高
    upper = ti.linear_fit_line(seg_max)                  # 上沿·阻力(OLS 直线)
    # 下沿: 连接主要低点(±1年 swing low)的直线,下移 1σ 落到低点下方做支撑
    hw = 250
    cmin = close.rolling(2 * hw + 1, center=True).min()
    low_mask = (close == cmin) & (close.index >= fit_start)
    lower = _pivot_line(close, low_mask, fit_start, shift_sigma=support_shift_sigma)
    fig.add_trace(go.Scatter(x=idx, y=close.to_numpy(), name="沪深300 收盘",
                             line=dict(color=_PAL["series_1"], width=2)))
    if len(upper):
        fig.add_trace(go.Scatter(x=pd.to_datetime(upper.index), y=upper.to_numpy(),
                                 name=f"上沿·阻力·直线({years}年滚动最高·自{fit_start[:4]}年起拟合)",
                                 line=dict(color=_PAL["pos_extreme"], width=1.5)))
    if len(lower):
        fig.add_trace(go.Scatter(x=pd.to_datetime(lower.index), y=lower.to_numpy(),
                                 name=f"下沿·支撑·直线(连接主要低点·自{fit_start[:4]}年起)",
                                 line=dict(color=_PAL["neg_extreme"], width=1.5)))
    if len(upper) and len(lower):
        mid = ((upper + lower) / 2.0).dropna()
        if len(mid):
            fig.add_trace(go.Scatter(x=pd.to_datetime(mid.index), y=mid.to_numpy(),
                                     name="中位·(顶+底)/2",
                                     line=dict(color=_PAL["muted"], width=1.5, dash="dash")))
    # 通道内 20%/80% 分位线 = 下沿 + p×(上沿−下沿): 把通道细分成带(20%=近支撑 / 80%=近阻力)
    if len(upper) and len(lower):
        _w = (upper - lower).dropna()
        if len(_w):
            p20 = (lower + 0.20 * _w).dropna()
            p80 = (lower + 0.80 * _w).dropna()
            fig.add_trace(go.Scatter(x=pd.to_datetime(p20.index), y=p20.to_numpy(),
                                     name="通道20%分位(近支撑)",
                                     line=dict(color=_PAL["good"], width=1, dash="dot")))
            fig.add_trace(go.Scatter(x=pd.to_datetime(p80.index), y=p80.to_numpy(),
                                     name="通道80%分位(近阻力)",
                                     line=dict(color=_PAL["critical"], width=1, dash="dot")))
    cur = float(close.iloc[-1])
    fig.add_trace(go.Scatter(x=[idx[-1]], y=[cur], mode="markers+text",
                             marker=dict(size=10, color=_PAL["ink"]),
                             text=[f"现在 {cur:.1f}"], textposition="top center",
                             showlegend=False))
    fig.update_layout(
        height=420, margin=dict(l=50, r=20, t=30, b=30),
        paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
        font=dict(color=_PAL["ink"], family="system-ui, sans-serif"), showlegend=True)
    fig.update_yaxes(gridcolor=_PAL["grid"])
    fig.update_xaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["grid"], type="date",
                     hoverformat="%Y-%m-%d", rangeslider_visible=True,
                     rangeselector=dict(buttons=[
                         dict(count=1, label="1年", step="year", stepmode="backward"),
                         dict(count=3, label="3年", step="year", stepmode="backward"),
                         dict(count=5, label="5年", step="year", stepmode="backward"),
                         dict(label="全部", step="all"),
                     ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]))
    return fig


def _discipline_label(zone: str) -> tuple[str, str]:
    """zone → (纪律标签, 配色)。展示层确定性映射,不发明新判断(框架第二层:下方只买/上方只卖)。"""
    if "低位" in zone:
        return "只买不卖", _PAL["good"]
    if "高位" in zone:
        return "只卖不买", _PAL["critical"]
    if "分化" in zone:
        return "中性·观望", _PAL["warning"]
    if "中性" in zone:
        return "中性", _PAL["ink_sec"]
    return "—", _PAL["muted"]


def _position_bar(pct: float, label: str = "") -> str:
    """0-100% 估值位置条:20%/80% 刻度 + 三段着色(绿/中性/红) + 当前三角标记 + 纪律标签。
    pct=PE·PB 复合分位。复用 .meter 视觉语言但自绘刻度/标记(看清「当前位置 vs 上下区间」)。"""
    p = 0.0 if pd.isna(pct) else max(0.0, min(1.0, float(pct)))
    zc = _PAL["good"] if p < 0.2 else _PAL["critical"] if p > 0.8 else _PAL["ink_sec"]
    val_txt = f"{p:.0%}" if not pd.isna(pct) else "—"
    return (
        f"<div class='tile' style='min-width:240px'>"
        f"<div class='tile-label'>估值位置(PE·PB 复合)</div>"
        f"<div class='tile-value' style='color:{zc}'>{val_txt}</div>"
        f"<div style='position:relative;height:10px;margin-top:10px'>"
        f"<div style='position:absolute;left:0;width:20%;height:100%;background:{_PAL['good']}22;border-radius:3px 0 0 3px'></div>"
        f"<div style='position:absolute;left:20%;width:60%;height:100%;background:{_PAL['grid']}'></div>"
        f"<div style='position:absolute;left:80%;width:20%;height:100%;background:{_PAL['critical']}22;border-radius:0 3px 3px 0'></div>"
        f"<div style='position:absolute;left:20%;top:-3px;width:1px;height:16px;background:{_PAL['ink_sec']}'></div>"
        f"<div style='position:absolute;left:80%;top:-3px;width:1px;height:16px;background:{_PAL['ink_sec']}'></div>"
        f"<div style='position:absolute;left:{p*100:.0f}%;top:-5px;transform:translateX(-50%);"
        f"width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;"
        f"border-top:7px solid {zc}'></div>"
        f"</div>"
        f"<div class='tile-sub' style='display:flex;justify-content:space-between;margin-top:8px'>"
        f"<span style='color:{_PAL['good']}'>◀只买</span>"
        f"<span style='color:{zc};font-weight:600'>{label or '中性'}</span>"
        f"<span style='color:{_PAL['critical']}'>只卖▶</span></div>"
        f"</div>")


def _meter(label: str, pct: float, sub: str = "") -> str:
    p = 0.0 if pd.isna(pct) else max(0.0, min(1.0, float(pct)))
    zc = _PAL["good"] if p < 0.2 else _PAL["critical"] if p > 0.8 else _PAL["ink_sec"]
    val_txt = f"{p:.0%}" if not pd.isna(pct) else "—"
    return (f"<div class='tile'><div class='tile-label'>{label}</div>"
            f"<div class='tile-value' style='color:{zc}'>{val_txt}</div>"
            f"<div class='meter'><div class='meter-fill' style='width:{p*100:.0f}%;background:{zc}'></div></div>"
            f"<div class='tile-sub'>{sub}</div></div>")


def _valuation_tile_html(val: dict, fig_html: str = "", price_fig_html: str = "") -> str:
    zone = val.get("zone", "—")
    if "低位" in zone:
        zc = _PAL["good"]
    elif "高位" in zone:
        zc = _PAL["critical"]
    elif "分化" in zone:
        zc = _PAL["warning"]    # 结构分化 = 需警惕(ROE 偏弱)
    else:
        zc = _PAL["ink_sec"]
    pe_sub = f"沪深300 PE-TTM {val['pe_ttm']:.1f}" if not pd.isna(val.get("pe_ttm")) else "—"
    pb_sub = f"沪深300 PB {val['pb']:.2f}" if not pd.isna(val.get("pb")) else "—"
    # 复合估值位置 = PE/PB 分位均值(两者皆缺 → NaN);纪律标签由 zone 确定
    _pp = [v for v in (val.get("pe_pct"), val.get("pb_pct")) if not pd.isna(v)]
    pos_pct = sum(_pp) / len(_pp) if _pp else float("nan")
    disc_label, disc_color = _discipline_label(zone)
    tiles = (
        f"{_meter('沪深300 PE 分位(同口径)', val['pe_pct'], pe_sub)}"
        f"{_meter('沪深300 PB 分位(同口径)', val['pb_pct'], pb_sub)}"
        f"{_position_bar(pos_pct, disc_label)}")
    pe_txt = f"{val['pe_pct']:.0%}" if not pd.isna(val.get("pe_pct")) else "—"
    pb_txt = f"{val['pb_pct']:.0%}" if not pd.isna(val.get("pb_pct")) else "—"
    if "分化" in zone:
        hint = (f"PE=PB/ROE → 一高一低 = ROE 偏弱。建议观望:资产便宜但盈利下滑,不急于抄底;"
                f"盈利企稳→转积极,继续下滑→转保守(沪深300 PE {pe_txt} / PB {pb_txt})")
    elif "低位" in zone or "高位" in zone:
        side = "低" if "低位" in zone else "高"
        hint = f"沪深300 PE+PB 同口径双{side}(PE {pe_txt} / PB {pb_txt})"
    else:  # 中位·中性
        hint = f"沪深300 PE+PB 都在历史中间区(PE {pe_txt} / PB {pb_txt})"
    out = (f"<div class='tiles-row'>{tiles}</div>"
           f"<div style='margin-top:10px'>{_chip('估值开关: ' + zone, zc)} "
           f"{_chip('纪律: ' + disc_label, disc_color)} "
           f"<span class='hint'>{hint}</span></div>"
           f"<div class='hint' style='margin-top:6px'>指标:PE(TTM)=市值÷净利润 · PB=市值÷净资产 · "
           f"ROE=净利润÷净资产 · 故 PE=PB÷ROE</div>")
    if fig_html:
        out += (f"<div class='hint' style='margin:10px 0 4px'>实线=全历史序列;虚线=近10年 20%/50%/80% 分位"
                f"(便宜区淡绿 / 贵区淡红 / 中位灰);点=当前(含近10年分位)。可拖底部窗口看时段。</div>"
                f"<div>{fig_html}</div>")
    if price_fig_html:
        out += (f"<div class='hint' style='margin:10px 0 4px'>沪深300 收盘价 + 顶/底/中位趋势线(阻力/支撑/中位,自2009起拟合)"
                f" + 通道内20%/80%分位(下沿+0.2/0.8×通道宽,细分支撑/阻力带)。</div>"
                f"<div>{price_fig_html}</div>")
    return out


# ---- ⑥ 市场温度·大小盘温差 ----
def _market_temp_html(mt: dict) -> str:
    if not mt.get("valid"):
        return "<p class='hint'>市场温度数据不足</p>"
    diff = mt["diff"]
    regime = mt["regime"]
    rc = (_PAL["critical"] if regime == "小盘偏贵"
          else _PAL["good"] if regime == "小盘偏便宜" else _PAL["ink_sec"])
    mkt_sub = f"全市场中位数 PB {mt['market_pb']:.2f}"
    hs_sub = f"沪深300 PB {mt['hs300_pb']:.2f}"
    return (f"<div class='tiles-row'>"
            f"{_meter('全A 股 PB 分位', mt['market_pct'], mkt_sub)}"
            f"{_meter('沪深300 PB 分位', mt['hs300_pct'], hs_sub)}"
            f"<div class='tile'><div class='tile-label'>大小盘温差</div>"
            f"<div class='tile-value' style='color:{rc}'>{diff:+.0%}</div>"
            f"<div class='tile-sub'>{regime}</div></div></div>"
            f"<div class='hint' style='margin-top:8px'>温差 = 全市场PB分位 − 沪深300PB分位;"
            f"&gt;0 小盘偏贵(中小盘热),&lt;0 小盘偏便宜(小盘机会),≈0 同步</div>")


# ---- ④ 蓝筹 vs 成长 ----
def _style_card_html(style: dict) -> str:
    lean = style.get("lean")
    if not lean:
        return "<p class='hint'>风格数据不足</p>"
    lean_cn = {"growth": "偏成长(弹性好)", "blue_chip": "偏蓝筹(防御)"}.get(lean, lean)
    lean_c = _PAL["series_2"] if lean == "growth" else _PAL["series_1"]
    b = "▲ 上行" if style["blue_up"] else "▼ 下行"
    g = "▲ 上行" if style["growth_up"] else "▼ 下行"
    return (f"<div class='tiles-row' style='gap:18px'>"
            f"<div class='tile'><div class='tile-label'>蓝筹(上证50)</div>"
            f"<div class='tile-value' style='color:{_PAL['series_1']}'>{b}</div></div>"
            f"<div class='tile'><div class='tile-label'>成长(创业板指)</div>"
            f"<div class='tile-value' style='color:{_PAL['series_2']}'>{g}</div></div>"
            f"<div class='tile'><div class='tile-label'>仓位倾向</div>"
            f"<div class='tile-value' style='color:{lean_c};font-size:18px'>{lean_cn}</div></div></div>"
            f"<div class='hint' style='margin-top:8px'>注:上行 = 站上 60 日线 且 均线趋势向上;"
            f"下行 = 两者未同时满足。都上行→偏成长,都下行→偏蓝筹,相反→偏向上的</div>")


# ---- ⑤ 信号列表 ----
def _signals_html(diag: dict) -> str:
    sigs = []
    for sym, info in diag["indices"].items():
        if not info.get("valid"):
            continue
        dg = info["diagnosis"]; bo = dg["breakout"]
        fresh = ti.fresh_cross_direction(dg.get("cross"))   # 真穿越闸门(非「在线上」)
        if fresh in ("up", "down") and bo["grade"] >= 2:
            ago = (dg.get("cross") or {}).get("bars_ago", "?")
            dir_cn = "有效突破▲" if fresh == "up" else "有效跌破▼"
            c = _PAL["good"] if fresh == "up" else _PAL["critical"]
            warn = " <span class='hint'>(震荡市,信号谨慎)</span>" if dg["choppy"] else ""
            sigs.append(f"<li>{info['name']}({sym}): {_chip(dir_cn + ' g' + str(bo['grade']), c)}"
                        f" <span class='hint'>{ago}日前穿越</span>{warn}</li>")
    if not sigs:
        return "<p class='hint'>当前无有效突破/跌破信号(近5日真穿越 + 偏离≥2%)</p>"
    return "<ul class='sig-list'>" + "".join(sigs) + "</ul>"


# ---- ⑦ 相对周期律(创业板 vs 上证 点差:包络位置 → 极点/中枢)----
def _relative_cycle_figure(rc: dict, spread: pd.Series) -> go.Figure:
    """点差(上证−创业板,点)长历史 + 振幅通道趋势线(上/下/中) + 当前点。

    上沿 = **连接主要高点**(±1年 swing high)的直线,自 2015 起的长窗口。OLS-on-rolling-max 会被
    早期高位平台(2015-2019)锚定、近年浮在天花板之上(偏高); 改过峰顶 pivot 拟合 → 贴着顶走。
    下沿 = rolling-min 的长窗口 OLS(地板趋势): 点差的 swing-low 过少且非单调(2016/2018/2021 =
    629/1176/-86),过 pivot 拟合会被 2018 假低点拽歪、过度外推到历史最低之下 → 用稳定的地板趋势线。
    中线 = (上沿+下沿)/2。当前精确 5 年包络见 tile。"""
    idx = pd.to_datetime(spread.index)
    ch = ti.cycle_trend_channel(spread, fit_start="2015-01-01", swing_hw=250,
                                envelope=252 * rc.get("envelope_years", 5))
    upper, lower = ch["upper"], ch["lower"]   # 与 headline tile 同源(ti.cycle_trend_channel) → 图端点 == tile 数字
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=idx, y=spread, name="点差(上证−创业板)",
                             line=dict(color=_PAL["series_1"], width=2)))
    if len(upper):
        fig.add_trace(go.Scatter(x=pd.to_datetime(upper.index), y=upper.to_numpy(),
                                 name=f"上沿·趋势线(连主要高点·自2015)",
                                 line=dict(color=_PAL["pos_extreme"], width=1.5)))
    if len(lower):
        fig.add_trace(go.Scatter(x=pd.to_datetime(lower.index), y=lower.to_numpy(),
                                 name=f"下沿·趋势线(地板·长窗口OLS·自2015)",
                                 line=dict(color=_PAL["neg_extreme"], width=1.5)))
    if len(upper) and len(lower):
        mid = ((upper + lower) / 2.0).dropna()
        if len(mid):
            fig.add_trace(go.Scatter(x=pd.to_datetime(mid.index), y=mid.to_numpy(),
                                     name="中线·趋势(上沿+下沿)/2",
                                     line=dict(color=_PAL["muted"], width=1.5, dash="dash")))
    now = rc.get("spread_now")
    if not pd.isna(now) and len(idx):
        fig.add_trace(go.Scatter(x=[idx[-1]], y=[now], mode="markers+text",
                                 marker=dict(size=10, color=_PAL["ink"]),
                                 text=[f"现在 {now:+.0f}"], textposition="top center",
                                 showlegend=False))
    fig.update_layout(
        height=460, margin=dict(l=50, r=20, t=30, b=30),
        paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
        font=dict(color=_PAL["ink"], family="system-ui, sans-serif"), showlegend=True)
    fig.update_yaxes(gridcolor=_PAL["grid"], zeroline=True, zerolinewidth=1,
                     zerolinecolor=_PAL["baseline"])
    fig.update_xaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["grid"], type="date",
                     hoverformat="%Y-%m-%d", rangeslider_visible=True,
                     rangeselector=dict(buttons=[
                         dict(count=1, label="1年", step="year", stepmode="backward"),
                         dict(count=3, label="3年", step="year", stepmode="backward"),
                         dict(count=5, label="5年", step="year", stepmode="backward"),
                         dict(label="全部", step="all"),
                     ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]))
    return fig


def _relative_cycle_html(rc: dict, fig_html: str = "") -> str:
    if not rc.get("valid"):
        return "<p class='hint'>相对周期律数据不足(需 上证综指+创业板指 各 ≥2 年)</p>"
    zone = rc.get("chan_zone") or rc["zone"]   # 显示用趋势通道zone(与chan_pos一致);rc["zone"]是envelope口径(留给E5提醒)
    zc = (_PAL["neg_extreme"] if zone == "下沿极点"
          else _PAL["pos_extreme"] if zone == "上沿极点" else _PAL["ink_sec"])
    now = rc["spread_now"]
    tiles = (
        f"<div class='tile'><div class='tile-label'>当前点差(上证−创业板)</div>"
        f"<div class='tile-value' style='color:{_PAL['ink']}'>{now:+.0f}"
        f" <span style='font-size:.72em;color:{_PAL['ink_sec']}'>/ 中线 {rc['chan_mid']:+.0f}</span></div>"
        f"<div class='tile-sub'>趋势通道 {rc['chan_lo']:+.0f} ~ {rc['chan_hi']:+.0f} (图上下沿)</div></div>"
        f"{_meter('周期位置(趋势通道)', rc['chan_pos'], '图上下沿通道 · ' + zone)}"
        f"{_meter('历史稀有度(5yr秩分位)', rc['rank_pct'], 'house口径 · 分布偏态时会与通道位置背离')}"
        f"<div class='tile'><div class='tile-label'>结构漂移</div>"
        f"<div class='tile-value' style='color:{_PAL['ink_sec']}'>{rc['drift_pts_per_yr']:+.0f} 点/年</div>"
        f"<div class='tile-sub'>10yr OLS · &lt;0 = 创业板结构性跑赢</div></div>")
    run_txt = f"{rc['run_dir'] or '—'} {rc['run']}日" if rc.get("run_dir") else "—"
    b_tiles = (
        f"<div class='tile'><div class='tile-label'>短期动量(20日)</div>"
        f"<div class='tile-value'>{rc['mom_now']:+.0f} 点</div>"
        f"<div class='tile-sub'>点差近20日净变动</div></div>"
        f"<div class='tile'><div class='tile-label'>连续相对强弱</div>"
        f"<div class='tile-value'>{run_txt}</div>"
        f"<div class='tile-sub'>spread 同向连续天数</div></div>")
    hint = ("相对周期律:点差 = 上证综指 − 创业板指(点)。<b>headline=当前点差在图上趋势通道[下沿,上沿]内的位置</b>"
            "(贴可见上下沿趋势线;极点才有回归方向,中枢无edge = 仅相对回归风险解除,非绝对涨跌)。"
            "历史稀有度=同窗口 raw spread 秩分位(house口径,分布偏态时会与通道位置背离,两者并存看)。"
            "结构漂移 &lt;0 = 创业板长期跑赢(约 −40~−50 点/年)。上沿→回归利创业板,下沿→回归利上证。"
            "(E5 提醒仍按作者口径 5 年精确包络 env_pos 触发,与本显示的趋势通道位置分立。)")
    out = (f"<div class='tiles-row'>{tiles}</div>"
           f"<div style='margin-top:10px'>{_chip('周期: ' + zone, zc)}</div>"
           f"<div class='tiles-row' style='margin-top:12px'>{b_tiles}</div>"
           f"<div class='hint' style='margin-top:8px'>{hint}</div>")
    if fig_html:
        out += (f"<div class='hint' style='margin-top:10px'>通道线(自 2015 长窗口,看整体上下沿趋势,非精确边缘): "
                f"上沿 = 连接主要高点的直线(贴峰顶); 下沿 = 地板长窗口趋势线;"
                f"上方 tile 的「趋势通道/周期位置」即用这两条线的当前端点算 → 与图一致。</div>"
                f"<div style='margin-top:4px'>{fig_html}</div>")
    return out


def _cycle_stage_chip(diag: dict) -> str:
    """D: 顶部一行「周期定位」= ③估值zone + ①任一宽基偏离极值 → 定性合成。"""
    val_zone = (diag.get("valuation") or {}).get("zone", "—")
    extremes = []
    for info in (diag.get("indices") or {}).values():
        if not info.get("valid"):
            continue
        pct = ((info.get("diagnosis") or {}).get("deviation") or {}).get("pct")
        if pct is not None and not pd.isna(pct) and (pct <= 0.05 or pct >= 0.95):
            extremes.append((info["name"], pct))
    if extremes:
        ext_txt = "、".join(f"{n}偏离{p:.1%}" for n, p in extremes[:2])
    else:
        ext_txt = "宽基偏离均中性"
    return (f"<div class='hint' style='margin-bottom:12px'>📍 周期定位:估值『{val_zone}』· {ext_txt}"
            f" → 估值+趋势极值合成读大盘阶段(定性参考,非交易信号)</div>")


# ---- ⑧ 成交量地量监测(两市成交额/MA250 → 地量 + 量价 event-study)----
def _turnover_figure(turnover_df: pd.DataFrame, index_close: pd.Series,
                     dry_events: list) -> go.Figure:
    """上证综指(左轴) + 两市成交额(右轴) 双轴;地量事件(成交额/MA250≤0.6)打点标注。"""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(x=pd.to_datetime(index_close.index), y=index_close.to_numpy(),
                             name="上证综指", line=dict(color=_PAL["series_1"], width=1.6)),
                  secondary_y=False)
    fig.add_trace(go.Scatter(x=pd.to_datetime(turnover_df.index),
                             y=(turnover_df["total"] / 1e8).to_numpy(),
                             name="两市成交额(亿)", line=dict(color=_PAL["series_2"], width=1.3),
                             opacity=0.85), secondary_y=True)
    if len(dry_events):
        tot = turnover_df["total"]
        vmap = tot.to_dict()
        ratio = (tot / tot.rolling(ti.TURNOVER_MA).mean()).to_dict()
        lby = ti.turnover_new_low_years(tot).to_dict()

        def _dry_trace(subset, name, color, size, opacity):
            if not subset:
                return
            customdata = [[vmap.get(d, float("nan")) / 1e8,
                           ratio.get(d, float("nan")),
                           lby.get(d, float("nan"))] for d in subset]
            fig.add_trace(go.Scatter(
                x=pd.to_datetime([str(d) for d in subset]),
                y=[vmap.get(d, float("nan")) / 1e8 for d in subset],
                name=name, mode="markers",
                marker=dict(color=color, size=size, opacity=opacity,
                            line=dict(color=_PAL["ink"], width=0.5)),
                customdata=customdata,
                hovertemplate=("<b>%{x|%Y-%m-%d}</b> " + name + "<br>"
                               "成交额 %{customdata[0]:.0f} 亿<br>"
                               "/MA250 = %{customdata[1]:.2f}(越低越地)<br>"
                               "近 %{customdata[2]:.1f} 年最低成交额<extra></extra>")),
                secondary_y=True)

        ext_lb = ti.TURNOVER_EXTREME_LOOKBACK
        extreme = [d for d in dry_events if lby.get(d, float("nan")) >= ext_lb]
        normal = [d for d in dry_events if not (lby.get(d, float("nan")) >= ext_lb)]
        _dry_trace(normal, "地量(普通)", _PAL["warning"], 6, 0.55)
        _dry_trace(extreme, "地量(极端·近>0.5年最低)", _PAL["critical"], 10, 0.9)
    fig.update_layout(
        height=460, margin=dict(l=55, r=60, t=30, b=30),
        paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
        font=dict(color=_PAL["ink"], family="system-ui, sans-serif"), showlegend=True)
    fig.update_yaxes(title_text="上证综指", secondary_y=False, gridcolor=_PAL["grid"],
                     zerolinecolor=_PAL["baseline"])
    fig.update_yaxes(title_text="成交额(亿)", secondary_y=True, gridcolor=_PAL["grid"])
    fig.update_xaxes(gridcolor=_PAL["grid"], type="date", hoverformat="%Y-%m-%d",
                     rangeslider_visible=True,
                     rangeselector=dict(buttons=[
                         dict(count=1, label="1年", step="year", stepmode="backward"),
                         dict(count=3, label="3年", step="year", stepmode="backward"),
                         dict(count=5, label="5年", step="year", stepmode="backward"),
                         dict(label="全部", step="all"),
                     ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]))
    return fig


def _turnover_html(t: dict, fig_html: str = "") -> str:
    if not t.get("valid"):
        return "<p class='hint'>成交量地量监测数据不足(需 两市成交额 + 上证综指 各 ≥1 年)</p>"
    dry = t["is_dry"]
    dry_chip = _chip("⚠ 地量区" if dry else "非地量",
                     _PAL["warning"] if dry else _PAL["ink_sec"])
    dsd = t["days_since_dry"]
    row1 = (
        f"<div class='tile'><div class='tile-label'>当前两市成交额</div>"
        f"<div class='tile-value'>{t['turnover_yi']:.0f}<span style='font-size:14px'>亿</span></div>"
        f"<div class='tile-sub'>/MA250 = {t['ratio_now']:.2f} · 地量阈值 0.6</div></div>"
        f"{_meter('3年成交额分位', t['pct_3yr'], '低=地量区 · 高=天量')}"
        f"{_meter('5年成交额分位', t['pct_5yr'], '低=地量区 · 高=天量')}"
        f"<div class='tile'><div class='tile-label'>距上次地量</div>"
        f"<div class='tile-value'>{dsd if dsd is not None else '—'}<span style='font-size:14px'>交易日</span></div>"
        f"<div class='tile-sub'>地量=成交额/MA250≤0.6</div></div>")
    wr20, wr60, mr60 = t["win_rate_20"], t["win_rate_60"], t["median_ret_60"]
    row2 = (
        f"<div class='tile'><div class='tile-label'>量底→价底(经验)</div>"
        f"<div class='tile-value' style='color:{_PAL['good']}'>中位 {t['time_to_bottom_median']:.0f}日</div>"
        f"<div class='tile-sub'>最长 {t['time_to_bottom_max']:.0f}日 · 时效扎实</div></div>"
        f"<div class='tile'><div class='tile-label'>地量后20日胜率</div>"
        f"<div class='tile-value' style='color:{_PAL['ink_sec']}'>{wr20 * 100:.0f}%</div>"
        f"<div class='tile-sub'>≈抛硬币(价仍跌向底)</div></div>"
        f"<div class='tile'><div class='tile-label'>地量后60日胜率</div>"
        f"<div class='tile-value' style='color:{_PAL['ink_sec']}'>{wr60 * 100:.0f}%</div>"
        f"<div class='tile-sub'>中位收益 {mr60 * 100:+.1f}% · 成熟市场无优势</div></div>"
        f"<div class='tile'><div class='tile-label'>样本数</div>"
        f"<div class='tile-value'>{t['sample']}</div>"
        f"<div class='tile-sub'>成熟市场(2000+)地量事件</div></div>")
    hint = ("量价框架:地量 = 成交额/MA250≤0.6(regime 自适应)。"
            "<b>时效扎实</b>:量底→价底中位 ~1 月(唯一站得住的论断)。"
            "<b>胜率无优势</b>:各 horizon 均~50%(成熟市场 2000+;90s 幼年期会虚高,已排除)。"
            "→ 地量≠高胜率买点,只提示'价底临近'。样本偏稀(~80 次/20 年)→ 经验参考。叠估值开关③更可信。")
    ex_n = t.get("extreme_sample") or 0
    ex_wr = t.get("extreme_win_rate_60")
    ex_p = t.get("extreme_pvalue")
    ex_box = ""
    if ex_n and not pd.isna(ex_wr) and not pd.isna(ex_p):
        cmp = ("高于" if (not pd.isna(t.get("win_rate_60")) and ex_wr > t["win_rate_60"])
               else "未高于")
        ex_box = (f"<div style='margin-top:10px;padding:8px 12px;background:{_PAL['warning']}1A;"
                  f"border-left:3px solid {_PAL['critical']};border-radius:4px;font-size:13px'>"
                  f"🔥 <b>极端地量(近>0.5年最低)</b>: 60日胜率 <b>{ex_wr * 100:.0f}%</b>"
                  f" · 样本 {ex_n} · p={ex_p:.2f}(未显著) — "
                  f"<span class='hint'>较全样本{cmp},但样本薄、非定律,不能据此加仓</span></div>")
    out = (f"<div class='tiles-row'>{row1}</div>"
           f"<div style='margin-top:10px'>{dry_chip} <span class='hint'>"
           f"{'成交额萎缩,处地量区' if dry else '成交额未萎缩(非地量)'}</span></div>"
           f"<div class='tiles-row' style='margin-top:12px'>{row2}</div>"
           f"{ex_box}"
           f"<div class='hint' style='margin-top:8px'>{hint}</div>")
    if fig_html:
        out += f"<div style='margin-top:10px'>{fig_html}</div>"
    return out


# ---- ⑨ 恐惧贪婪指数(5成分 0-100 复合 · 市场情绪温度计)----
_FG_COMPONENT_LABELS = [
    ("momentum", "动量", "上证综指 close/MA60 偏离分位"),
    ("turnover", "流动性", "两市成交额/MA250 分位"),
    ("volatility", "波动率", "20日实现波动率分位(反转)"),
    ("valuation", "估值", "全市场 PB 中位分位"),
    ("leverage", "杠杆", "融资余额20日变化率分位(仅沪市)"),
]


def _fear_greed_figure(score_series) -> go.Figure:
    """恐惧贪婪复合分(0-100)历史:恐惧区(绿)/贪婪区(红)底色 + 极端档线 + 当前点。
    底色语义与偏离度横幅一致(A股 红=超买/贪婪 · 绿=超卖/恐惧)。"""
    s = pd.Series(score_series, dtype=float).dropna()
    fig = go.Figure()
    fig.add_hrect(y0=0, y1=45, fillcolor=_PAL["good"], opacity=0.07, line_width=0)
    fig.add_hrect(y0=55, y1=100, fillcolor=_PAL["critical"], opacity=0.07, line_width=0)
    fig.add_hline(y=50, line=dict(color=_PAL["muted"], width=1, dash="dash"))
    fig.add_hline(y=25, line=dict(color=_PAL["good"], width=1, dash="dot"),
                  annotation_text="极度恐惧", annotation_position="left")
    fig.add_hline(y=75, line=dict(color=_PAL["critical"], width=1, dash="dot"),
                  annotation_text="极度贪婪", annotation_position="left")
    if len(s):
        idx = pd.to_datetime(s.index)
        fig.add_trace(go.Scatter(x=idx, y=s.to_numpy(), name="恐惧贪婪分",
                                 line=dict(color=_PAL["ink_sec"], width=2)))
        cur = float(s.iloc[-1])
        zc = _PAL["good"] if cur < 25 else _PAL["critical"] if cur > 75 else _PAL["ink_sec"]
        fig.add_trace(go.Scatter(x=[idx[-1]], y=[cur], mode="markers+text",
                                 marker=dict(size=12, color=zc,
                                             line=dict(color=_PAL["surface"], width=1.5)),
                                 text=[f"现在 {cur:.0f} {fg.classify(cur)}"],
                                 textposition="top center", showlegend=False))
    fig.update_layout(
        height=380, margin=dict(l=44, r=20, t=20, b=30),
        paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
        font=dict(color=_PAL["ink"], family="system-ui, sans-serif"), showlegend=False)
    fig.update_yaxes(range=[0, 100], dtick=25, gridcolor=_PAL["grid"], zeroline=False,
                     title_text="0 极度恐惧 · 100 极度贪婪")
    # x 轴用 autorange(默认)→ plotly 自动把「全部」按钮置为 active 高亮
    # (byt: step==='all' → active ⟺ axis.autorange===true);代价是右侧 autorange 外扩空白,
    # 这是「全部」态的固有表现。rangeslider 同口径 → 拖拽按钮默认满窗。
    xax = dict(gridcolor=_PAL["grid"], zerolinecolor=_PAL["grid"], type="date",
               hoverformat="%Y-%m-%d", rangeslider_visible=True,
               rangeselector=dict(buttons=[
                   dict(count=1, label="1年", step="year", stepmode="backward"),
                   dict(count=3, label="3年", step="year", stepmode="backward"),
                   dict(label="全部", step="all"),
               ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]))
    fig.update_xaxes(**xax)
    return fig


def _fear_greed_html(fg_diag: dict, fig_html: str = "") -> str:
    if not fg_diag.get("valid"):
        return ("<p class='hint'>恐惧贪婪指数数据不足"
                "(需 上证综指/成交额/全市场PB/融资余额 各 ≥ 5 年滚动样本)</p>")
    score = fg_diag["score"]
    label = fg_diag["label"]
    zc = _PAL["good"] if score < 25 else _PAL["critical"] if score > 75 else _PAL["ink_sec"]
    comps = fg_diag["components"]
    comp_tiles = "".join(
        _meter(cn, comps.get(key, float("nan")) / 100.0, desc)
        for key, cn, desc in _FG_COMPONENT_LABELS)
    hint = ("恐惧贪婪 = 动量/流动性/波动率/估值/杠杆 五成分等权复合(每类一票),各做 5 年滚动百分位 → "
            "<b>0-100,高分=贪婪、低分=恐惧</b>。<b>温度计不是开关</b>:和 ⑧地量一样实证无择时 edge,"
            "只给市场情绪的快速读数,供与 ③估值/⑦周期/⑧地量 叠加综合判断,不喂交易引擎。"
            "注:杠杆成分仅沪市(深市总量历史 akshare 不可得);窗口成分不足 5 年的早期段会缺值。")
    out = (
        f"<div class='tiles-row'>"
        f"<div class='tile' style='min-width:200px'><div class='tile-label'>恐惧贪婪复合分</div>"
        f"<div class='tile-value' style='color:{zc};font-size:34px'>{score:.0f}</div>"
        f"<div class='tile-sub'>{_chip(label, zc)}</div></div>"
        f"<div class='tile'><div class='tile-label'>读数口径</div>"
        f"<div class='tile-value' style='font-size:17px;color:{_PAL['ink_sec']}'>5成分 · 5y滚动分位</div>"
        f"<div class='tile-sub'>截至 {fg_diag['date']}</div></div></div>"
        f"<div class='hint' style='margin:12px 0 6px'>五成分(各 0-100,高分=贪婪):</div>"
        f"<div class='tiles-row'>{comp_tiles}</div>"
        f"<div class='hint' style='margin-top:10px'>{hint}</div>")
    if fig_html:
        out += (f"<div class='hint' style='margin:12px 0 4px'>复合分历史:绿区=恐惧/超卖、红区=贪婪/超买"
                f"(A股红=涨·超买 / 绿=跌·超卖,与偏离度横幅一致);虚线=极度恐惧(25)/中性(50)/极度贪婪(75)。</div>"
                f"<div>{fig_html}</div>")
    return out


# ---- ⑩ 关键位监测 ----
_KL_OC_COLOR = {"hold": _PAL["good"], "break_reclaim": _PAL["warning"],
                "break_down": _PAL["critical"]}


def _key_levels_figure(close: pd.Series, levels: list[dict], years: int = 2) -> go.Figure:
    """近 ~years 年收盘 + 关键位横线(线=trace 可悬停/点图例隔离,色=结局) + 回踩标记。"""
    s = close.astype(float)
    s = s.iloc[-252 * years:]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=pd.to_datetime(s.index), y=s.to_numpy(), name="上证综指",
                             line=dict(color=_PAL["series_1"], width=1.6),
                             hovertemplate="%{x|%Y-%m-%d}<br>%{y:.0f}<extra></extra>"))
    px_map = close.to_dict()
    for lv in levels:
        c = _KL_OC_COLOR.get(lv["outcome"], _PAL["ink_sec"])
        dash = "dot" if lv["pending"] else ("dash" if lv["outcome"] == "break_down" else "solid")
        kind = "平台顶" if lv["kind"] == "platform" else "前低"
        fig.add_trace(go.Scatter(
            x=[pd.to_datetime(s.index[0]), pd.to_datetime(s.index[-1])], y=[lv["level"]] * 2,
            mode="lines", name=f"{kind} {lv['level']:.0f} · {slv.OUTCOME_LABEL[lv['outcome']]}",
            line=dict(color=c, width=1.4, dash=dash),
            hovertemplate=f"{kind} 位 {lv['level']:.0f} · {lv['state']}<extra></extra>"))
        if lv["touch"] in px_map:
            fig.add_trace(go.Scatter(x=[pd.to_datetime([lv["touch"]])[0]], y=[lv["level"]],
                                     mode="markers", showlegend=False,
                                     marker=dict(symbol="triangle-down", size=9, color=c),
                                     hovertemplate=f"回踩 {lv['touch']}<extra>{kind} {lv['level']:.0f}</extra>"))
    fig.update_layout(height=340, margin=dict(l=55, r=20, t=20, b=30),
                      paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                      font=dict(color=_PAL["ink"]), showlegend=True,
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig.update_yaxes(gridcolor=_PAL["grid"])
    fig.update_xaxes(gridcolor=_PAL["grid"], type="date", hoverformat="%Y-%m-%d")
    return fig


def _key_levels_html(snap: dict | None, fig_html: str = "") -> str:
    """⑩ 关键位监测:下/上第一档 tile + 关键位状态表 + 三幕剧本提示。只读温度计,不喂引擎。"""
    if not snap or not snap.get("levels"):
        return ("<p class='hint'>关键位监测数据不足(需 上证综指 ≥500 日 + 可检出平台/前低事件;"
                "详见 scripts/validate_support_break.py 深度报告)</p>")
    close, nb, na = snap["close"], snap.get("next_below"), snap.get("next_above")

    def _tile(label: str, val: str, sub: str, color: str = _PAL["ink_sec"]) -> str:
        return (f"<div class='tile'><div class='tile-label'>{label}</div>"
                f"<div class='tile-value' style='color:{color};font-size:20px'>{val}</div>"
                f"<div class='tile-sub'>{sub}</div></div>")

    t_px = _tile("上证综指现价", f"{close:.0f}",
                 f"截至 {snap['date']} · 事件总数 {snap['n_events']}", _PAL["ink"])
    if nb:
        t_nb = _tile("下方第一支撑", f"{nb['level']:.0f}",
                     f"{nb['state']} · 距 {nb['dist']:+.1%}",
                     _KL_OC_COLOR.get(nb["outcome"], _PAL["ink_sec"]))
    else:
        t_nb = _tile("下方第一支撑", "—", "近端无未破位事件位")
    if na:
        t_na = _tile("上方第一压力", f"{na['level']:.0f}",
                     f"{na['state']} · 距 {na['dist']:+.1%}", _PAL["critical"])
    else:
        t_na = _tile("上方第一压力", "—", "近端无已破位事件位")
    tiles = f"<div class='tiles-row'>{t_px}{t_nb}{t_na}</div>"

    def _dist_cell(r: dict) -> str:
        style = "font-variant-numeric:tabular-nums"
        if r["in_zone"]:
            style += f";font-weight:700;color:{_PAL['warning']}"
        mark = " ←在带内" if r["in_zone"] else ""
        return f"<td style='{style}'>{r['dist']:+.1%}{mark}</td>"

    rows = "".join(
        f"<tr><td>{'平台顶' if r['kind'] == 'platform' else '前低'}</td>"
        f"<td style='font-variant-numeric:tabular-nums'>{r['level']:.0f}</td>"
        f"<td>{r['touch']}</td>"
        f"<td style='color:{_KL_OC_COLOR.get(r['outcome'], _PAL['ink_sec'])}'>{r['state']}</td>"
        f"<td>{r['confirm']}</td>{_dist_cell(r)}</tr>"
        for r in snap["levels"])
    table = ("<table class='stat'><tr><th>类型</th><th>位</th><th>回踩日</th><th>状态</th>"
             "<th>确认日</th><th>现价距离</th></tr>" + rows + "</table>")
    hint = ("<b>规则选位,无手画线</b>:平台顶(40日窗振幅≤8%·有效突破后≥5日在带上)+ 前低枢轴(两侧各10日更低·"
            "反弹≥5%)。状态机:回踩带=位±1% · 破位=收盘破 位−1% · 收回=3日内回带 · 守住=15日无破位。"
            "<b>实证(event-study 58例)</b>:破位·未收确认后 <b>20日实现波动显著抬升</b>(19.9% vs 守住14.5%),"
            "回撤中位数与方向胜率不分离 → <b>温度计不是开关,永不喂交易引擎</b>。<br>"
            "<b>三幕剧本(幕0预承诺)</b>:① 缩量止跌+放量反包 → 引擎信号正常执行,不加戏;"
            "② 收盘破位−1% 且 3日不收 → 波动应对:仓位上限降一档、只等右侧、警惕持仓 ETF 相关性→1 的假分散"
            "(理由是波动分布变了,不是看空方向);③ 旧位破位后剧本重写,不沿用旧位。"
            "当前进行中的测试与主流 claim 见 docs/CLAIMS_LEDGER.md。")
    out = tiles + ("<div class='hint' style='margin:10px 0 6px'>关键位状态(新→旧):</div>" + table
                   + f"<div class='hint' style='margin-top:10px'>{hint}</div>")
    if fig_html:
        out += f"<div style='margin-top:12px'>{fig_html}</div>"
    return out


_CSS = """
:root{--surface:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink-sec:#52514e;--muted:#898781;--grid:#e1e0d9;--hover:#f4f3ef}
[data-theme="dark"]{--surface:#1a1a19;--plane:#0d0d0d;--ink:#ffffff;--ink-sec:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--hover:#262624}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);font-family:system-ui,-apple-system,'Segoe UI',sans-serif;padding:24px;max-width:1200px;margin:0 auto}
.topbar{display:flex;justify-content:space-between;align-items:center;margin:0 0 4px}
h1{font-size:22px;margin:0}
h2{font-size:16px;margin:0 0 10px;color:var(--ink-sec)}
.meta{color:var(--ink-sec);font-size:13px;margin-bottom:16px}
section{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:16px;margin-bottom:16px}
section h2{margin-top:0}
table.stat{border-collapse:collapse;width:100%;font-size:13px}
table.stat th,table.stat td{padding:7px 10px;border-bottom:1px solid var(--grid);text-align:left}
table.stat th{color:var(--muted);font-weight:600}
table.stat tbody tr:hover{background:var(--hover)}
.tiles-row{display:flex;gap:16px;flex-wrap:wrap}
.tile{flex:1;min-width:160px;background:var(--plane);border:1px solid var(--grid);border-radius:8px;padding:12px}
.tile-label{color:var(--muted);font-size:12px;margin-bottom:4px}
.tile-value{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2}
.tile-sub{color:var(--ink-sec);font-size:11px;margin-top:6px}
.meter{height:6px;background:var(--grid);border-radius:3px;margin-top:8px;overflow:hidden}
.meter-fill{height:100%;border-radius:3px}
.hint{color:var(--muted);font-size:12px}
.sig-list{margin:0;padding-left:20px;font-size:14px;line-height:2}
#theme-btn{background:var(--surface);border:1px solid var(--grid);color:var(--ink-sec);border-radius:8px;padding:6px 12px;cursor:pointer;font-size:15px;line-height:1}
#theme-btn:hover{background:var(--hover)}
"""


_JS = """
function _isDark(){return document.documentElement.getAttribute('data-theme')==='dark';}
function _applyPlotly(dark){if(!window.Plotly)return;var u={'paper_bgcolor':dark?'#1a1a19':'#fcfcfb','plot_bgcolor':dark?'#1a1a19':'#fcfcfb','font.color':dark?'#ffffff':'#0b0b0b'};['xaxis','xaxis2','yaxis','yaxis2'].forEach(function(a){u[a+'.gridcolor']=dark?'#2c2c2a':'#e1e0d9';u[a+'.zerolinecolor']=dark?'#383835':'#c3c2b7';});document.querySelectorAll('.plotly-graph-div').forEach(function(gd){try{Plotly.relayout(gd,u);}catch(e){}});}
function toggleTheme(){var cur=document.documentElement.getAttribute('data-theme');var isDark=(cur==='dark');var next=isDark?'light':'dark';document.documentElement.setAttribute('data-theme',next);var b=document.getElementById('theme-btn');if(b)b.textContent=next==='dark'?'☀️':'🌙';_applyPlotly(next==='dark');}
window.addEventListener('DOMContentLoaded',function(){var b=document.getElementById('theme-btn');if(b)b.textContent=_isDark()?'☀️':'🌙';_applyPlotly(_isDark());});
/* ③ 估值图 y 轴随 x 窗口自适应:plotly autorange 对全量数据算,切「全部」/拖滑块后
   x 变而 y 不动 → 监听 relayout(只响应含 x range 的事件,防与自身 relayout 成环),
   按可见段逐 y 轴(traces 按 yaxis 分组)重算 min/max±8% 后 relayout。 */
(function(){
  function _yfit(gd){
    if(!gd.data||!gd.layout||!window.Plotly)return;
    var xr=(gd.layout.xaxis&&gd.layout.xaxis.range)||(gd.layout.xaxis2&&gd.layout.xaxis2.range);
    if(!xr)return;
    var x0=new Date(xr[0]).getTime(),x1=new Date(xr[1]).getTime(),g={};
    gd.data.forEach(function(t){
      if(!t.x||!t.y)return;
      var ax=t.yaxis||'y',lo=Infinity,hi=-Infinity;
      for(var i=0;i<t.x.length;i++){
        var tm=new Date(t.x[i]).getTime();
        if(tm>=x0-1&&tm<=x1+1){var v=t.y[i];if(v==null||isNaN(v))continue;if(v<lo)lo=v;if(v>hi)hi=v;}
      }
      if(lo===Infinity)return;
      if(!g[ax])g[ax]=[Infinity,-Infinity];
      g[ax]=[Math.min(g[ax][0],lo),Math.max(g[ax][1],hi)];
    });
    var upd={};
    Object.keys(g).forEach(function(ax){
      var k=ax==='y'?'yaxis':'yaxis'+ax.slice(1),r=g[ax],pad=(r[1]-r[0])*0.08||1;
      upd[k+'.range']=[r[0]-pad,r[1]+pad];
    });
    if(Object.keys(upd).length){try{Plotly.relayout(gd,upd);}catch(e){}}
  }
  document.addEventListener('plotly_relayout',function(e){
    var hasX=Object.keys(e).some(function(k){return /^xaxis\\d*\\.range/.test(k);});
    if(!hasX)return;
    var gd=document.getElementById('val-fig');
    if(gd&&e.target===gd)_yfit(gd);
  },false);
})();
"""


def render_index_timing(store, output_path, period: int = ti.MA_PERIOD,
                        lookback: int | None = None, title: str = "指数择时层看板") -> str:
    """组装六件套 + ⑦相对周期律,写出离线自包含 HTML。返回输出路径。"""
    diag = dz.diagnose_layer(store, period=period, lookback=lookback)
    # ③ 估值开关: PE/PB 时序图(③ 在 HTML 最前 → 由它承载 plotly.js 首加载)
    val = diag.get("valuation") or {}
    val_fig_html = ""
    if val.get("valid"):
        try:
            _pe = store.get_index_pe_series("沪深300")
            _pb = store.get_index_pb_series("沪深300")
            if len(_pe) or len(_pb):
                val_fig_html = _valuation_figure(val, _pe, _pb).to_html(
                    full_html=False, include_plotlyjs=True, div_id="val-fig")
        except Exception:  # noqa: BLE001
            val_fig_html = ""
    # ③ 沪深300 价格趋势线图(顶/底/中位;plotly.js 由 ③ PE/PB 图承载,缺则由本图承载)
    price_fig_html = ""
    try:
        _d300 = store.get_index_daily_series("000300")
        if len(_d300) >= 252:
            price_fig_html = _valuation_price_figure(_d300).to_html(
                full_html=False, include_plotlyjs=(val_fig_html == ""))
    except Exception:  # noqa: BLE001
        price_fig_html = ""
    # ⑦ 相对周期律: 点差图(plotly.js 已由 ③ 承载 → ⑦ include=False;③ 缺则 ⑦ 承载)
    rc = diag.get("relative_cycle") or {}
    rc_fig_html = ""
    if rc.get("valid"):
        try:
            _sp = ti.relative_spread_series(
                store.get_index_daily_series(rc["benchmark"])["close"],
                store.get_index_daily_series(rc["growth"])["close"])
            if len(_sp):
                rc_fig_html = _relative_cycle_figure(rc, _sp).to_html(
                    full_html=False, include_plotlyjs=(val_fig_html == "" and price_fig_html == ""))
        except Exception:  # noqa: BLE001
            rc_fig_html = ""
    # ⑧ 成交量地量监测 figure (plotly.js 已由 ⑦ 或 ① 首图加载 → include_plotlyjs=False)
    tv = diag.get("turnover") or {}
    tv_fig_html = ""
    if tv.get("valid"):
        try:
            _mt = store.get_market_turnover_series()
            _ipx = store.get_index_daily_series(tv["index"])["close"]
            _iev = ti.turnover_dry_events(_mt["total"], start=ti.TURNOVER_MATURE_START)
            if len(_mt) and len(_ipx):
                tv_fig_html = _turnover_figure(_mt, _ipx, _iev).to_html(
                    full_html=False, include_plotlyjs=False)
        except Exception:  # noqa: BLE001
            tv_fig_html = ""
    # ⑨ 恐惧贪婪指数 figure (plotly.js 已由 ③/① 首图加载 → include=False)
    fg_diag = diag.get("fear_greed") or {}
    fg_fig_html = ""
    if fg_diag.get("valid"):
        try:
            fg_fig_html = _fear_greed_figure(fg_diag.get("score_series")).to_html(
                full_html=False, include_plotlyjs=False)
        except Exception:  # noqa: BLE001
            fg_fig_html = ""
    # ⑩ 关键位监测: 上证综指 平台顶/前低 事件位状态机(plotly.js 已由前面图承载)
    kl_snap, kl_fig_html = None, ""
    try:
        _sse = store.get_index_daily_series("000001")
        if len(_sse) >= 500:
            kl_snap = slv.monitor_snapshot(_sse["close"], _sse.get("volume"))
            if kl_snap.get("levels"):
                kl_fig_html = _key_levels_figure(_sse["close"], kl_snap["levels"]).to_html(
                    full_html=False, include_plotlyjs=False)
    except Exception:  # noqa: BLE001
        kl_snap, kl_fig_html = None, ""
    figs_html, first = [], (val_fig_html == "" and price_fig_html == "" and rc_fig_html == "")   # ③/⑦ 已加载 plotly.js → ① 首图不再重复
    for sym, nm in dz.BROAD_INDICES:
        df = store.get_index_daily_series(sym)
        if len(df) < period:
            continue
        fig = _deviation_figure(sym, nm, df, period)
        figs_html.append(fig.to_html(full_html=False, include_plotlyjs=first))
        first = False
    last_date = next((i.get("date_last") for i in diag["indices"].values() if i.get("valid")), "—")
    html = (
        f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><style>{_CSS}</style></head><body>"
        f"<div class='topbar'><h1>{title}</h1>"
        f"<button id='theme-btn' onclick='toggleTheme()'>🌙</button></div>"
        f"<div class='meta'>数据截至 {last_date} · {period}日线 · 生成于 {datetime.now():%Y-%m-%d %H:%M}</div>"
        f"{_cycle_stage_chip(diag)}"
        f"<h2>③ 估值开关</h2><section>{_valuation_tile_html(diag['valuation'], val_fig_html, price_fig_html)}</section>"
        f"<h2>⑥ 市场温度·大小盘温差</h2><section>{_market_temp_html(diag['market_temp'])}</section>"
        f"<h2>⑦ 相对周期律·沪深成长温差</h2><section>{_relative_cycle_html(rc, rc_fig_html)}</section>"
        f"<h2>⑧ 成交量地量监测</h2><section>{_turnover_html(tv, tv_fig_html)}</section>"
        f"<h2>⑨ 恐惧贪婪指数</h2><section>{_fear_greed_html(fg_diag, fg_fig_html)}</section>"
        f"<h2>⑩ 关键位监测</h2><section>{_key_levels_html(kl_snap, kl_fig_html)}</section>"
        f"<h2>④ 蓝筹 vs 成长 仓位倾向</h2><section>{_style_card_html(diag['style'])}</section>"
        f"<h2>② 趋势状态</h2><section>{_trend_table_html(diag)}</section>"
        f"<h2>⑤ 有效突破/跌破信号</h2><section>{_signals_html(diag)}"
        f"<div class='hint' style='margin-top:8px'>有效突破/跌破 = 近 5 日内真正穿越 60 日线 + 偏离≥2%"
        f"(S13「收盘价穿越 60 日线 ±2% 以上」);gN=强度(2=±2%、3=±3%)。仅在线上/下但无近期穿越者不计;"
        f"震荡市标注信号谨慎。</div></section>"
        f"<h2>① 偏离极值曲线</h2><section>"
        f"<div class='hint' style='margin-bottom:10px'>主图:收盘价 vs 60日线;副图:偏离度(价格−均线)÷均线,"
        f"虚线=历史极值(红=正极值/超买,蓝=负极值/超卖),黑点=当前。"
        f"<b style='color:{_PAL['pos_extreme']}'>▲/▼=历史极值事件</b>"
        f"(全历史分位≤5%/≥95%区间的最深处一点),标注「第k低/高·±x%」——"
        f"<b>第几=全历史绝对排名(1=史上最极端,唯一不重复)</b>"
        f"(纯历史观察·不指导交易)。点贴近虚线=接近历史极值。</div>"
        + "".join(figs_html) + "</section>"
        f"<script>{_JS}</script></body></html>"
    )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)
