"""仓位管理看板(第五看板 · 只读诊断旁路)— 估值档 × 用户预案表 对照器。

来源:《股市仓位管理》(重远投资观·陈老师,2024-10)核心思想 = 股市不可预测 → 仓位管理是保险;
主仓吃复合增长 + 超配搏波动。本看板只做「环境侧」的数据产品:
  ① 逐日估值档回放:沪深300 同口径 PE+PB 滚动分位 → 四档(判定语义复用 diagnose.diagnose_valuation,
     阈值 PE_PCT_LOW/HIGH,parity 由测试锁死;原函数只有当前快照,这里补逐日序列)
  ② 用户预案表:config/params.yaml position_plan(档位→权益仓位%区间);看板只对照「现在在哪格」
  ③ 档位统计:历史占比/前向收益/波动 — 仓库实证口径(温度计非开关,无胜率保证)

红线:纯跟踪·不标买卖点·不喂交易引擎;预案表归用户,看板不发明建议。
层级:大资产配置层(权益 vs 现金总比例);权益内的轮动择时归引擎(RegimeFilter),两层不混。
"""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from . import diagnose as dz
from . import support_levels as slv
from .dashboard import _CSS, _JS, _PAL, _chip
from .diagnose import PE_PCT_HIGH, PE_PCT_LOW, VALUATION_INDEX

# 预案表配置键 → 档位 label(与 diagnose.diagnose_valuation 的四档完全对齐)
ZONE_KEYS = {"low": "低位·可激进", "mid": "中位·中性",
             "split": "结构分化·宜观望", "high": "高位·宜保守"}
ZONE_ORDER = [ZONE_KEYS["low"], ZONE_KEYS["mid"], ZONE_KEYS["split"], ZONE_KEYS["high"]]
ZONE_COLORS = {ZONE_KEYS["low"]: _PAL["good"], ZONE_KEYS["mid"]: _PAL["ink_sec"],
               ZONE_KEYS["split"]: _PAL["warning"], ZONE_KEYS["high"]: _PAL["critical"]}
ROLLING_WINDOW_DAYS = 252 * 10   # 10 年滚动分位窗口(与 diagnose_valuation 口径一致)
MIN_PERIODS = 252                # 首 252 交易日分位留白(启动期样本太少不可靠)


# ---- ① 逐日估值档回放 ----

def _rolling_pct(s: pd.Series, lookback_years: int = 10,
                 min_periods: int = MIN_PERIODS) -> pd.Series:
    """逐日滚动分位:当前值在截尾窗口(252×lookback 年,不足用全部可得)中的分位。

    语义精确复刻 diagnose._pct 的 (s<last).sum()/len(s)(连续序列无并列),保证末值 parity;
    非 raw 循环性能不可接受,raw=True 传 ndarray 等价计算。当前值 NaN → NaN(不误报 0)。"""
    if len(s) == 0:
        return pd.Series(dtype=float)
    win = 252 * lookback_years
    return s.rolling(win, min_periods=min_periods).apply(
        lambda w: float((w < w[-1]).mean()) if math.isfinite(w[-1]) else math.nan,
        raw=True)


def classify_zone(pe_pct, pb_pct, low: float = PE_PCT_LOW,
                  high: float = PE_PCT_HIGH) -> str | None:
    """四档判定(语义对齐 diagnose.diagnose_valuation:双低/双高/跨中线/其余)。

    pb_pct 缺失 → PE-only 三档 fallback(同 diagnose);任一关键分位 NaN → None(无档)。"""
    if pe_pct is None or not math.isfinite(float(pe_pct)):
        return None
    pe_p = float(pe_pct)
    if pb_pct is None or not math.isfinite(float(pb_pct)):
        return (ZONE_KEYS["low"] if pe_p < low
                else ZONE_KEYS["high"] if pe_p > high else ZONE_KEYS["mid"])
    pb_p = float(pb_pct)
    if pe_p < low and pb_p < low:
        return ZONE_KEYS["low"]
    if pe_p > high and pb_p > high:
        return ZONE_KEYS["high"]
    if (pe_p > 0.50) != (pb_p > 0.50):    # 跨中线:一偏贵一偏便宜 → 分化
        return ZONE_KEYS["split"]
    return ZONE_KEYS["mid"]


def valuation_zone_series(pe_df: pd.DataFrame, pb_df: pd.DataFrame,
                          lookback_years: int = 10,
                          min_periods: int = MIN_PERIODS) -> pd.DataFrame:
    """逐日估值档回放 → DataFrame[date 索引][pe_ttm, pe_pct, pb, pb_pct, zone]。

    PE/PB 外连接对齐(日期并集);zone 无档时为 ""(首 min_periods 日留白,PB 缺 PE-only)。"""
    out = pd.DataFrame(index=pd.Index(list(pe_df.index)))
    out["pe_ttm"] = (pd.to_numeric(pe_df["pe_ttm"], errors="coerce").to_numpy()
                     if "pe_ttm" in pe_df.columns else np.nan)
    if len(pb_df) and "pb" in pb_df.columns:
        out = out.join(pd.to_numeric(pb_df["pb"], errors="coerce"), how="outer").sort_index()
    else:
        out["pb"] = np.nan
    out["pe_pct"] = _rolling_pct(pd.to_numeric(out["pe_ttm"], errors="coerce"),
                                 lookback_years, min_periods)
    out["pb_pct"] = _rolling_pct(pd.to_numeric(out["pb"], errors="coerce"),
                                 lookback_years, min_periods)
    out["zone"] = [classify_zone(p, b) or "" for p, b in zip(out["pe_pct"], out["pb_pct"])]
    return out


# ---- 档位段/事件(只记事实,不做涨跌复盘) ----

def _zone_runs(zone: pd.Series) -> list[list]:
    """连续段 [start_date, zone, n_days](空档跳过、不断段——留白不算切换)。"""
    runs: list[list] = []
    for date, z in zone.items():
        zs = z if (isinstance(z, str) and z) else ""
        if not zs:
            continue
        if runs and runs[-1][1] == zs:
            runs[-1][2] += 1
        else:
            runs.append([str(date), zs, 1])
    return runs


def zone_snapshot(zone: pd.Series) -> dict:
    """当前档快照:{zone, entered_on, days_in_zone, prev_zone, as_of, valid}。"""
    runs = _zone_runs(zone)
    if not runs:
        return {"valid": False}
    valid_dates = [str(i) for i, z in zone.items() if isinstance(z, str) and z]
    start, cur, n = runs[-1]
    return {"valid": True, "zone": cur, "entered_on": start, "days_in_zone": int(n),
            "prev_zone": runs[-2][1] if len(runs) > 1 else None,
            "as_of": valid_dates[-1] if valid_dates else "—"}


def recent_switches(zone: pd.Series, n: int = 10) -> list[dict]:
    """档位切换事件流水(只记事实):[{date, prev, zone, days, ongoing}] 按时间正序取最近 n 条。

    date=新档首日,days=该段已持续交易日(末段 ongoing=True);不做「切换后涨跌」复盘。"""
    runs = _zone_runs(zone)
    events = [{"date": r[0], "prev": runs[i - 1][1], "zone": r[1], "days": int(r[2]),
               "ongoing": i == len(runs) - 1}
              for i, r in enumerate(runs) if i > 0]
    return events[-n:]


# ---- ③ 档位统计(仓库实证口径:分布描述,非收益承诺) ----

def zone_stats(zone: pd.Series, close: pd.Series,
               fwd_windows: tuple[int, ...] = (252, 756)) -> dict:
    """每档:历史占比 + 前向收益(中位/均值)+ 前向年化波动(中位)。

    前向收益 = close[t+w]/close[t] − 1;前向波动 = t+1..t+w 日收益 std × √252(rolling O(n))。
    末端窗口不足的样本丢弃(dropna 不外推);close reindex 到 zone 日期后缺失的日不计入。"""
    fwd_windows = tuple(sorted(fwd_windows))
    out = {label: {"days": 0, "share": float("nan"),
                   "fwd": {w: {} for w in fwd_windows}} for label in ZONE_ORDER}
    px = pd.to_numeric(pd.Series(close), errors="coerce").reindex(zone.index)
    base = zone.notna() & (zone.astype(str) != "") & px.notna()
    base_n = int(base.sum())
    if base_n == 0:
        return out
    ret = px.pct_change()
    for label in ZONE_ORDER:
        m = base & (zone == label)
        days = int(m.sum())
        out[label]["days"] = days
        out[label]["share"] = days / base_n
        if days == 0:
            continue
        for w in fwd_windows:
            fwd_ret = (px.shift(-w) / px - 1)[m].dropna()
            fwd_vol = (ret.rolling(w).std().shift(-w) * math.sqrt(252))[m].dropna()
            out[label]["fwd"][w] = {
                "n": int(len(fwd_ret)),
                "median": float(fwd_ret.median()) if len(fwd_ret) else float("nan"),
                "mean": float(fwd_ret.mean()) if len(fwd_ret) else float("nan"),
                "vol": float(fwd_vol.median()) if len(fwd_vol) else float("nan"),
            }
    return out


# ---- ② 预案表(config 驱动;看板只对照,不发明建议) ----

def load_position_plan(params: dict) -> dict:
    """解析 params.yaml position_plan 段 → {zones: {档位label: {min,max}}, note}。

    校验失败抛 ValueError(带合法键清单;看板顶红条展示,不静默降级)。"""
    sec = (params or {}).get("position_plan") or {}
    zones_cfg = sec.get("zones") or {}
    got = set(zones_cfg)
    missing = set(ZONE_KEYS) - got
    if missing:
        raise ValueError(f"position_plan.zones 缺少键 {sorted(missing)};"
                         f"合法键 = {sorted(ZONE_KEYS)}"
                         f"(low/mid/split/high → {ZONE_ORDER})")
    unknown = got - set(ZONE_KEYS)
    if unknown:
        raise ValueError(f"position_plan.zones 含未知键 {sorted(unknown)};"
                         f"合法键 = {sorted(ZONE_KEYS)}")
    zones = {}
    for k, label in ZONE_KEYS.items():
        z = zones_cfg.get(k) or {}
        lo_raw, hi_raw = z.get("equity_min"), z.get("equity_max")
        if lo_raw is None or hi_raw is None:
            raise ValueError(f"position_plan.zones.{k}: 缺 equity_min/equity_max 字段")
        lo, hi = float(lo_raw), float(hi_raw)
        if not (0.0 <= lo <= hi <= 1.0):
            raise ValueError(f"position_plan.zones.{k}: 需满足 0 ≤ equity_min ≤ equity_max ≤ 1"
                             f"(当前 {lo}~{hi})")
        zones[label] = {"min": lo, "max": hi}
    return {"zones": zones, "note": str(sec.get("note", "")).strip()}


# ---- 渲染 ----

def _hex_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def _fmt_pct(x, digits: int = 0, sign: bool = False) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    return f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"


def _zone_figure(zone_df: pd.DataFrame) -> go.Figure:
    """PE/PB 滚动分位双线 + 四档色带(y2 隐藏轴填充,4 trace 不随切换次数爆炸)。"""
    fig = go.Figure()
    x = pd.to_datetime(pd.Index(zone_df.index))
    zone_arr = zone_df["zone"].to_numpy()
    for label in ZONE_ORDER:
        band = np.where(zone_arr == label, 85.0, np.nan)
        fig.add_trace(go.Scatter(
            x=x, y=band, yaxis="y2", name=label, legendgroup=label,
            line=dict(width=0), fill="tozeroy",
            fillcolor=_hex_rgba(ZONE_COLORS[label], 0.14),
            hoverinfo="skip", showlegend=True))
    common = dict(x=x, mode="lines", customdata=zone_arr,
                  hovertemplate="%{x|%Y-%m-%d} · 档位 %{customdata}<br>%{y:.0f}%<extra>%{fullData.name}</extra>")
    fig.add_trace(go.Scatter(y=zone_df["pe_pct"] * 100, name="PE 分位",
                             line=dict(color=_PAL["series_1"], width=1.6), **common))
    fig.add_trace(go.Scatter(y=zone_df["pb_pct"] * 100, name="PB 分位",
                             line=dict(color=_PAL["series_2"], width=1.6), **common))
    for y, txt, dash in ((20, "20%", "dash"), (50, "50%", "dot"), (80, "80%", "dash")):
        fig.add_hline(y=y, line=dict(color=_PAL["muted"], dash=dash, width=1),
                      annotation_text=txt, annotation_position="top left",
                      annotation_font=dict(color=_PAL["muted"], size=10))
    fig.update_layout(
        paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
        font=dict(color=_PAL["ink_sec"], size=12),
        margin=dict(l=44, r=16, t=34, b=10), height=430,
        yaxis=dict(title="滚动分位 %", range=[0, 100],
                   gridcolor=_PAL["grid"], zeroline=False),
        yaxis2=dict(overlaying="y", range=[0, 100], visible=False),
        xaxis=dict(gridcolor=_PAL["grid"],
                   rangeselector=dict(
                       bgcolor=_PAL["plane"], activecolor=_PAL["grid"],
                       buttons=[dict(count=1, label="1年", step="year", stepmode="backward"),
                                dict(count=3, label="3年", step="year", stepmode="backward"),
                                dict(step="all", label="全部")]),
                   rangeslider=dict(visible=True, thickness=0.06)),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.04, x=0))
    return fig


def _context_chips(store) -> list[tuple[str, str]]:
    """环境注记 chips(⑨恐贪/⑧地量/⑩关键位)——复用现有 tracker 计算,只取当前值。"""
    chips: list[tuple[str, str]] = []
    try:
        fgd = dz.diagnose_fear_greed(store)
        if fgd.get("valid"):
            label = str(fgd.get("label", "—"))
            color = (_PAL["critical"] if "贪婪" in label
                     else _PAL["series_1"] if "恐惧" in label else _PAL["ink_sec"])
            chips.append((f"⑨恐贪 {fgd.get('score', float('nan')):.0f} {label}", color))
    except Exception:  # noqa: BLE001
        pass
    try:
        tv = dz.diagnose_turnover(store)
        if tv.get("valid"):
            dry = bool(tv.get("is_dry"))
            chips.append((f"⑧地量{'·是' if dry else '·否'} "
                          f"{tv.get('ratio_now', float('nan')):.2f}",
                          _PAL["warning"] if dry else _PAL["ink_sec"]))
    except Exception:  # noqa: BLE001
        pass
    try:
        sse = store.get_index_daily_series("000001")
        if len(sse) >= 500:
            snap = slv.monitor_snapshot(sse["close"], sse.get("volume"))
            lv = (snap.get("levels") or [None])[0]
            if lv:
                chips.append((f"⑩关键位 {lv.get('level', float('nan')):.0f} "
                              f"{lv.get('state', '—')}", _PAL["ink_sec"]))
    except Exception:  # noqa: BLE001
        pass
    return chips


def _tiles_html(snap: dict, plan: dict | None, zone_df: pd.DataFrame,
                chips: list[tuple[str, str]]) -> str:
    last = zone_df.iloc[-1] if len(zone_df) else None
    cur = snap.get("zone") if snap.get("valid") else None
    # 当前档位
    if cur:
        prev = (f" · 上一档 {snap['prev_zone']}" if snap.get("prev_zone") else "")
        tile1 = (f"<div class='tile'><div class='tile-label'>当前档位</div>"
                 f"<div class='tile-value' style='font-size:20px'>"
                 f"{_chip(cur, ZONE_COLORS[cur])}</div>"
                 f"<div class='tile-sub'>进入 {snap['entered_on']} · "
                 f"在档 {snap['days_in_zone']} 个交易日{prev}</div></div>")
    else:
        tile1 = ("<div class='tile'><div class='tile-label'>当前档位</div>"
                 "<div class='tile-value' style='font-size:20px'>—</div>"
                 "<div class='tile-sub'>估值数据不足</div></div>")
    # 预案权益区间(当前档)
    prow = plan["zones"].get(cur) if (plan and cur) else None
    if prow:
        tile2 = (f"<div class='tile'><div class='tile-label'>预案权益仓位(当前档)</div>"
                 f"<div class='tile-value'>{prow['min'] * 100:.0f}% ~ "
                 f"{prow['max'] * 100:.0f}%</div>"
                 f"<div class='tile-sub'>{plan['note'] or 'config/params.yaml position_plan'}</div></div>")
    else:
        tile2 = ("<div class='tile'><div class='tile-label'>预案权益仓位(当前档)</div>"
                 "<div class='tile-value'>—</div>"
                 f"<div class='tile-sub'>{'position_plan 未配置' if plan else '预案表未加载'}</div></div>")
    # 估值分位末值
    if last is not None:
        tile3 = ("<div class='tile'><div class='tile-label'>估值分位(末值)</div>"
                 f"<div class='tile-value' style='font-size:20px'>"
                 f"PE {_fmt_pct(last['pe_pct'])} · PB {_fmt_pct(last['pb_pct'])}</div>"
                 f"<div class='tile-sub'>10 年滚动 · 数据截至 {snap.get('as_of', '—')}</div></div>")
    else:
        tile3 = ("<div class='tile'><div class='tile-label'>估值分位(末值)</div>"
                 "<div class='tile-value' style='font-size:20px'>—</div></div>")
    chips_html = ("<div style='margin-top:10px'>环境注记(不改档位,只作参照): "
                  + " ".join(_chip(t, c) for t, c in chips)
                  + "</div>") if chips else ""
    return f"<div class='tiles-row'>{tile1}{tile2}{tile3}</div>{chips_html}"


def _plan_table_html(plan: dict | None, stats: dict, current_zone: str | None) -> str:
    zones = (plan or {}).get("zones", {})
    head = ("档位", "权益仓位区间(预案)", "历史占比", "前向1年收益中位",
            "前向3年收益中位", "前向1年波动中位(年化)", "样本日")
    rows = []
    for label in ZONE_ORDER:
        p = zones.get(label)
        st = stats.get(label) or {}
        fwd = st.get("fwd") or {}
        f1, f3 = fwd.get(252) or {}, fwd.get(756) or {}
        rng = f"{p['min'] * 100:.0f}% ~ {p['max'] * 100:.0f}%" if p else "—"
        cur = " style='background:var(--hover)'" if label == current_zone else ""
        mark = " ◀ 当前" if label == current_zone else ""
        rows.append(
            f"<tr{cur}><td>{_chip(label, ZONE_COLORS[label])}{mark}</td>"
            f"<td style='font-variant-numeric:tabular-nums'>{rng}</td>"
            f"<td>{_fmt_pct(st.get('share'))}</td>"
            f"<td style='font-variant-numeric:tabular-nums'>{_fmt_pct(f1.get('median'), 1, sign=True)}</td>"
            f"<td style='font-variant-numeric:tabular-nums'>{_fmt_pct(f3.get('median'), 1, sign=True)}</td>"
            f"<td style='font-variant-numeric:tabular-nums'>{_fmt_pct(f1.get('vol'), 1)}</td>"
            f"<td>{st.get('days', 0)}</td></tr>")
    thead = "".join(f"<th>{h}</th>" for h in head)
    return (f"<table class='stat'><thead><tr>{thead}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
            f"<div class='hint' style='margin-top:8px'>预案表 = 你在 config/params.yaml "
            f"<code>position_plan</code> 自定义的档位→权益仓位%区间;看板只对照「现在在哪格」。"
            f"前向收益/波动为历史分布描述(逐日样本、末端不足窗口已丢弃),"
            f"不是收益承诺——档位是温度计,不是开关。</div>")


def _switches_table_html(events: list[dict]) -> str:
    if not events:
        return "<div class='hint'>尚无档位切换记录(或数据不足)。</div>"
    rows = []
    for e in reversed(events):    # 新→旧
        ongoing = " · 进行中" if e.get("ongoing") else ""
        rows.append(
            f"<tr><td>{e['date']}</td>"
            f"<td>{_chip(e['prev'], ZONE_COLORS.get(e['prev'], _PAL['muted']))} → "
            f"{_chip(e['zone'], ZONE_COLORS.get(e['zone'], _PAL['muted']))}</td>"
            f"<td>{e['days']} 个交易日{ongoing}</td></tr>")
    return ("<table class='stat'><thead><tr><th>切换日(新档首日)</th><th>档位变化</th>"
            f"<th>该段持续</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
            "<div class='hint' style='margin-top:8px'>只记事实,不做「切换后涨跌」复盘——"
            "命中与消失均是状态变化,非荐股依据。</div>")


def render_position_report(store, params: dict, output_path,
                           title: str = "仓位管理看板") -> str:
    """组装五块 section,写出离线自包含 HTML(data/position.html)。返回输出路径。"""
    try:
        pe = store.get_index_pe_series(VALUATION_INDEX)
        pb = store.get_index_pb_series(VALUATION_INDEX)
    except Exception:  # noqa: BLE001
        pe, pb = pd.DataFrame(), pd.DataFrame()
    zone_df = valuation_zone_series(pe, pb) if len(pe) else pd.DataFrame(
        columns=["pe_ttm", "pb", "pe_pct", "pb_pct", "zone"])
    has_zone = len(zone_df) > 0 and (zone_df["zone"].astype(str) != "").any()

    try:
        plan = load_position_plan(params)
        plan_err = ""
    except ValueError as e:
        plan, plan_err = None, str(e)

    snap = zone_snapshot(zone_df["zone"]) if len(zone_df) else {"valid": False}
    stats: dict = {}
    if has_zone:
        try:
            close = store.get_index_daily_series("000300")["close"]
            stats = zone_stats(zone_df["zone"], close)
        except Exception:  # noqa: BLE001
            stats = {}
    switches = recent_switches(zone_df["zone"]) if len(zone_df) else []
    chips = _context_chips(store)

    fig_html = ""
    if has_zone:
        try:
            fig_html = _zone_figure(zone_df).to_html(
                full_html=False, include_plotlyjs=True, div_id="pos-fig")
        except Exception:  # noqa: BLE001
            fig_html = ""

    err_banner = (f"<div style='border:1px solid {_PAL['critical']};background:"
                  f"{_hex_rgba(_PAL['critical'], 0.08)};color:{_PAL['critical']};"
                  f"border-radius:8px;padding:10px 14px;margin-bottom:16px;font-size:13px'>"
                  f"⚠️ position_plan 配置错误:{plan_err}</div>") if plan_err else ""

    if has_zone:
        sec1 = _tiles_html(snap, plan, zone_df, chips)
        data_start = str(zone_df.index[0])
        fig_sec = (fig_html or "<div class='hint'>图渲染失败(数据异常)。</div>")
        sec3 = _plan_table_html(plan, stats, snap.get("zone") if snap.get("valid") else None)
        hint2 = ("<div class='hint' style='margin-top:8px'>色带 = 估值档区间(点图例可开关);"
                 "分位=10 年滚动窗口(不足 10 年用可得历史),首 252 交易日留白(启动期不可靠)。"
                 "历史占比/前向收益见下表——分布描述,非收益承诺。</div>")
        notes = [
            "<b>温度计非开关</b>:档位与前向收益统计是历史分布描述,不是收益承诺。本仓 event-study"
            "(⑧地量/⑩关键位)实证口径:时效与波动分离成立、方向胜率无 edge——本看板永不喂交易引擎。",
            f"<b>分位口径</b>:10 年滚动窗口(2520 交易日),历史不足 10 年时用全部可得数据;"
            f"首 252 交易日留白。数据源:index_pe/index_pb(沪深300,2005-04 起)+ index_daily(000300)。",
            "<b>预案表归你</b>:config/params.yaml <code>position_plan</code> 自定义(档位→权益仓位%"
            "区间);看板只回答「现在在哪格、离哪条线多远」,不发明买卖建议。",
            "<b>方法论出处</b>:《股市仓位管理》(重远投资观·陈老师,2024-10)——股市不可预测→仓位"
            "管理是保险;主仓吃复合增长+超配搏波动。其「高确定性/90%回归」等话术按本仓实证口径降权,"
            "仅作思想来源,不作为看板置信度。",
            "<b>层级边界(v1 未含)</b>:大资产配置层(权益 vs 现金);现金腿/股债性价比未做"
            "(中国国债收益率无数据源)、权益内主仓/超配拆分未做(引擎轮动=权益内执行层,两层不混)、"
            "档位切换推送未接。",
        ]
        sec5 = ("<ul style='margin:0;padding-left:18px;font-size:13px;line-height:1.9;"
                "color:var(--ink-sec)'>" + "".join(f"<li>{n}</li>" for n in notes) + "</ul>")
    else:
        sec1 = ("<div class='hint'>估值数据不足:先运行 <code>python scripts/backfill_index.py</code> "
                "回填沪深300 PE/PB 后重新生成。</div>")
        fig_sec = ""
        hint2 = ""
        sec3 = ""
        sec5 = ""

    html = (
        f"<!DOCTYPE html><html lang='zh-CN' data-theme='light'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><style>{_CSS}</style></head><body>"
        f"<div class='topbar'><h1>{title}</h1>"
        f"<button id='theme-btn' onclick='toggleTheme()'>🌙</button></div>"
        f"<div class='meta'>数据截至 {snap.get('as_of', '—')} · 沪深300 估值档 · "
        f"生成于 {datetime.now():%Y-%m-%d %H:%M}</div>"
        f"{err_banner}"
        f"<h2>① 当前档位 × 预案对照</h2><section>{sec1}</section>"
        f"<h2>② 估值档历史回放(沪深300 PE/PB 滚动分位)</h2><section>{hint2}{fig_sec}</section>"
        f"<h2>③ 预案表 × 档位统计</h2><section>{sec3}</section>"
        f"<h2>④ 档位切换事件(只记事实)</h2><section>{_switches_table_html(switches)}</section>"
        f"<h2>⑤ 读图说明(实证口径与边界)</h2><section>{sec5}</section>"
        f"<script>{_JS}</script></body></html>"
    )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)
