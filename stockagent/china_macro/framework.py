"""国内宏观看板渲染(第七看板 data/china_macro.html · 只读旁路 · 永不喂引擎)。

五 section:
① 货币信用 —— ⑪ 货币条件完整版(M2/M1 同比+剪刀差+社融脉冲+episode 状态机+事件标记+实证结论
   meta 读)。复用 tracker.diagnose.diagnose_money_conditions 与 tracker.money_conditions 纯函数
   (china_macro→tracker 同向依赖,与 pool→tracker 同礼遇;不反向、不 re-export)。
② 利率与流动性 —— Shibor / FDR007(DR 系定盘=央行政策目标利率) / LPR / 中债期限结构(10Y−2Y)
   + 央行资产负债表「对其他存款性公司债权」月度差分(OMO/MLF 净投放的滞后近似)。
③ 政策日历 —— 硬编码典型时点(政治局 4/7/12 月·中央经济工作会议·货政报告·两会·LPR·金融数据公布)
   → 下次时点+倒计时;只放事实,观点结算归 docs/CLAIMS_LEDGER.md。
④ 社融可观测成分(nowcast · 观测非预测) —— 地方债逐券月度+月内累计(政府债半程;国债明细无免费源)
   + 社融分项历史(信贷/企业债/股票,官方口径)做分布对照;信贷黑箱诚实留白。
⑤ 会议→M2 转向历史回放 —— 锚点月(3两会/4·7·12政治局+经济工作会议)后 3 个月 M2 同比方向统计,
   「12月定调→来年放水」叙事的历史对照;历史统计非因果非信号。

先行代理未过 event-study 礼遇前一律为观察项,不出现「预测/信号」措辞。
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from ..tracker import money_conditions as mcm
from ..tracker.diagnose import diagnose_money_conditions
from . import policy

# ---- palette (dataviz reference, light mode; 与 tracker 看板同族) ----
_PAL = {
    "surface": "#fcfcfb", "plane": "#f9f9f7", "ink": "#0b0b0b",
    "ink_sec": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
    "series_1": "#2a78d6", "series_2": "#008300", "series_3": "#7b3fbf",
    "pos_extreme": "#d03b3b", "neg_extreme": "#1c5cab",
    "good": "#0ca30c", "warning": "#fab219", "critical": "#d03b3b",
}

_MONEY_EVENT_STYLE = {
    "top_turn": ("triangle-down", _PAL["pos_extreme"], "见顶回落"),
    "bottom_turn": ("triangle-up", _PAL["good"], "触底回升"),
    "down_confirm": ("diamond", _PAL["warning"], "下行确认"),
}


def _last_f(df: pd.DataFrame, col: str) -> float:
    if df is None or len(df) == 0 or col not in df.columns:
        return float("nan")
    v = pd.to_numeric(df[col], errors="coerce").dropna()
    return float(v.iloc[-1]) if len(v) else float("nan")


def _tile(label: str, val: str, sub: str, color: str = _PAL["ink_sec"]) -> str:
    return (f"<div class='tile'><div class='tile-label'>{label}</div>"
            f"<div class='tile-value' style='color:{color};font-size:20px'>{val}</div>"
            f"<div class='tile-sub'>{sub}</div></div>")


def _chip(label: str, color: str) -> str:
    return (f"<span style='display:inline-block;padding:2px 8px;border-radius:8px;"
            f"background:{color}22;color:{color};font-size:12px;font-weight:600;"
            f"border:1px solid {color}55'>{label}</span>")


# ---- ① 货币信用 ----
def _money_figure(mcd: dict) -> go.Figure:
    fig = go.Figure()
    m2, m1 = mcd["m2_series"], mcd["m1_series"]
    sc, pu = mcd["scissor_series"], mcd["pulse_series"]
    fig.add_trace(go.Scatter(
        x=pd.to_datetime(m2.index), y=m2.to_numpy(dtype=float), name="M2 同比%",
        line=dict(color=_PAL["series_1"], width=2.2),
        hovertemplate="%{x|%Y-%m}<br>M2 同比 %{y:.1f}%<extra></extra>"))
    if len(m1):
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(m1.index), y=m1.to_numpy(dtype=float),
            name="M1 同比%(2024-01起不可比)", line=dict(color=_PAL["warning"], width=1.4),
            hovertemplate="%{x|%Y-%m}<br>M1 同比 %{y:.1f}%<extra></extra>"))
    if len(sc):
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(sc.index), y=sc.to_numpy(dtype=float), name="剪刀差 M1−M2",
            line=dict(color=_PAL["ink_sec"], width=1.2, dash="dash"),
            hovertemplate="%{x|%Y-%m}<br>剪刀差 %{y:.1f}pp<extra></extra>"))
    if len(pu):
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(pu.index), y=pu.to_numpy(dtype=float), name="社融脉冲%(右轴)",
            yaxis="y2", line=dict(color=_PAL["good"], width=1.6),
            hovertemplate="%{x|%Y-%m}<br>社融脉冲 %{y:.1f}%<extra></extra>"))
    m2_map = {str(m)[:7]: float(v) for m, v in m2.items()}
    for kind, (sym, color, label) in _MONEY_EVENT_STYLE.items():
        ev = [e for e in mcd.get("events", []) if e["kind"] == kind]
        if not ev:
            continue
        fig.add_trace(go.Scatter(
            x=pd.to_datetime([e["month"] for e in ev]),
            y=[m2_map.get(e["month"][:7], e["yoy"]) for e in ev],
            name=label, mode="markers",
            marker=dict(symbol=sym, size=10, color=color,
                        line=dict(color=_PAL["ink"], width=0.5)),
            customdata=[[e["yoy"]] for e in ev],
            hovertemplate=("<b>%{x|%Y-%m}</b> " + label
                           + "<br>当时 M2 同比 %{customdata[0]:.1f}%<extra></extra>")))
    fig.add_vline(x=pd.to_datetime(mcm.M1_BREAK), line_dash="dot", line_color=_PAL["muted"],
                  annotation_text="M1 换新口径(含个人活期)", annotation_font=dict(size=10))
    fig.update_layout(
        height=420, margin=dict(l=44, r=52, t=20, b=30),
        paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
        font=dict(color=_PAL["ink"], family="system-ui, sans-serif"), showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis=dict(title_text="同比 %", gridcolor=_PAL["grid"], zerolinecolor=_PAL["grid"]),
        yaxis2=dict(title_text="社融脉冲 %(TTM增量/M2)", overlaying="y", side="right",
                    gridcolor="rgba(0,0,0,0)", zeroline=False, showgrid=False))
    fig.update_xaxes(gridcolor=_PAL["grid"], type="date", hoverformat="%Y-%m",
                     rangeslider_visible=True,
                     rangeselector=dict(buttons=[
                         dict(count=3, label="3年", step="year", stepmode="backward"),
                         dict(count=5, label="5年", step="year", stepmode="backward"),
                         dict(label="全部", step="all"),
                     ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]))
    return fig


def _money_html(mcd: dict, fig_html: str, conclusion: str) -> str:
    if not mcd.get("valid"):
        return ("<p class='hint'>货币信用数据不足(需 china_money_supply ≥24 个月;先跑 "
                "python scripts/backfill_index.py --money)</p>")
    st = mcd["state"]
    zc = (_PAL["warning"] if st.get("direction") == "down"
          else _PAL["good"] if st.get("direction") == "up" else _PAL["ink_sec"])
    msl = st.get("months_since_last")
    msl_txt = f"距上次事件 {msl} 个月" if msl is not None else "历史无事件"
    month_disp = mcd["month_last"][:7]
    m2v, m1v = mcd["m2_yoy"], mcd["m1_yoy"]
    scv = (float(mcd["scissor_series"].iloc[-1]) if len(mcd["scissor_series"])
           and not pd.isna(mcd["scissor_series"].iloc[-1]) else float("nan"))
    puv = (float(mcd["pulse_series"].iloc[-1]) if len(mcd["pulse_series"])
           and not pd.isna(mcd["pulse_series"].iloc[-1]) else float("nan"))
    tsf_sub = f"截至 {mcd['tsf_last'][:7]}" if mcd.get("tsf_last") else "(缺社融数据)"
    tiles = (
        "<div class='tiles-row'>"
        + _tile("M2 同比", f"{m2v:.1f}%", month_disp + " · 全社会的钱的增速", _PAL["series_1"])
        + _tile("M1 同比", f"{m1v:.1f}%", "现金+活期=随时能花的活钱(新口径)", _PAL["warning"])
        + _tile("剪刀差 M1−M2", f"{scv:+.1f}pp", "活钱 vs 总池(负=钱沉淀)", _PAL["ink_sec"])
        + _tile("社融脉冲", f"{puv:.1f}%", tsf_sub + " · 增量TTM/M2 代理", _PAL["good"])
        + "<div class='tile' style='min-width:220px'><div class='tile-label'>episode 状态机</div>"
          f"<div class='tile-value' style='color:{zc};font-size:18px'>{mcd['state_label']}</div>"
          f"<div class='tile-sub'>{_chip('2月动量口径', zc)} {msl_txt}</div></div></div>")
    concl = (conclusion or "").strip()
    concl_html = (f"<b>实证(event-study)</b> [臂数字 = 事件后 60 日<b>上涨概率</b>% / 中位涨跌% "
                  f"(n=历史次数);基线 = 无条件随机日]: {concl}" if concl
                  else "<b>实证(event-study)</b>: 未运行 python scripts/validate_m2_timing.py —— 结论注入占位")
    hint = ("<b>M2 = 广义货币的同比增速,「放水」水位计</b>;M1 = 现金+活期存款 = 社会的「活钱」。"
            "「M2 定大盘」叙事在此只作<b>纯数据跟踪</b>——怎么用由你综合各看板自行决定。"
            "<b>温度计非开关,永不喂交易引擎</b>(指数择时看板 ⑪ 为同源择时摘要)。<br>"
            f"口径:金十源·月频(次月中旬公布上月,天然滞后 2-6 周);事件标记=2月动量 run 状态机"
            f"(连降{mcm.DOWN_RUN}月=下行确认/大段后反向{mcm.TURN_RUN}月=拐点,与 validate_m2_timing 同规则);"
            "M1 于 2024-01 换新口径(含个人活期)——前后不可比,只展示不进研究;"
            "社融脉冲=增量TTM/M2 代理(存量同比无免费源)。<br>" + concl_html)
    return tiles + f"<div class='hint' style='margin-top:10px'>{hint}</div>" + (
        f"<div style='margin-top:12px'>{fig_html}</div>" if fig_html else "")


# ---- ② 利率与流动性 ----
def _funding_figure(repo: pd.DataFrame, shibor: pd.DataFrame) -> go.Figure:
    """资金面:FDR007(DR 系定盘) + FR007 + Shibor O/N/3M(日频)。"""
    fig = go.Figure()
    if repo is not None and len(repo) and "fdr007" in repo.columns:
        fig.add_trace(go.Scatter(x=pd.to_datetime(repo.index), name="FDR007(政策目标利率)",
                                 y=pd.to_numeric(repo["fdr007"], errors="coerce"),
                                 line=dict(color=_PAL["series_1"], width=2),
                                 hovertemplate="%{x|%Y-%m-%d}<br>FDR007 %{y:.2f}%<extra></extra>"))
        fig.add_trace(go.Scatter(x=pd.to_datetime(repo.index), name="FR007",
                                 y=pd.to_numeric(repo["fr007"], errors="coerce"),
                                 line=dict(color=_PAL["muted"], width=1.1),
                                 hovertemplate="%{x|%Y-%m-%d}<br>FR007 %{y:.2f}%<extra></extra>"))
    if shibor is not None and len(shibor):
        for col, nm, c, w in (("overnight", "Shibor O/N", _PAL["warning"], 1.3),
                              ("m3", "Shibor 3M", _PAL["series_2"], 1.3)):
            if col in shibor.columns:
                s = pd.to_numeric(shibor[col], errors="coerce")
                fig.add_trace(go.Scatter(x=pd.to_datetime(s.index), name=nm, y=s,
                                         line=dict(color=c, width=w, dash="dot" if col == "m3" else "solid"),
                                         hovertemplate=f"%{{x|%Y-%m-%d}}<br>{nm} %{{y:.2f}}%<extra></extra>"))
    fig.update_layout(height=360, margin=dict(l=44, r=20, t=20, b=30),
                      paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                      font=dict(color=_PAL["ink"]), showlegend=True,
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                      yaxis=dict(title_text="%", gridcolor=_PAL["grid"]))
    fig.update_xaxes(gridcolor=_PAL["grid"], type="date", hoverformat="%Y-%m-%d",
                     rangeslider_visible=True,
                     rangeselector=dict(buttons=[
                         dict(count=1, label="1年", step="year", stepmode="backward"),
                         dict(count=3, label="3年", step="year", stepmode="backward"),
                         dict(label="全部", step="all"),
                     ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]))
    return fig


def _term_figure(lpr: pd.DataFrame, bond: pd.DataFrame) -> go.Figure:
    """政策与期限:LPR(月频 step) + 中债 10Y(日频) + 10Y−2Y(右轴)。"""
    fig = go.Figure()
    if lpr is not None and len(lpr):
        for col, nm, c in (("lpr1y", "LPR 1Y", _PAL["series_1"]), ("lpr5y", "LPR 5Y", _PAL["series_3"])):
            if col in lpr.columns:
                s = pd.to_numeric(lpr[col], errors="coerce").dropna()
                if len(s):
                    fig.add_trace(go.Scatter(x=pd.to_datetime(s.index), name=nm, y=s,
                                             line=dict(color=c, width=1.6, shape="hv"),
                                             hovertemplate=f"%{{x|%Y-%m-%d}}<br>{nm} %{{y:.2f}}%<extra></extra>"))
    if bond is not None and len(bond) and "y10" in bond.columns:
        s = pd.to_numeric(bond["y10"], errors="coerce").dropna()
        fig.add_trace(go.Scatter(x=pd.to_datetime(s.index), name="中债 10Y", y=s,
                                 line=dict(color=_PAL["critical"], width=1.4),
                                 hovertemplate="%{x|%Y-%m-%d}<br>10Y %{y:.2f}%<extra></extra>"))
        sp = pd.to_numeric(bond["spread_10y2y"], errors="coerce").dropna()
        if len(sp):
            fig.add_trace(go.Scatter(x=pd.to_datetime(sp.index), name="10Y−2Y(右轴)", y=sp,
                                     yaxis="y2", line=dict(color=_PAL["ink_sec"], width=1.1, dash="dash"),
                                     hovertemplate="%{x|%Y-%m-%d}<br>10Y−2Y %{y:.2f}pp<extra></extra>"))
    fig.update_layout(height=360, margin=dict(l=44, r=52, t=20, b=30),
                      paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                      font=dict(color=_PAL["ink"]), showlegend=True,
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                      yaxis=dict(title_text="利率 %", gridcolor=_PAL["grid"]),
                      yaxis2=dict(title_text="期限利差 pp", overlaying="y", side="right",
                                  gridcolor="rgba(0,0,0,0)", zeroline=False, showgrid=False))
    fig.update_xaxes(gridcolor=_PAL["grid"], type="date", hoverformat="%Y-%m-%d",
                     rangeslider_visible=True,
                     rangeselector=dict(buttons=[
                         dict(count=1, label="1年", step="year", stepmode="backward"),
                         dict(count=3, label="3年", step="year", stepmode="backward"),
                         dict(label="全部", step="all"),
                     ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]))
    return fig


def _omo_figure(cb: pd.DataFrame) -> go.Figure:
    """OMO/MLF 净投放近似:柱=「对其他存款性公司债权」月度差分(万亿),线=余额(右轴,万亿)。"""
    fig = go.Figure()
    if cb is not None and len(cb) and "claim_odc" in cb.columns:
        s = pd.to_numeric(cb["claim_odc"], errors="coerce").dropna() / 1e4   # 亿→万亿
        diff = s.diff().dropna()
        fig.add_trace(go.Bar(
            x=pd.to_datetime(diff.index), y=diff.to_numpy(), name="月度净投放(近似)",
            marker_color=[_PAL["critical"] if v >= 0 else _PAL["good"] for v in diff],
            hovertemplate="%{x|%Y-%m}<br>净投放 %{y:+.2f} 万亿(近似)<extra></extra>"))
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(s.index), y=s.to_numpy(), name="OMO/MLF 余额(右轴)",
            yaxis="y2", line=dict(color=_PAL["series_1"], width=1.6),
            hovertemplate="%{x|%Y-%m}<br>余额 %{y:.1f} 万亿<extra></extra>"))
    fig.update_layout(height=320, margin=dict(l=44, r=52, t=20, b=30), barmode="group",
                      paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                      font=dict(color=_PAL["ink"]), showlegend=True,
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                      yaxis=dict(title_text="月度变动 万亿", gridcolor=_PAL["grid"],
                                 zerolinecolor=_PAL["grid"]),
                      yaxis2=dict(title_text="余额 万亿", overlaying="y", side="right",
                                  gridcolor="rgba(0,0,0,0)", zeroline=False, showgrid=False))
    fig.update_xaxes(gridcolor=_PAL["grid"], type="date", hoverformat="%Y-%m",
                     rangeslider_visible=True)
    return fig


def _rates_html(repo, shibor, lpr, bond, cb, figs: list[str]) -> str:
    if all(df is None or len(df) == 0 for df in (repo, shibor, lpr, bond)):
        return ("<p class='hint'>利率与流动性数据不足(先跑 "
                "python scripts/backfill_china_macro.py --rates)</p>")
    fdr7 = _last_f(repo, "fdr007")
    son = _last_f(shibor, "overnight")
    lpr1, lpr5 = _last_f(lpr, "lpr1y"), _last_f(lpr, "lpr5y")
    y10, sp = _last_f(bond, "y10"), _last_f(bond, "spread_10y2y")
    omo_txt, omo_sub = "—", "央行资产负债表缺"
    if cb is not None and len(cb) and "claim_odc" in cb.columns:
        s = pd.to_numeric(cb["claim_odc"], errors="coerce").dropna()
        if len(s) >= 2:
            d = float(s.iloc[-1] - s.iloc[-2]) / 1e4
            omo_txt = f"{d:+.2f}万亿"
            omo_sub = f"{str(s.index[-1])[:7]} 月净投放(近似,滞后~1月)"
    tiles = (
        "<div class='tiles-row'>"
        + _tile("FDR007", f"{fdr7:.2f}%" if fdr7 == fdr7 else "—",
                "DR 系定盘·央行政策目标利率区", _PAL["series_1"])
        + _tile("Shibor O/N", f"{son:.2f}%" if son == son else "—",
                "银行间隔夜资金面脉搏", _PAL["warning"])
        + _tile("LPR 1Y / 5Y", f"{lpr1:.1f}% / {lpr5:.1f}%" if lpr1 == lpr1 else "—",
                "每月 20 日报价·贷款定价锚", _PAL["series_3"])
        + _tile("中债 10Y", f"{y10:.2f}%" if y10 == y10 else "—",
                f"10Y−2Y {sp*100:.0f}bp" if sp == sp else "期限结构缺",
                _PAL["critical"])
        + _tile("OMO/MLF 月净投放", omo_txt, omo_sub, _PAL["ink_sec"])
        + "</div>")
    hint = ("<b>利率是资金的价格,流动性是水的管道</b>:FDR007(银银间回购定盘)比 Shibor 更贴央行政策意图——"
            "持续低于政策利率=水充裕,持续偏高=紧;Shibor 3M 报价利率反映中期预期;LPR 为贷款定价锚(20 日月频);"
            "中债 10Y−2Y 期限利差走阔=增长/通胀预期升,压平=压低预期。<br>"
            "口径:金十源(2026-08 探针验证);FDR007/FR007 自 2020-09、Shibor 自 2015、LPR 自 1991、"
            "中债自 1990。<b>OMO 月净投放=央行资产负债表「对其他存款性公司债权」月度差分(滞后~1 月的近似;"
            "日度净投放/票据转贴利率免费源缺→待补,见 docs/EXECUTION_PLAN-国内宏观.md)</b>。"
            "先行代理未过 event-study 礼遇前一律为观察项,不构成信号;永不喂交易引擎。"
            "<span style='color:#898781'>A股配色:红=投放/多,绿=回笼。</span>")
    out = tiles + f"<div class='hint' style='margin-top:10px'>{hint}</div>"
    out += "".join(f"<div style='margin-top:12px'>{f}</div>" for f in figs if f)
    return out


# ---- ④ 社融可观测成分(nowcast · 观测非预测) ----
def _lgb_figure(monthly: pd.Series, cur_ym: str) -> go.Figure:
    """地方债月度实际发行柱(亿元);当前月高亮=月内进行时(不完整月)。"""
    fig = go.Figure()
    if monthly is not None and len(monthly):
        colors = [_PAL["critical"] if ym == cur_ym else _PAL["series_1"]
                  for ym in monthly.index]
        fig.add_trace(go.Bar(
            x=pd.to_datetime([f"{ym}-01" for ym in monthly.index]),
            y=monthly.to_numpy(dtype=float), name="月度实际发行(亿)",
            marker_color=colors,
            hovertemplate="%{x|%Y-%m}<br>%{y:,.0f} 亿元<extra></extra>"))
    fig.update_layout(height=300, margin=dict(l=56, r=20, t=20, b=30),
                      paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                      font=dict(color=_PAL["ink"]), showlegend=False,
                      yaxis=dict(title_text="亿元", gridcolor=_PAL["grid"]))
    fig.update_xaxes(gridcolor=_PAL["grid"], type="date", hoverformat="%Y-%m",
                     rangeslider_visible=True)
    return fig


def _tsf_comp_figure(tsf: pd.DataFrame) -> go.Figure:
    """社融分项月度(亿元,社融官方口径):人民币贷款/企业债券/股票融资。"""
    fig = go.Figure()
    if tsf is not None and len(tsf):
        for col, nm, c in (("rmb_loans", "人民币贷款", _PAL["series_1"]),
                           ("corp_bond", "企业债券", _PAL["series_2"]),
                           ("equity_fin", "股票融资", _PAL["series_3"])):
            if col not in tsf.columns:
                continue
            s = pd.to_numeric(tsf[col], errors="coerce").dropna()
            if not len(s):
                continue
            fig.add_trace(go.Scatter(
                x=pd.to_datetime(s.index), y=s.to_numpy(dtype=float), name=nm,
                line=dict(color=c, width=1.5),
                hovertemplate=f"%{{x|%Y-%m}}<br>{nm} %{{y:,.0f}} 亿<extra></extra>"))
    fig.update_layout(height=300, margin=dict(l=56, r=20, t=20, b=30),
                      paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                      font=dict(color=_PAL["ink"]), showlegend=True,
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                      yaxis=dict(title_text="亿元(月增)", gridcolor=_PAL["grid"],
                                 zerolinecolor=_PAL["grid"]))
    fig.update_xaxes(gridcolor=_PAL["grid"], type="date", hoverformat="%Y-%m",
                     rangeslider_visible=True)
    return fig


def _nowcast_html(monthly, tsf, figs: list[str], cur_ym: str) -> str:
    """④ 社融可观测成分:政府债(地方债)月内 nowcast + 社融分项历史。观测非预测。"""
    if (monthly is None or len(monthly) == 0) and (tsf is None or len(tsf) == 0):
        return ("<p class='hint'>社融可观测成分数据不足(先跑 "
                "python scripts/backfill_china_macro.py --lgb 与 backfill_index.py --money)</p>")
    from . import nowcast as nc
    prog = nc.mtd_progress(monthly, cur_ym)
    ratio_txt = f"{prog['ratio'] * 100:.0f}%" if prog["ratio"] == prog["ratio"] else "—"
    cur_txt = f"{prog['cur']:,.0f}亿" if prog["cur"] == prog["cur"] else "—"
    last_txt = f"{prog['last_full']:,.0f}亿" if prog["last_full"] == prog["last_full"] else "—"

    def _tsf_tile(col: str, label: str, color: str) -> str:
        if tsf is None or len(tsf) == 0 or col not in tsf.columns:
            return _tile(label, "—", "(缺分项数据)", _PAL["muted"])
        s = pd.to_numeric(tsf[col], errors="coerce").dropna()
        if not len(s):
            return _tile(label, "—", "(分项缺)", _PAL["muted"])
        return _tile(label, f"{float(s.iloc[-1]):,.0f}亿",
                     f"{str(s.index[-1])[:7]} 月 · 社融口径", color)

    tiles = ("<div class='tiles-row'>"
             + _tile(f"本月地方债已发行({cur_ym})", cur_txt,
                     f"vs 近12月完整月均值 {ratio_txt} · 月内进行时", _PAL["critical"])
             + _tile("上月地方债全月", last_txt, "完整月对照", _PAL["series_1"])
             + _tsf_tile("rmb_loans", "信贷分项(社融口径)", _PAL["series_1"])
             + _tsf_tile("corp_bond", "企业债分项(社融口径)", _PAL["series_2"])
             + "</div>")
    hint = ("<b>社融分子端,只有一部分能提前看见</b>:政府债(此处=地方债逐券明细,国债发行明细无免费源→半程)"
            "与企业债按发行/缴款**日度**可观测;**信贷(最大头)是月度黑箱**——免费票据利率源缺,待补"
            "(见执行规划)。分项历史为社融官方口径(滞后 2-3 月),做「信贷/企业债通常多大」的分布对照。<br>"
            "口径三重诚实:①cninfo 逐券口径(含再融资/跨市场),绝对量级未与官方月报交叉校验——本 section 用于"
            "**月度节奏与月内累计的相对观察**;②当前月是进行时(红柱不完整,和完整月比天然偏低);"
            "③「本月已发行→社融政府债分项」还需净融资(发行−到期)换算,此处只看发行侧。<b>观测非预测,"
            "永不喂引擎。</b>")
    out = tiles + f"<div class='hint' style='margin-top:10px'>{hint}</div>"
    out += "".join(f"<div style='margin-top:12px'>{f}</div>" for f in figs if f)
    return out


# ---- ⑤ 会议→M2 转向历史回放 ----
def _meeting_html(stats: dict, ahead: int = 3) -> str:
    """⑤ 会议锚点会后 M2 同比方向的历史统计——「12月定调→来年放水」叙事的历史对照。"""
    if not stats:
        return "<p class='hint'>会议→M2 回放数据不足(需 M2 同比 ≥ 数年月度序列)</p>"
    rows = ""
    for a, s in sorted(stats.items()):
        prob = s["up_prob"]
        pc = _PAL["good"] if prob >= 0.6 else _PAL["critical"] if prob <= 0.4 else _PAL["ink_sec"]
        ld = s["latest_delta"]
        lc = _PAL["good"] if ld > 0 else _PAL["critical"] if ld < 0 else _PAL["ink_sec"]
        rows += (f"<tr><td style='text-align:left;font-weight:600'>{s['label']}</td>"
                 f"<td>{s['n']}</td>"
                 f"<td style='font-weight:700;color:{pc}'>{prob * 100:.0f}%</td>"
                 f"<td>{s['median_delta']:+.1f}pp</td>"
                 f"<td>{s['latest_ym']}</td>"
                 f"<td style='color:{lc}'>{ld:+.1f}pp</td></tr>")
    hint = (f"读法:每个政策锚点月之后 <b>{ahead} 个月</b>,M2 同比相对锚点月**上行了还是下行了**——"
            "上行概率高=该锚点历史上常是「放水起点」叙事的统计对照(如 12 月中央经济工作会议定调→来年信用扩张)。"
            "<b>月度锚点近似</b>(会议实际日期在月中,对月度序列无差);最近一个还没走完 ahead 的锚不进统计(防半程假读);"
            "n≈17-18/锚点,读方向不读精度;<b>历史对照非因果</b>,更非信号——会议效果取决于当时稳增长压力,"
            "同一锚点在不同周期方向可反。温度计非开关,永不喂引擎。")
    return (f"<table><tr><th style='text-align:left'>锚点(典型)</th><th>n</th>"
            f"<th>会后{ahead}月 M2 上行概率</th><th>中位变化</th><th>最近完整锚</th>"
            f"<th>最近变化</th></tr>{rows}</table>"
            f"<div class='hint' style='margin-top:10px'>{hint}</div>")


# ---- ③ 政策日历 ----
def _policy_html(events: list[dict]) -> str:
    if not events:
        return "<p class='hint'>政策日历为空(内部错误:POLICY_RULES 为空)</p>"
    rows = "".join(
        f"<tr><td style='text-align:left;font-weight:600'>{e['name']}</td>"
        f"<td>{e['date']}</td><td>{e['typical']}</td>"
        f"<td style='font-weight:700;color:{_PAL['critical'] if e['days_left'] <= 14 else _PAL['ink_sec']}'>"
        f"{e['days_left']} 天</td><td style='text-align:left'>{e['note']}</td></tr>"
        for e in events)
    hint = ("<b>硬编码典型时点(历史惯例近似,以官方公告为准)</b>:政治局经济会议 4/7/12 月、中央经济工作会议"
            " 12 月中、央行货政报告季度、两会 3-05、LPR 每月 20 日、金融统计数据(M2/社融)每月 10-15 日。"
            "本表<b>只放事实与倒计时,不做观点打分</b>——主流观点的预登记与到期结算归 docs/CLAIMS_LEDGER.md;"
            "「会后 3 个月 M2 方向变化」的历史统计回放属 v2(见执行规划)。"
            "文章叙事的『12 月经济会议定调放水』即中央经济工作会议+政治局 12 月会议。")
    return (f"<table><tr><th style='text-align:left'>事件</th><th>下次(典型)</th><th>典型时点</th>"
            f"<th>距今</th><th style='text-align:left'>关注点</th></tr>{rows}</table>"
            f"<div class='hint' style='margin-top:10px'>{hint}</div>")


# ---- 页面 ----
_CSS = """
:root{--surface:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink-sec:#52514e;--muted:#898781;--grid:#e1e0d9;--hover:#f4f3ef}
[data-theme="dark"]{--surface:#1a1a19;--plane:#0d0d0d;--ink:#ffffff;--ink-sec:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--hover:#262624}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);font-family:system-ui,-apple-system,'Segoe UI',sans-serif;padding:24px;max-width:1200px;margin:0 auto}
.topbar{display:flex;justify-content:space-between;align-items:center;margin:0 0 4px}
h1{font-size:22px;margin:0}
h2{font-size:16px;margin:0 0 10px;color:var(--ink-sec)}
section{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:16px;margin-bottom:16px}
section h2{margin-top:0}
.meta{color:var(--ink-sec);font-size:13px;margin-bottom:16px}
.hint{color:var(--muted);font-size:12.5px;line-height:1.7}
.tiles-row{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0}
.tile{flex:1;min-width:150px;background:var(--plane);border:1px solid var(--grid);border-radius:8px;padding:10px 12px}
.tile-label{color:var(--muted);font-size:12px}
.tile-value{font-weight:700;font-size:20px}
.tile-sub{color:var(--ink-sec);font-size:11.5px;margin-top:2px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 10px;border-bottom:1px solid var(--grid);text-align:right}
th{color:var(--muted);font-weight:600;text-align:left}
"""

_JS = """
function _applyPlotly(dark){
  document.querySelectorAll('.plotly-graph-div').forEach(function(gd){
    if(!gd.data || !window.Plotly){return;}
    var bg = dark?'#1a1a19':'#fcfcfb', fg = dark?'#ffffff':'#0b0b0b',
        gr = dark?'#2c2c2a':'#e1e0d9';
    var u = {paper_bgcolor:bg, plot_bgcolor:bg, font:{color:fg}};
    gd.data.forEach(function(tr, i){
      if(tr.xaxis !== 'x2' && tr.yaxis !== 'y2'){return;}
    });
    ['xaxis','yaxis','xaxis2','yaxis2','yaxis3','yaxis4'].forEach(function(ax){
      u[ax] = {gridcolor:gr, zerolinecolor:gr, linecolor:gr, tickfont:{color:fg},
               title:{font:{color:fg}}};
    });
    Plotly.relayout(gd, u);
  });
}
function toggleTheme(){
  var el = document.documentElement;
  var dark = el.getAttribute('data-theme') !== 'dark';
  el.setAttribute('data-theme', dark ? 'dark' : 'light');
  try{ _applyPlotly(dark); }catch(e){}
  var b = document.getElementById('theme-btn');
  if(b){ b.textContent = dark ? '☀️' : '🌙'; }
}
"""


def render_china_macro(store, out_path: Path, asof: str = "") -> Path:
    """渲染国内宏观看板 → data/china_macro.html (只读·不喂引擎)。"""
    from . import nowcast as nc

    mcd = diagnose_money_conditions(store)
    repo = store.get_repo_fix_series()
    shibor = store.get_shibor_series()
    lpr = store.get_lpr_series()
    bond = store.get_cn_bond_series()
    cb = store.get_cb_balance_series()
    tsf = store.get_china_tsf_series()
    concl = store.get_meta("china_money_conclusion", "") or ""
    policy_events = policy.policy_calendar(date.today())
    lgb_detail = store.get_lgb_issue()
    lgb_monthly = nc.lgb_monthly_series(lgb_detail)
    cur_ym = date.today().strftime("%Y-%m")

    figs: list[str] = []
    first = True
    if mcd.get("valid"):
        try:
            figs.append(_money_figure(mcd).to_html(
                full_html=False, include_plotlyjs=first, div_id="cm_money"))
            first = False
        except Exception:  # noqa: BLE001
            pass
    money_html = _money_html(mcd, figs[-1] if figs else "", concl)
    rates_figs = []
    for build, args, div in ((_funding_figure, (repo, shibor), "cm_funding"),
                             (_term_figure, (lpr, bond), "cm_term"),
                             (_omo_figure, (cb,), "cm_omo")):
        try:
            f = build(*args)
            if len(f.data):
                rates_figs.append(f.to_html(full_html=False, include_plotlyjs=first, div_id=div))
                first = False
        except Exception:  # noqa: BLE001
            continue
    rates_html = _rates_html(repo, shibor, lpr, bond, cb, rates_figs)
    nowcast_figs = []
    for build, args, div in ((_lgb_figure, (lgb_monthly, cur_ym), "cm_lgb"),
                             (_tsf_comp_figure, (tsf,), "cm_tsfcomp")):
        try:
            f = build(*args)
            if len(f.data):
                nowcast_figs.append(f.to_html(full_html=False, include_plotlyjs=first, div_id=div))
                first = False
        except Exception:  # noqa: BLE001
            continue
    nowcast_html = _nowcast_html(lgb_monthly, tsf, nowcast_figs, cur_ym)
    anchor_stats = (nc.meeting_anchor_stats(mcd["m2_series"])
                    if mcd.get("valid") and len(mcd.get("m2_series", [])) else {})
    meeting_html = _meeting_html(anchor_stats)
    policy_html = _policy_html(policy_events)

    asof_txt = asof or f"{datetime.now():%Y-%m-%d %H:%M}"
    top_hint = ("第七看板 · 中国本土宏观观测层(与「宏观框架」=海外宏观对称)。"
                "<b>只读旁路:永不喂交易引擎;先行代理未过 event-study 礼遇前一律为观察项,"
                "不出现「预测/信号」措辞</b>——M2 拐点实证已示:公布滞后吃掉几乎全部 edge,"
                "温度计非开关。怎么用由你综合各看板自行决定。")
    html = (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>国内宏观 · 货币与利率观测</title><style>" + _CSS + "</style></head><body>"
        "<div class='topbar'><h1>国内宏观 · 货币与利率观测</h1>"
        "<button id='theme-btn' onclick='toggleTheme()'>🌙</button></div>"
        f"<div class='meta'>生成于 {asof_txt} · 数据截至见各图 · 金十源</div>"
        f"<div class='hint' style='margin-bottom:14px'>{top_hint}</div>"
        "<h2>① 货币信用(M2/M1/社融)</h2><section>" + money_html + "</section>"
        "<h2>② 利率与流动性(Shibor/FDR007/LPR/中债期限结构/OMO)</h2><section>" + rates_html + "</section>"
        "<h2>③ 政策日历(下次时点+倒计时)</h2><section>" + policy_html + "</section>"
        "<h2>④ 社融可观测成分(政府债·企业债 nowcast · 观测非预测)</h2><section>" + nowcast_html + "</section>"
        "<h2>⑤ 会议→M2 转向历史回放(锚点月后 3 个月)</h2><section>" + meeting_html + "</section>"
        "<script>" + _JS + "</script></body></html>")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
