"""⑧ 成交量地量 event-study 报告(只读诊断):实证验证'地量→价底'的时效与胜率。

地量 = 两市成交额 / MA250 ≤ 0.6(regime 自适应,消除名义额长期上行)。对历史每次地量事件,
算上证综指前瞻收益 + 量底→价底天数,输出:多 horizon 胜率/收益分布 + 量底→价底天数分布 +
事件清单 + 价量双轴图。

实证预期:时效(量底→价底中位~1月)扎实;短期(≤20日)胜率~50%(价仍跌向底),中期(40-60日)
才现温和优势。样本偏稀(~100+次/30年)→ 经验参考非定律。

Usage:
  python scripts/validate_volume_bottom.py            # 默认上证综指 000001
  python scripts/validate_volume_bottom.py --index 000300  # 换沪深300
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from stockagent.config import get_config
from stockagent.data.store import Store
from stockagent.tracker import indicators as ti

_PAL = {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink_sec": "#52514e",
        "muted": "#898781", "grid": "#e1e0d9", "baseline": "#c3c2b7",
        "series_1": "#2a78d6", "series_2": "#008300", "warning": "#fab219",
        "good": "#0ca30c", "critical": "#d03b3b"}

_CSS = """
:root{--surface:#fcfcfb;--ink:#0b0b0b;--ink-sec:#52514e;--muted:#898781;--grid:#e1e0d9}
body{margin:0;background:#f9f9f7;color:var(--ink);font-family:system-ui,sans-serif;padding:24px;max-width:1100px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:18px 0 8px;color:var(--ink-sec)}
.meta{color:var(--ink-sec);font-size:13px;margin-bottom:16px}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}
th,td{padding:6px 10px;border-bottom:1px solid var(--grid);text-align:right}
th{text-align:left;color:var(--muted);font-weight:600}
.hint{color:var(--muted);font-size:12px}
"""


def _per_event(turnover: pd.Series, px: pd.Series, events: list, forward: tuple):
    """每个地量事件的明细:日期、成交额、比值、各 horizon 前瞻收益、量底→价底天数。"""
    df = pd.DataFrame({"t": turnover, "px": px}).dropna()
    df = df[df["t"] > 0]
    ratio = df["t"] / df["t"].rolling(ti.TURNOVER_MA).mean()
    lby = ti.turnover_new_low_years(df["t"])
    maxf = max(forward)
    rows = []
    for idx in events:
        pos = df.index.get_loc(idx)
        if pos + maxf >= len(df):
            continue
        rec = {"date": str(idx)[:10], "turnover_yi": float(df["t"].iloc[pos]) / 1e8,
               "ratio": float(ratio.iloc[pos]), "lookback_yr": float(lby.iloc[pos])}
        px0 = float(df["px"].iloc[pos])
        seg = df["px"].iloc[pos + 1: pos + 1 + maxf]
        rec["ttb"] = int(seg.values.argmin()) + 1
        for N in forward:
            rec[f"ret_{N}"] = float(df["px"].iloc[pos + N]) / px0 - 1.0
        rows.append(rec)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="000001", help="价格基准指数(默认 000001 上证综指)")
    ap.add_argument("--out", default="data/volume_bottom_study.html")
    args = ap.parse_args()

    st = Store(get_config().db_path)
    mt = st.get_market_turnover_series()
    px = st.get_index_daily_series(args.index)["close"]
    if len(mt) < ti.TURNOVER_MA + 61 or len(px) < ti.TURNOVER_MA + 61:
        print("数据不足:需 两市成交额 + 指数 各 ≥ MA250+60 日")
        sys.exit(1)
    turnover = mt["total"]
    events = ti.turnover_dry_events(turnover, start=ti.TURNOVER_MATURE_START)
    stats = ti.volume_bottom_stats(turnover, px, start=ti.TURNOVER_MATURE_START)
    long_fwd = (5, 10, 20, 40, 60, 120, 250)   # 长 horizon 看极端地量的延迟反弹(120日≈6月,250日≈1年)
    detail = _per_event(turnover, px, events, long_fwd)

    # ---- console summary ----
    print(f"=== ⑧ 成交量地量 event-study (基准 {args.index}) ===")
    print(f"地量定义: 两市成交额/MA250 ≤ {ti.TURNOVER_DRY_THRESHOLD}  |  事件 {stats['detected']} 可用样本 {stats['sample']}")
    print(f"量底→价底: 中位 {stats['time_to_bottom_median']:.0f} 交易日  最长 {stats['time_to_bottom_max']:.0f}")
    print("  horizon | 胜率  | 中位收益")
    for N in ti.TURNOVER_FORWARD:
        print(f"    {N:>2}日 | {stats[f'win_rate_{N}'] * 100:>3.0f}% | {stats[f'median_ret_{N}'] * 100:+.1f}%")

    # ---- figure 1: 价量双轴 + 地量事件 ----
    fig1 = make_subplots(specs=[[{"secondary_y": True}]])
    fig1.add_trace(go.Scatter(x=pd.to_datetime(px.index), y=px.to_numpy(), name=args.index,
                              line=dict(color=_PAL["series_1"], width=1.6)), secondary_y=False)
    fig1.add_trace(go.Scatter(x=pd.to_datetime(turnover.index), y=(turnover / 1e8).to_numpy(),
                              name="两市成交额(亿)", line=dict(color=_PAL["series_2"], width=1.3),
                              opacity=0.85), secondary_y=True)
    vmap = turnover.to_dict()
    rmap = (turnover / turnover.rolling(ti.TURNOVER_MA).mean()).to_dict()
    lmap = ti.turnover_new_low_years(turnover).to_dict()

    def _dry_trace(subset, name, color, size, opacity):
        if not subset:
            return
        customdata = [[vmap.get(d, float("nan")) / 1e8, rmap.get(d, float("nan")),
                       lmap.get(d, float("nan"))] for d in subset]
        fig1.add_trace(go.Scatter(
            x=pd.to_datetime([str(d) for d in subset]),
            y=[vmap.get(d, float("nan")) / 1e8 for d in subset],
            name=name, mode="markers",
            marker=dict(color=color, size=size, opacity=opacity, line=dict(color=_PAL["ink"], width=0.5)),
            customdata=customdata,
            hovertemplate=("<b>%{x|%Y-%m-%d}</b> " + name + "<br>"
                           "成交额 %{customdata[0]:.0f} 亿<br>"
                           "/MA250 = %{customdata[1]:.2f}(越低越地)<br>"
                           "近 %{customdata[2]:.1f} 年最低成交额<extra></extra>")),
            secondary_y=True)

    ext_lb = ti.TURNOVER_EXTREME_LOOKBACK
    extreme = [d for d in events if lmap.get(d, float("nan")) >= ext_lb]
    normal = [d for d in events if not (lmap.get(d, float("nan")) >= ext_lb)]
    _dry_trace(normal, "地量(普通)", _PAL["warning"], 6, 0.55)
    _dry_trace(extreme, "地量(极端·近>0.5年最低)", _PAL["critical"], 10, 0.9)
    fig1.update_layout(height=460, margin=dict(l=55, r=60, t=30, b=30),
                       paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                       font=dict(color=_PAL["ink"]), showlegend=True)
    fig1.update_yaxes(title_text=args.index, secondary_y=False, gridcolor=_PAL["grid"])
    fig1.update_yaxes(title_text="成交额(亿)", secondary_y=True, gridcolor=_PAL["grid"])
    fig1.update_xaxes(gridcolor=_PAL["grid"], type="date", hoverformat="%Y-%m-%d",
                      rangeslider_visible=True,
                      rangeselector=dict(buttons=[
                          dict(count=1, label="1年", step="year", stepmode="backward"),
                          dict(count=3, label="3年", step="year", stepmode="backward"),
                          dict(count=5, label="5年", step="year", stepmode="backward"),
                          dict(label="全部", step="all"),
                      ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]))

    # ---- figure 2: 各 horizon 胜率 ----
    Ns = list(ti.TURNOVER_FORWARD)
    wrs = [stats[f"win_rate_{N}"] * 100 for N in Ns]
    fig2 = go.Figure([go.Bar(x=[f"{n}日" for n in Ns], y=wrs,
                             marker_color=[_PAL["critical"] if w < 50 else _PAL["good"] for w in wrs],
                             text=[f"{w:.0f}%" for w in wrs], textposition="outside")])
    fig2.add_hline(y=50, line=dict(color=_PAL["muted"], dash="dash", width=1))
    fig2.update_layout(height=280, margin=dict(l=40, r=20, t=20, b=30),
                       paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                       font=dict(color=_PAL["ink"]), showlegend=False,
                       yaxis_title="胜率%", xaxis_title="地量后持有 N 交易日")
    fig2.update_yaxes(gridcolor=_PAL["grid"], range=[0, 100])
    fig2.update_xaxes(gridcolor=_PAL["grid"])

    # ---- event table ----
    rows_html = "".join(
        f"<tr><td style='text-align:left'>{r['date']}</td><td>{r['turnover_yi']:.0f}</td>"
        f"<td>{r['ratio']:.2f}</td><td>{r['lookback_yr']:.1f}</td><td>{r['ttb']}</td>"
        f"<td style='color:{_PAL['good'] if r['ret_20']>0 else _PAL['critical']}'>{r['ret_20']*100:+.1f}%</td>"
        f"<td style='color:{_PAL['good'] if r['ret_60']>0 else _PAL['critical']}'>{r['ret_60']*100:+.1f}%</td></tr>"
        for r in reversed(detail))  # 最新在上

    # ---- 极端度(近X年最低)分桶 · 各 horizon 胜率(看延迟反弹规律)----
    strat_rows = ""
    for lo, hi, lab in [(0, 0.5, "普通 ≤0.5年"), (0.5, 1, "0.5-1年"),
                        (1, 2, "1-2年"), (2, 999, ">2年")]:
        s = [r for r in detail if lo <= r["lookback_yr"] < hi]
        if not s:
            continue
        wr = lambda n: f"{sum(1 for r in s if r[f'ret_{n}'] > 0) / len(s) * 100:.0f}%"
        strat_rows += (f"<tr><td style='text-align:left'>{lab}</td><td>{len(s)}</td>"
                       f"<td>{wr(20)}</td><td>{wr(60)}</td><td>{wr(120)}</td><td>{wr(250)}</td></tr>")
    strat_html = (f"<h2>按极端度(近X年最低)分桶 · 各 horizon 胜率</h2>"
                  f"<div class='hint'>看规律:极端子集(>0.5年)是否在更长 horizon 才现优势?"
                  f"N=有完整 250 日前瞻的事件。样本薄 → 规律性仅供参考。</div>"
                  f"<table><tr><th style='text-align:left'>lookback</th><th>N</th><th>20日</th>"
                  f"<th>60日</th><th>120日</th><th>250日</th></tr>{strat_rows}</table>")

    last_date = str(px.index[-1])[:10]
    html = (
        f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>成交量地量 event-study</title><style>{_CSS}</style></head><body>"
        f"<h1>⑧ 成交量地量 event-study</h1>"
        f"<div class='meta'>地量=两市成交额/MA250≤{ti.TURNOVER_DRY_THRESHOLD} · 基准 {args.index} · "
        f"数据截至 {last_date} · 事件 {stats['detected']} / 可用样本 {stats['sample']}</div>"
        f"<div class='hint'>实证(成熟市场 2000+):时效(量底→价底中位 {stats['time_to_bottom_median']:.0f} 交易日)扎实;"
        f"但各 horizon 胜率均~50%(无优势;90s 幼年期会虚高,已排除)。样本偏稀 → 经验参考非定律。</div>"
        f"<h2>价量双轴 + 地量事件</h2>{fig1.to_html(full_html=False, include_plotlyjs=True)}"
        f"<h2>各 horizon 胜率(虚线=50%基准)</h2>{fig2.to_html(full_html=False, include_plotlyjs=False)}"
        f"{strat_html}"
        f"<h2>地量事件清单(最新在上)</h2>"
        f"<table><tr><th style='text-align:left'>日期</th><th>成交额(亿)</th><th>/MA250</th>"
        f"<th>近X年最低</th><th>量底→价底(日)</th><th>20日收益</th><th>60日收益</th></tr>{rows_html}</table>"
        f"</body></html>")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"\n报告 -> {out}")


if __name__ == "__main__":
    main()
