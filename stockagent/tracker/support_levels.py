"""关键支撑位 event-study 原语 — 平台顶突破回测:守住 vs 破位 的前向二阶矩(只读诊断)。

研究问题(2026-08 讨论):支撑位的信息量在二阶矩(波动/回撤的条件分布)不在一阶矩(方向)。
事件 = 平台顶(箱体上沿)有效突破后首次回踩;结局 = 守住 / 破位·收回(假破) / 破位·未收(真破)。
前向指标从「确认日」起算 —— 与决策一致(结局确认后才行动);对比无条件基准日分布。

纯函数、选位全规则化(无手画线):平台=尾部滑窗无前视;结局虽用未来窗口判定,但那是
事件定义(研究分组),不是可交易信号。同 ⑧地量:经验参考非定律,永不喂交易引擎。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# ---- 参数(默认;研究脚本可覆写) ----
PLATFORM_WIN = 40          # 平台滑窗(交易日,尾部窗口无前视)
PLATFORM_RANGE = 0.08      # 窗内 max/min−1 ≤ 8% 视为箱体
PLATFORM_MIN_DAYS = 15     # 平台段至少 15 个平台日(合并后)
PLATFORM_MERGE_GAP = 3     # 平台日允许 ≤3 日断裂仍并同一段
BREAKOUT_EPS = 0.005       # 收盘 > 顶×1.005 = 有效突破
ZONE_EPS = 0.01            # 回踩带 = 顶 ±1%(带状非线状)
MIN_ABOVE = 5              # 突破后须在带上方 ≥5 日,回踩才算「回测」(立即跌回=突破失败,弃)
BREAK_EPS = 0.01           # 收盘 < 顶×0.99 = 破位
RECLAIM_DAYS = 3           # 破位后 3 日内收盘收回带上 = 假破(收回)
RESOLVE_WIN = 15           # 回踩后 15 日内无收盘破位 = 守住(确认日 = touch+15)
COOLDOWN = 20              # 事件(touch 日)间最小间隔,去簇
VOL_HIGH = 1.2             # 放量破位 = 破位日成交量 / MA20 ≥ 1.2
FORWARD = (5, 10, 20, 60)  # 前向窗口(交易日)
MATURE_START = "2000-01-01"  # 成熟市场起点(同 ⑧,排除 90s 幼年期)

# 第二类选位:前低(枢轴低点)回测 —— 覆盖「双底/前低支撑」(如 2026-07 上证 3760~3766 双底带),
# 与平台顶回测(前高/箱体上沿)互补,两类在报告层合并 + 全局去重。
PIVOT_SIDE = 10            # 枢轴低点:两侧各 10 日收盘更高(确认滞后 10 日,事件取确认后)
BOUNCE_MIN = 0.05          # 前低后须先反弹 ≥5% 再回踩(否则=下跌中继,不是支撑测试)

OUTCOMES = ("hold", "break_reclaim", "break_down")
OUTCOME_LABEL = {"hold": "守住", "break_reclaim": "破位·收回", "break_down": "破位·未收"}


def detect_platforms(close: pd.Series, win: int = PLATFORM_WIN,
                     max_range: float = PLATFORM_RANGE,
                     min_days: int = PLATFORM_MIN_DAYS,
                     merge_gap: int = PLATFORM_MERGE_GAP) -> list[dict]:
    """滑动尾部窗口找箱体段(无前视):以 t 结尾的 win 日内 max/min−1 ≤ max_range → t 是平台日,
    level=该窗 max(箱体上沿)。连续平台日(允许 ≤merge_gap 日断裂)合并为段,level=段内每日窗顶中位数。
    返回 [{start, end, level}] 按时间序。"""
    s = close.astype(float)
    if len(s) < win:
        return []
    plat_days: list = []          # [(date, window_top)]
    for i in range(win - 1, len(s)):
        w = s.iloc[i - win + 1: i + 1]
        if float(w.max()) / float(w.min()) - 1.0 <= max_range:
            plat_days.append((s.index[i], float(w.max())))
    if not plat_days:
        return []
    segs, cur = [], [plat_days[0]]
    for prev, nxt in zip(plat_days, plat_days[1:]):
        gap = s.index.get_loc(nxt[0]) - s.index.get_loc(prev[0]) - 1
        if gap <= merge_gap:
            cur.append(nxt)
        else:
            segs.append(cur)
            cur = [nxt]
    segs.append(cur)
    out = []
    for seg in segs:
        if len(seg) < min_days:
            continue
        out.append({"start": str(seg[0][0]), "end": str(seg[-1][0]),
                    "level": float(np.median([d[1] for d in seg]))})
    return out


def _resolve_outcome(s: pd.Series, level: float, touch: int,
                     resolve_win: int = RESOLVE_WIN, break_eps: float = BREAK_EPS,
                     reclaim_days: int = RECLAIM_DAYS,
                     zone_eps: float = ZONE_EPS) -> tuple[str, int, int | None]:
    """回踩(touch 位置)后的结局判定:resolve_win 日内首个收盘 < level×(1−break_eps)=破位;
    破位后 reclaim_days 日内收盘重回 level×(1+zone_eps)=假破(收回),否则真破;无破位=守住。
    返回 (outcome, confirm_pos, break_pos|None);confirm=结局确认位置(hold=touch+resolve_win)。"""
    outcome, confirm, break_date = "hold", touch + resolve_win, None
    for i in range(touch, min(touch + resolve_win + 1, len(s))):
        if float(s.iloc[i]) < level * (1 - break_eps):
            break_date = i
            outcome, confirm = "break_down", i + reclaim_days
            for j in range(i + 1, min(i + reclaim_days + 1, len(s))):
                if float(s.iloc[j]) >= level * (1 + zone_eps):
                    outcome, confirm = "break_reclaim", j
                    break
            break
    return outcome, confirm, break_date


def breakout_retest_events(close: pd.Series, volume: pd.Series | None = None,
                           start: str = MATURE_START,
                           win: int = PLATFORM_WIN, max_range: float = PLATFORM_RANGE,
                           min_days: int = PLATFORM_MIN_DAYS,
                           breakout_eps: float = BREAKOUT_EPS, zone_eps: float = ZONE_EPS,
                           min_above: int = MIN_ABOVE, break_eps: float = BREAK_EPS,
                           reclaim_days: int = RECLAIM_DAYS, resolve_win: int = RESOLVE_WIN,
                           cooldown: int = COOLDOWN) -> list[dict]:
    """平台顶突破→首次回踩事件(每平台至多 1 事件,全局 cooldown 去簇)。

    流程:平台结束 → win 后 250 日内找有效突破(收盘 > 顶×(1+breakout_eps)) → 突破后须
    连续 min_above 日收盘在带上方(否则=突破失败,弃) → 250 日内首次收盘回踩入带(顶±zone)
    = touch → resolve_win 日内:首次收盘 < 顶×(1−break_eps) 为破位;破位后 reclaim_days 日内
    收盘重回带上=break_reclaim,否则=break_down;无破位=hold。
    返回 [{platform_start/end, level, breakout, touch, outcome, confirm, break_date,
    vol_ratio(破位日量/MA20,无破位或无量=NaN)}],含前向数据不足的 pending 事件(供看现状)。"""
    s = close.astype(float).loc[start:]
    v = None
    if volume is not None and len(volume):
        v = volume.astype(float).loc[start:]
        v_ma = v.rolling(20).mean()
    events: list[dict] = []
    last_touch_pos = -10**9
    for plat in detect_platforms(s, win, max_range, min_days):
        lvl = plat["level"]
        p_end = s.index.get_loc(plat["end"])
        # 突破日:平台结束后 250 日内首个收盘 > 顶×(1+eps)
        bo = None
        for i in range(p_end + 1, min(p_end + 251, len(s))):
            if float(s.iloc[i]) > lvl * (1 + breakout_eps):
                bo = i
                break
        if bo is None:
            continue
        # 突破后须连续 min_above 日在带上方(收盘 > 顶×(1+zone));跌破=突破失败弃
        if bo + min_above >= len(s) or any(
                float(s.iloc[bo + k]) <= lvl * (1 + zone_eps) for k in range(1, min_above + 1)):
            continue
        # 首次回踩:之后 250 日内首个收盘 ≤ 顶×(1+zone)
        touch = None
        for i in range(bo + min_above + 1, min(bo + 251, len(s))):
            if float(s.iloc[i]) <= lvl * (1 + zone_eps):
                touch = i
                break
        if touch is None or touch - last_touch_pos < cooldown:
            continue
        outcome, confirm, break_date = _resolve_outcome(s, lvl, touch, resolve_win,
                                                        break_eps, reclaim_days, zone_eps)
        vol_ratio = float("nan")
        if break_date is not None and v is not None:
            vd = s.index[break_date]
            if vd in v.index and not math.isnan(float(v_ma.get(vd, float("nan")))):
                vol_ratio = float(v[vd]) / float(v_ma[vd])
        events.append({
            "platform_start": plat["start"], "platform_end": plat["end"], "level": lvl,
            "breakout": str(s.index[bo]), "touch": str(s.index[touch]),
            "outcome": outcome, "confirm": str(s.index[min(confirm, len(s) - 1)]),
            "break_date": str(s.index[break_date]) if break_date is not None else None,
            "vol_ratio": vol_ratio, "high_vol_break": bool(vol_ratio >= VOL_HIGH),
            "pending": confirm >= len(s) - 1 or len(s) - 1 - max(confirm, touch) < max(FORWARD),
        })
        last_touch_pos = touch
    return events


def pivot_lows(close: pd.Series, side: int = PIVOT_SIDE,
               start: str = MATURE_START) -> list[dict]:
    """枢轴低点:两侧各 side 日收盘均更高的收盘点(确认天然滞后 side 日)。返回 [{date, level}]。"""
    s = close.astype(float).loc[start:]
    v = s.to_numpy()
    out = []
    for i in range(side, len(v) - side):
        w = v[i - side: i + side + 1]
        if v[i] == w.min() and (w > v[i]).sum() == side * 2:   # 严格最低(两侧全部更高),防平台底重复计数
            out.append({"date": str(s.index[i]), "level": float(v[i])})
    return out


def low_retest_events(close: pd.Series, volume: pd.Series | None = None,
                      start: str = MATURE_START, side: int = PIVOT_SIDE,
                      bounce_min: float = BOUNCE_MIN, zone_eps: float = ZONE_EPS,
                      break_eps: float = BREAK_EPS, reclaim_days: int = RECLAIM_DAYS,
                      resolve_win: int = RESOLVE_WIN,
                      cooldown: int = COOLDOWN) -> list[dict]:
    """前低(双底)回测事件:枢轴低点确认 → 250 日内反弹至 ≥ 低点×(1+bounce_min) → 之后首次
    收盘回踩入带(低点±zone)= touch → 结局判定同平台顶(_resolve_outcome)。
    每枢轴至多 1 事件,本函数内 cooldown 去簇;字段同 breakout_retest_events + kind='low'。"""
    s = close.astype(float).loc[start:]
    v = None
    if volume is not None and len(volume):
        v = volume.astype(float).loc[start:]
        v_ma = v.rolling(20).mean()
    events: list[dict] = []
    last_touch_pos = -10**9
    for piv in pivot_lows(s, side=side, start=s.index[0]):
        lvl = piv["level"]
        p = s.index.get_loc(piv["date"]) + side          # 确认位置(枢轴右侧 side 日)
        bounce = None
        for i in range(p + 1, min(p + 251, len(s))):
            if float(s.iloc[i]) >= lvl * (1 + bounce_min):
                bounce = i
                break
        if bounce is None:
            continue
        touch = None
        for i in range(bounce + 1, min(bounce + 251, len(s))):
            if float(s.iloc[i]) <= lvl * (1 + zone_eps):
                touch = i
                break
        if touch is None or touch - last_touch_pos < cooldown:
            continue
        outcome, confirm, break_date = _resolve_outcome(s, lvl, touch, resolve_win,
                                                        break_eps, reclaim_days, zone_eps)
        vol_ratio = float("nan")
        if break_date is not None and v is not None:
            vd = s.index[break_date]
            if vd in v.index and not math.isnan(float(v_ma.get(vd, float("nan")))):
                vol_ratio = float(v[vd]) / float(v_ma[vd])
        events.append({
            "kind": "low", "platform_start": piv["date"], "platform_end": piv["date"],
            "level": lvl, "breakout": str(s.index[bounce]), "touch": str(s.index[touch]),
            "outcome": outcome, "confirm": str(s.index[min(confirm, len(s) - 1)]),
            "break_date": str(s.index[break_date]) if break_date is not None else None,
            "vol_ratio": vol_ratio, "high_vol_break": bool(vol_ratio >= VOL_HIGH),
            "pending": confirm >= len(s) - 1 or len(s) - 1 - max(confirm, touch) < max(FORWARD),
        })
        last_touch_pos = touch
    return events


def merged_events(close: pd.Series, volume: pd.Series | None = None,
                  cooldown: int = COOLDOWN, **kw) -> list[dict]:
    """平台顶 + 前低 两类事件合并,按 touch 时间序全局去重(相邻 touch < cooldown 只留先到者)。
    breakout_retest_events 产出的 kind='platform'(在其内部补写)。"""
    plats = breakout_retest_events(close, volume, **kw)
    for e in plats:
        e["kind"] = "platform"
    lows = low_retest_events(close, volume, **{k: v for k, v in kw.items()
                                               if k in ("start", "zone_eps", "break_eps",
                                                        "reclaim_days", "resolve_win", "cooldown")})
    s = close.astype(float)
    all_ev = sorted(plats + lows, key=lambda e: s.index.get_loc(e["touch"]))
    out, last_pos = [], -10**9
    for e in all_ev:
        pos = s.index.get_loc(e["touch"])
        if pos - last_pos < cooldown:
            continue
        out.append(e)
        last_pos = pos
    return out


def forward_risk_rows(close: pd.Series, events: list[dict],
                      forward: tuple = FORWARD) -> list[dict]:
    """每事件从「确认日」起算前向:收益 ret_N / 窗内最大回撤 mdd_N(相对确认日收盘的最低收盘)/
    年化波动 vol_20/vol_60(对数收益 std×√252)。前向数据不足的事件跳过(留给 pending 展示)。"""
    s = close.astype(float)
    maxf = max(forward)
    rows = []
    for ev in events:
        if ev["confirm"] not in s.index:
            continue
        pos = s.index.get_loc(ev["confirm"])
        if pos + maxf >= len(s):
            continue
        base = float(s.iloc[pos])
        seg = s.iloc[pos + 1: pos + 1 + maxf]
        rec = {**ev}
        for n in forward:
            rec[f"ret_{n}"] = float(s.iloc[pos + n]) / base - 1.0
            rec[f"mdd_{n}"] = float(seg.iloc[:n].min()) / base - 1.0
        for n in (20, 60):
            r = np.diff(np.log(s.iloc[pos: pos + n + 1].to_numpy()))
            rec[f"vol_{n}"] = float(np.std(r, ddof=1) * math.sqrt(252)) if len(r) > 2 else float("nan")
        rows.append(rec)
    return rows


def baseline_risk_rows(close: pd.Series, stride: int = 5, n_days: int = 3000,
                       forward: tuple = FORWARD) -> list[dict]:
    """无条件基准:全样本每隔 stride 日取锚点的前向风险(供对比:条件分布 vs 无条件分布)。"""
    s = close.astype(float)
    anchor_pos = list(range(len(s) - max(forward) - 1, max(len(s) - max(forward) - 1 - n_days, 0), -stride))
    fake = [{"confirm": str(s.index[p]), "outcome": "baseline", "touch": str(s.index[p]),
             "level": float("nan"), "pending": False} for p in anchor_pos]
    return forward_risk_rows(s, fake, forward)


def group_summary(rows: list[dict], keys=("vol_20", "vol_60", "mdd_20", "mdd_60",
                                          "ret_5", "ret_20", "ret_60")) -> dict:
    """按 outcome 分组的分布摘要(中位数/四分位/胜率)。刻意同时报告收益胜率:预期~50%(诚实呈现无 edge)。"""
    out: dict[str, dict] = {}
    df = pd.DataFrame(rows)
    if df.empty:
        return out
    for oc, g in df.groupby("outcome"):
        d = {"n": int(len(g))}
        for k in keys:
            if k in g.columns and g[k].notna().any():
                d[f"{k}_med"] = float(g[k].median())
                d[f"{k}_q25"] = float(g[k].quantile(0.25))
                d[f"{k}_q75"] = float(g[k].quantile(0.75))
        for n in (5, 20, 60):
            if f"ret_{n}" in g.columns:
                d[f"win_{n}"] = float((g[f"ret_{n}"] > 0).mean())
        out[oc] = d
    return out


VOL_WINDOW = 20             # 破位确认后波动抬升窗口(event-study:20日实现波动分离✔)


def monitor_snapshot(close: pd.Series, volume: pd.Series | None = None,
                     recent_n: int = 500, max_levels: int = 5, **kw) -> dict:
    """⑩关键位监测快照(指数看板):最近事件位 + 状态机 + 现价距离 + 下/上第一档。

    levels = 最近 recent_n 交易日内回踩的事件位(新→旧,截 max_levels 个),每项带:
    dist(现价/位−1)/in_zone/state。state 按判定窗是否走完(与 pending=前向数据不足区分):
    回踩窗口未完→「回踩测试中·第k日」;破位后收回窗未完→「破位观察中·第k日」;
    已守住 / 假破·已收回 / 已破位·未收(确认后 VOL_WINDOW 日内标「波动窗口内」,对应实证的
    波动抬升期)。next_below=现价下方最近的未破位(支撑),next_above=现价上方最近的已破位
    (翻空为压);均无 → None。纯只读温度计,永不喂引擎。"""
    s = close.astype(float)
    events = merged_events(s, volume, **kw)
    last_pos, last = len(s) - 1, float(s.iloc[-1])
    zone = kw.get("zone_eps", ZONE_EPS)
    levels, below, above = [], [], []
    reclaim_days = kw.get("reclaim_days", RECLAIM_DAYS)
    resolve_win = kw.get("resolve_win", RESOLVE_WIN)
    for e in sorted(events, key=lambda x: s.index.get_loc(x["touch"]), reverse=True):
        pos = s.index.get_loc(e["touch"])
        if last_pos - pos >= recent_n:
            continue
        dist = last / e["level"] - 1.0
        # 结局是否已走完判定窗(pending 只说明前向数据不足,结局可能早已确定)
        if e.get("break_date") and e["break_date"] in s.index:
            bp = s.index.get_loc(e["break_date"])
            if bp + reclaim_days >= last_pos:                 # 破位刚发生,收回窗未走完
                state = f"破位观察中·第{last_pos - bp}日(收回窗未走完)"
            elif e["outcome"] == "break_reclaim":
                state = "假破·已收回"
            else:
                confirm_pos = s.index.get_loc(e["confirm"])
                w = "·波动窗口内" if last_pos - confirm_pos <= VOL_WINDOW else ""
                state = f"已破位·未收{w}"
        elif pos + resolve_win >= last_pos:                    # 回踩窗口未走完,守住未定
            state = f"回踩测试中·第{last_pos - pos}日"
        else:
            state = "已守住"
        rec = {**e, "dist": dist, "in_zone": abs(dist) <= zone, "state": state}
        if len(levels) < max_levels:
            levels.append(rec)
        if e["outcome"] != "break_down" and e["level"] < last * (1 - zone):
            below.append(rec)                      # 未破位且在现价下方 → 支撑
        elif e["outcome"] == "break_down" and e["level"] > last * (1 + zone):
            above.append(rec)                      # 已破位且在现价上方 → 压力
    pick = lambda xs: min(xs, key=lambda r: abs(r["dist"])) if xs else None  # noqa: E731
    return {"date": str(s.index[-1]), "close": last, "n_events": len(events),
            "levels": levels, "next_below": pick(below), "next_above": pick(above)}


def bootstrap_median_diff(a: list[float], b: list[float], n_boot: int = 2000,
                          seed: int = 7) -> dict:
    """两组中位数之差的 bootstrap 90% 区间(无 scipy 依赖;区间不含 0 → 分布分离显著)。"""
    if len(a) < 3 or len(b) < 3:
        return {"diff_med": float("nan"), "lo": float("nan"), "hi": float("nan"),
                "separated": False}
    rng = np.random.default_rng(seed)
    a_, b_ = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    meds = [float(np.median(rng.choice(a_, len(a_))) - np.median(rng.choice(b_, len(b_))))
            for _ in range(n_boot)]
    lo, hi = float(np.quantile(meds, 0.05)), float(np.quantile(meds, 0.95))
    return {"diff_med": float(np.median(a_) - np.median(b_)), "lo": lo, "hi": hi,
            "separated": bool(lo > 0 or hi < 0)}
