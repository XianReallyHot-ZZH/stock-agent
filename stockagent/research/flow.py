"""板块资金流向（份额视角）· 跨 ETF 横截面纯函数。

研究看板「板块资金流向」section 的计算核心：把池内 ETF 的份额净申赎聚合到
行业组（etf_pool.yaml 的 group 字段，9 组），回答两个问题：

  1. 钱是全市场增量进来，还是板块间存量轮动？
     —— 全池净流入 vs 毛额（Σ|各组净流入|）的分解 + 轮动强度 + 定性标签
        （增量普涨/增量聚焦/存量轮动/净赎回/缩量观望；温度计非开关，数字永远展示）。
  2. 各组各自的净流入节奏与热度迁移史？
     —— 组级 W 日滚动净流入时序（亿元）+ 组×月 份额 ROC 热力图。

口径：flow_t = (shares_t − shares_{t−1}) × unit_nav_t / 1e8（Δ份额 × 当日单位
净值，亿元；AUM 口径的真实现金进出，unit_nav 才是可交易的真实每份价值）。
拆分/份额折算事件由 timing.split_adjusted_shares 前复权消掉（份额×unit_nav
反向断崖检测，见该函数 docstring；同包内引用，非跨包耦合）。

方法论注记（与看板读图说明同步，防止误读）：
  - ETF 份额 = 净申赎（配置盘的脚印，比股票"主力资金流"干净——真实现金进出，
    非逐笔成交方向推断）。
  - 份额流入 ≠ 看好：A 股常见越跌越买的逆势申购——必须与偏离度/净值动量交叉看。
  - 「板块间流向」是推断非观测：每只 ETF 的申赎是独立净额，资金来源无标签
    （可能来自存款/卖股票/池外 ETF）；存量约束下的此消彼长是跷跷板的最强证据。
  - 池是精选池非全市场，流出可能去了池外主题 ETF（代表性偏差）。

隔离：纯函数（pandas 进 → DataFrame/dict 出），不 import DB/config；所有阈值
都是函数参数，由 scripts/research_report.py 从 params.yaml research.flow 注入。
不喂交易引擎（research 只读旁路）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockagent.research.timing import split_adjusted_shares

YI = 1e8  # 份 × 元/份 → 亿元


# ---------------------------------------------------------------------------
# 单 ETF 流入原语
# ---------------------------------------------------------------------------

def etf_flow_yi(shares_al: pd.Series, nav_al: pd.Series) -> pd.Series:
    """日净流入（亿元），索引与对齐后份额一致：flow_t = Δshares × nav_t / 1e8。

    shares_al/nav_al 应为 split_adjusted_shares 对齐产物（同一 unit_nav 日历）。
    diff() 使首个有效份额前为 NaN（ETF「出现」不算流入；上市前 reindex 后为 0）。
    """
    if shares_al is None or nav_al is None:
        return pd.Series(dtype=float)
    flow = (shares_al.diff() * nav_al) / YI
    return flow


def flow_panel(series_map: dict, min_obs: int = 2) -> tuple[dict, list]:
    """从 series_map 组装逐 ETF 对齐面板（拆分前复权口径）。

    series_map[sym] = {"shares": Store.get_scale_series df | None,
                       "nav":    Store.get_nav_series df | None,
                       ...}（多余 key 如 "current_shares" 忽略——spot-only 标的
    无历史序列，本来就不该进资金流聚合）。

    Returns (panel, excluded):
      panel[sym]  = {"shares": 前复权份额(unit_nav 日历, ffill),
                     "nav":    对齐 unit_nav,
                     "flow":   etf_flow_yi(...),
                     "splits": 拆分事件列表}
      excluded    = [sym, ...] 份额/净值不可用或有效份额观测 < min_obs 的标的
                    （进 panel 的条件按数据判，不按名单硬编码）。
    中途上市 ETF 正常纳入：上市前 flow=0（真实含义，非缺数）。
    """
    panel: dict = {}
    excluded: list = []
    for sym, m in (series_map or {}).items():
        shares_df = (m or {}).get("shares")
        nav_df = (m or {}).get("nav")
        # min_obs 按**原始**份额观测数判（对齐 ffill 会把 1 行放大成整条日历）
        if shares_df is None or "shares" not in shares_df.columns:
            excluded.append(sym)
            continue
        raw_obs = len(pd.to_numeric(shares_df["shares"], errors="coerce").dropna())
        if raw_obs < min_obs:
            excluded.append(sym)
            continue
        adj, events = split_adjusted_shares(shares_df, nav_df)
        if adj is None or adj.dropna().empty:
            excluded.append(sym)
            continue
        nav_al = pd.to_numeric(nav_df["unit_nav"], errors="coerce").dropna()
        nav_al = nav_al.reindex(adj.index)
        panel[sym] = {"shares": adj, "nav": nav_al, "flow": etf_flow_yi(adj, nav_al),
                      "splits": events}
    return panel, excluded


# ---------------------------------------------------------------------------
# 组级聚合
# ---------------------------------------------------------------------------

def group_rolling_flow(panel: dict, groups: dict[str, list[str]],
                       window: int = 20) -> pd.DataFrame:
    """组级 W 日滚动净流入（亿元）。groups: {组名: [sym,...]}（插入序=列序）。

    每组 = Σ成员 flow，统一对齐到**全池**并集日历、缺数补 0（上市前/首观测/
    个别成员净值晚一天 → 0 流入是真实含义），再 rolling(window, min_periods=window).sum()。
    必须对齐全池而非组内日历：单成员组的净值日历若短于全池（如 159941 净值晚一天），
    组内对齐会让该组在 DataFrame 拼接时尾部变 NaN。
    返回 DataFrame：index=日期str 升序、columns=组名（groups 插入序）、值=亿元。
    """
    if not panel:
        return pd.DataFrame()
    pool_idx = sorted(set().union(*(panel[s]["flow"].index for s in panel)))
    if not pool_idx:
        return pd.DataFrame()
    frames = {}
    for g, members in groups.items():
        members_in = [s for s in members if s in panel]
        if not members_in:
            continue
        total = pd.Series(0.0, index=pool_idx)
        for s in members_in:
            total = total.add(panel[s]["flow"].reindex(pool_idx).fillna(0.0), fill_value=0.0)
        frames[g] = total.rolling(window, min_periods=window).sum()
    if not frames:
        return pd.DataFrame()
    return pd.DataFrame(frames).sort_index()


def group_aum_yi(panel: dict, groups: dict[str, list[str]]) -> dict[str, float]:
    """各组最新规模（亿元）= Σ成员 末份份额 × 末 unit_nav / 1e8。"""
    out: dict[str, float] = {}
    for g, members in groups.items():
        tot = 0.0
        for s in members:
            if s not in panel:
                continue
            sh = panel[s]["shares"].dropna()
            nv = panel[s]["nav"].reindex(sh.index).dropna()
            if sh.empty or nv.empty:
                continue
            tot += float(sh.iloc[-1]) * float(nv.iloc[-1]) / YI
        out[g] = tot
    return out


def group_aum_series(panel: dict, groups: dict[str, list[str]]) -> pd.DataFrame:
    """各组**逐日真实规模**（亿元）= Σ成员 当日真实份额 × 当日 unit_nav / 1e8。

    panel 的份额是拆分前复权口径（末段=真实、历史被乘过拆分乘数）——用 splits
    事件回退：raw_t = adj_t ÷ Π(拆分日 > t 的 ratio)，否则历史 AUM 被高估
    2~4 倍、% 分母失真。对齐全池并集日历（与 group_rolling_flow 同一日历），
    成员缺数（未上市/日历外）计 0。
    作 % 态的逐日分母：pct_t = 净流入_t ÷ AUM_t × 100——分母随时间变化，
    单组曲线形状与亿态不同（规模小的时期同额流入占比更大）。
    """
    if not panel:
        return pd.DataFrame()
    pool_idx = sorted(set().union(*(panel[s]["shares"].index for s in panel)))
    if not pool_idx:
        return pd.DataFrame()
    frames = {}
    for g, members in groups.items():
        tot = pd.Series(0.0, index=pool_idx)
        for s in members:
            if s not in panel:
                continue
            sh = panel[s]["shares"].reindex(pool_idx).astype(float).copy()
            for ev in panel[s].get("splits") or []:      # 前复权 → 真实份额
                d = ev["date"]
                if d in sh.index:
                    sh.loc[sh.index < d] = sh.loc[sh.index < d] / float(ev["ratio"])
            nv = panel[s]["nav"].reindex(pool_idx)
            tot = tot.add((sh * nv).fillna(0.0) / YI, fill_value=0.0)
        frames[g] = tot
    if not frames:
        return pd.DataFrame()
    return pd.DataFrame(frames).sort_index()


# ---------------------------------------------------------------------------
# 增量 vs 存量 分解（tile 数据）
# ---------------------------------------------------------------------------

def pool_flow_state(group_roll: pd.DataFrame, *, window: int | None = None,
                    in_yi: float = 10.0, out_yi: float = -10.0,
                    gross_floor_yi: float = 15.0, breadth_floor_yi: float = 1.0,
                    breadth_min: float = 0.5) -> dict:
    """全池资金状态分解（取 group_roll 末行 = 截止最近交易日的 W 日窗口）。

    指标（温度计，数字永远展示；标签只是辅助阅读）：
      pool_net_yi    = Σ各组净流入 —— 全池口径的增量/撤退
      pool_gross_yi  = Σ|各组净流入| —— 组间双向活跃度（毛额）
      intensity      = |net|/gross ∈ [0,1] —— 0=纯对冲（存量轮动），1=全同向
      breadth        = 净流入 ≥ breadth_floor_yi 的组占比 —— 区分「普涨」vs「独大」
                       （净/毛比对这两种状态都 ≈1，必须靠广度分开）
      concentration  = max|组流|/gross —— 最大单组贡献占比（诊断量，不参与判定）

    标签判定树（顺序求值，完备）：
      net ≤ out_yi                     → 净赎回 (net_out)
      net ≥ in_yi  且 breadth ≥ min    → 增量普涨 (broad_in)
      net ≥ in_yi  且 breadth < min    → 增量聚焦 (focused_in)
      |net| < in_yi 且 gross ≥ floor   → 存量轮动 (rotation)
      其余（毛额不足，双向都不活跃）     → 缩量观望 (quiet)
      无有效数据                        → 数据不足 (insufficient)
    """
    if group_roll is None or len(group_roll) == 0 or group_roll.dropna(how="all").empty:
        return {"label": "数据不足", "label_key": "insufficient", "window": window,
                "pool_net_yi": np.nan, "pool_gross_yi": np.nan, "intensity": np.nan,
                "breadth": np.nan, "concentration": np.nan, "group_flows": [],
                "n_groups": 0}
    last = group_roll.iloc[-1].dropna()
    if last.empty:
        return {"label": "数据不足", "label_key": "insufficient", "window": window,
                "pool_net_yi": np.nan, "pool_gross_yi": np.nan, "intensity": np.nan,
                "breadth": np.nan, "concentration": np.nan, "group_flows": [],
                "n_groups": 0}
    net = float(last.sum())
    gross = float(last.abs().sum())
    intensity = abs(net) / gross if gross > 0 else np.nan
    n_groups = len(last)
    breadth = float((last >= breadth_floor_yi).sum()) / n_groups if n_groups else np.nan
    concentration = float(last.abs().max()) / gross if gross > 0 else np.nan
    if net <= out_yi:
        key = "net_out"
    elif net >= in_yi and breadth >= breadth_min:
        key = "broad_in"
    elif net >= in_yi:
        key = "focused_in"
    elif gross >= gross_floor_yi:
        key = "rotation"
    else:
        key = "quiet"
    labels = {"net_out": "净赎回", "broad_in": "增量普涨", "focused_in": "增量聚焦",
              "rotation": "存量轮动", "quiet": "缩量观望"}
    return {
        "label": labels[key], "label_key": key,
        "window": window,
        "pool_net_yi": net, "pool_gross_yi": gross,
        "intensity": intensity, "breadth": breadth, "concentration": concentration,
        "group_flows": [{"group": g, "flow_yi": float(v)} for g, v in last.items()],
        "n_groups": n_groups,
    }


# ---------------------------------------------------------------------------
# 组×月 热力图矩阵
# ---------------------------------------------------------------------------

def group_monthly_matrix(panel: dict, groups: dict[str, list[str]],
                         start_month: str = "2021-01",
                         end_month: str | None = None) -> pd.DataFrame:
    """组×月 份额净申赎 ROC（小数）。

    （2026-08 休眠：看板月度热力图已移除——ROC 方差与组规模成反比，共享色标被
    小组高波动吃满、大组有意义的变化显色苍白；长历史月度视角由线图 % 态+「全部」
    覆盖。纯函数+测试保留备用。）

    行=组名（groups 插入序）、列='YYYY-MM' 升序。
    每组每月：roc = Σ_{valid} 月末份额 / Σ_{valid} 上月末份额 − 1
    （月末 vs 上月末：落在月初第一个交易日的申赎归入当月——「月初 vs 月末」
    口径会把它整笔丢掉）。首月无上月末 → 用并集日历首日做基点。
    端点取并集 NAV 日历在该月的最后一个交易日；valid = 成员在两端都非 NaN
    （月中上市 → 当月剔除，分子分母同剔，不造"上市即巨幅流入"假象；
    ffill 对齐下上月末值 = 天然衔接值）。全组无 valid 成员 → NaN（空白=无数据）。
    末月为「月内至今」。
    """
    if not panel:
        return pd.DataFrame()
    cal = sorted(set().union(*(panel[s]["shares"].index for s in panel)))
    if not cal:
        return pd.DataFrame()
    cal_s = pd.Series(pd.to_datetime(cal), index=cal)
    first_month = pd.Period(start_month, freq="M")
    last_month = pd.Period(end_month, freq="M") if end_month else cal_s.iloc[-1].to_period("M")
    months = pd.period_range(first_month, last_month, freq="M")

    # 每月端点：月末 = 该月并集日历最后一天；基点 = 上月末（首月 = 日历首日）
    ends: dict[str, str] = {}
    base: dict[str, str | None] = {}
    prev_end: str | None = None
    for m in months:
        in_m = cal_s[cal_s.dt.to_period("M") == m]
        key = str(m)
        if in_m.empty:
            ends[key] = prev_end or ""      # 无交易日 → 空端点 → NaN 单元
            base[key] = None
            continue
        ends[key] = in_m.index[-1]
        base[key] = prev_end if prev_end is not None else cal[0]
        prev_end = ends[key]

    rows = {}
    for g, members in groups.items():
        row = {}
        for m in months:
            key = str(m)
            d0, d1 = base.get(key), ends.get(key)
            num = den = 0.0
            valid = False
            if d0 and d1 and d0 != d1:
                for s in members:
                    if s not in panel:
                        continue
                    sh = panel[s]["shares"]
                    v0, v1 = sh.get(d0), sh.get(d1)
                    if v0 is None or v1 is None or pd.isna(v0) or pd.isna(v1) or v0 <= 0:
                        continue
                    den += float(v0)
                    num += float(v1)
                    valid = True
            row[key] = (num / den - 1.0) if (valid and den > 0) else np.nan
        rows[g] = row
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame.from_dict(rows, orient="index")[[str(m) for m in months]]


# ---------------------------------------------------------------------------
# 申赎异动事件（单 ETF · 顶部提醒横幅数据）
# ---------------------------------------------------------------------------

def daily_flow_events(panel: dict, *, pctile: float = 0.99, floor_yi: float = 1.0,
                      scan_days: int = 22, min_history: int = 250,
                      side_min_obs: int = 30) -> list[dict]:
    """最近申赎异动事件（近 scan_days 个交易日）：全部命中按日期降序返回，
    截断/展示由 report 层做（条带图全画、文字台账取前 N）。
    多窗口横幅（2026-08）：本函数**一次按最大窗扫描**（scan_days=max(scan_windows)），
    各窗口（1/3/6/12月）只是日期切片——分位 as-of-today 全历史、与窗口无关，
    report 层按 alert_windows_payload 的截止日过滤即可，无需重扫。

    判定（自适应每只 ETF 自身波动性 + 金额地板滤小钱噪声）：
      日增减%（Δ份额/前日份额·拆分前复权口径）≥ 自身**方向**历史 pctile 分位
      且 |当日净申赎额|（亿元）≥ floor_yi。
    分位**按方向各自统计**（2026-08 与排名表「日申赎」列统一口径·同式同源·
    同日数字互证）：申购日在全部申购日里按幅度排名 / 赎回日在全部赎回日里——
    比绝对值口径更忠实于「罕见的大额申购/赎回」：申购端 routinely 有大脉冲的
    ETF 一次常规量级申购不该上榜；空前的赎回也不该被更肥的申购尾巴掩盖。
    某方向样本 < side_min_obs → 该方向退绝对值双向分位（kind="abs" 诚实降级）。
    分位为**全历史**口径（含事件当日自身；纯观察·与偏离度分位同哲学·不防前视）。
    **长窗口语义**：以今天的尺度衡量历史——早前的事件可能被其后更极端的流动
    挤出 99% 分位而从长窗视图消失，非「当时看来异常」的 point-in-time 口径。
    有效观测 < min_history 的 ETF 跳过（历史太短分位不可靠）。

    Returns: [{symbol, date, flow_yi, pct, pctile, pctile_kind('side'/'abs'),
               side('in'/'out')}, ...]"""
    events: list[dict] = []
    for sym, p in panel.items():
        pct = p["shares"].pct_change().dropna()
        if len(pct) < min_history:
            continue
        pos, neg = pct[pct >= 0], pct[pct < 0]
        rank_pos = pos.abs().rank(method="average", pct=True) if len(pos) >= side_min_obs else None
        rank_neg = neg.abs().rank(method="average", pct=True) if len(neg) >= side_min_obs else None
        rank_abs = pct.abs().rank(method="average", pct=True)
        flow = p["flow"]
        for d in pct.index[-scan_days:]:
            f = flow.get(d)
            if f is None or f != f or abs(float(f)) < floor_yi:
                continue
            v = float(pct.loc[d])
            r_side = rank_pos if v >= 0 else rank_neg
            if r_side is not None:
                ptd, kind = float(r_side.loc[d]), "side"
            else:
                ptd, kind = float(rank_abs.loc[d]), "abs"
            if ptd >= pctile:
                events.append({"symbol": sym, "date": d, "flow_yi": float(f),
                               "pct": v, "pctile": ptd, "pctile_kind": kind,
                               "side": "in" if v > 0 else "out"})
    events.sort(key=lambda e: (e["date"], abs(e["flow_yi"])), reverse=True)
    return events


# 窗口交易日数 → 横幅按钮标签（未命中映射的窗口退「N日」）
ALERT_WINDOW_LABELS = {22: "1月", 66: "3月", 132: "6月", 250: "1年"}


def alert_window_label(days: int) -> str:
    return ALERT_WINDOW_LABELS.get(int(days), f"{days}日")


def alert_windows_payload(calendar, windows, default_days: int) -> list[dict]:
    """各扫描窗口的元数据（days/label/cutoff/default），供横幅窗口切换。

    cutoff=交易日历倒数第 days 个交易日（ISO 日期串）——窗口成员资格按
    交易日切（22 交易日≈1 个自然月），非日历日近似。日历短于窗口 → 取首日
    （窗口覆盖全部历史，诚实全量）。空日历返回空列表。
    """
    cal_list = list(calendar)
    cal = pd.DatetimeIndex(pd.to_datetime(cal_list)) if cal_list else None
    if cal is None or not len(cal):
        return []
    out = []
    for w in sorted({int(x) for x in windows}):
        cutoff = cal[-w] if len(cal) >= w else cal[0]
        out.append({"days": w, "label": alert_window_label(w),
                    "cutoff": str(cutoff.date()), "default": w == int(default_days)})
    if not any(o["default"] for o in out) and out:   # default 不在集合 → 最大窗兜底
        out[-1]["default"] = True
    return out
