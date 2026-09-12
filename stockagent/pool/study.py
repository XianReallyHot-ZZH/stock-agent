"""验证器纯核心(高业绩池 V8)——状态机回放 · 指数对照 · 消融臂(2026-09)。

检验的可证伪 claim(docs/CLAIMS_LEDGER.md 同步登记): 陈老师「季报池 8 年 70% 场合跑赢
所有宽基指数」。回放口径(Q16 锁定):
  逐事件推进(无前视): 每股每环公告日重判地板+估值+风险门,过门=次日开盘入,不过=次日开盘出;
  窗口: 报告期 P 的首个环事件日 → 下一报告期的首个环事件日(滚动覆盖,不重叠不留白);
  对照: 上证综指/上证50/沪深300/中证500/中证1000/创业板指/科创50(index_daily 现成);
  消融臂: full / no_valuation(去估值门) / no_risk(去风险旗)——各门值几个点;
  小样本诚实注记: 8 季×3 环 ≈ 20 个窗口,对「70%」是小样本检验;结论措辞按仓库惯例
  「温度计非开关/样本内观察」。

回放近似(读图/报告注明): ①扣非精筛腿在回放中退归母口径(全市场一致,不引入幸存者偏差的
双口径);②股本=现市值/现价反推(股本缓变);③历史 universe 无 ST 名单(名称逐期漂移不可回放,
只做代码段过滤——轻微偏差如实注记);④日度等权再平衡近似。
"""
from __future__ import annotations

import math
from typing import Callable, Optional

import pandas as pd

from . import gates as gt
from . import valuation as vl

INDEX_BASELINES = ["000001", "000016", "000300", "000905", "000852", "399006", "000688"]
INDEX_LABELS = {"000001": "上证综指", "000016": "上证50", "000300": "沪深300",
                "000905": "中证500", "000852": "中证1000", "399006": "创业板指",
                "000688": "科创50"}


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def collect_events(period_frames: dict[str, dict[str, pd.DataFrame]]) -> list[dict]:
    """全期三环事件流(纯): period_frames = {period: {'forecast': df, 'express': df,
    'actual': df}}(df indexed by code,含 announce_date+环数据)。
    → [{code, date, period, ring, yoy/np_yoy, rev_yoy, type}] 按 date 升序(同日: 预告<快报<正式)。
    健全性闸门: 公告日必须 ≥ 报告期末(预告/快报/正式报都合法地晚于期末发布;预告面板每期
    ~30-40 行上游错标——如 FY2025 预告公告日 2024-09-06,报告期没开始就「预告」了——剔除)。"""
    ring_order = {"forecast": 0, "express": 1, "actual": 2}
    events: list[dict] = []
    for period, frames in period_frames.items():
        period_end = period  # YYYYMMDD;announce 归一 YYYYMMDD 后直接可比
        for ring in ("forecast", "express", "actual"):
            df = frames.get(ring)
            if df is None or len(df) == 0 or "announce_date" not in df.columns:
                continue
            for code, row in df.iterrows():
                ann = str(row.get("announce_date") or "")[:10]
                if not ann or ann < "1990-01-01":
                    continue
                if ann.replace("-", "") < period_end:
                    continue  # 公告日早于报告期末 = 上游错标,剔除
                if not str(code).zfill(6).startswith(("60", "68", "00", "30")):
                    continue  # 代码段过滤(北交所/债/基金误行)
                events.append({
                    "code": str(code), "date": ann, "period": period, "ring": ring,
                    "np_yoy": _num(row.get("np_yoy") if ring != "forecast" else row.get("yoy")),
                    "rev_yoy": _num(row.get("rev_yoy")),
                    "type": row.get("type") if ring == "forecast" else None,
                })
    events.sort(key=lambda e: (e["date"], ring_order[e["ring"]], e["code"]))
    return events


def gate_at_event(ev: dict, cfg: dict, price_getter: Callable[[str], pd.Series],
                  shares: dict[str, Optional[float]], report_np_abs: dict[str, dict[str, float]],
                  balance: dict[tuple[str, str], dict],
                  arm: str = "full",
                  report_eps: dict[tuple[str, str], float] | None = None) -> dict:
    """单事件的全门判定(纯;回放核心)。price_getter(code)→raw close Series(缓存由调用方管);
    shares = {code: 反推股本};report_np_abs = {code: {period: np_abs}};
    balance = {(period, code): zcfz 行};arm = full/no_valuation/no_risk。
    Returns gates.ring_floor 契约 + {valuation_pass, risk_red, peg, pb_pct, entered}。"""
    active = {"ring": ev["ring"], "announce_date": ev["date"],
              "np_yoy": ev["np_yoy"], "rev_yoy": ev["rev_yoy"],
              "yoy": ev["np_yoy"], "type": ev["type"]}
    floor = gt.ring_floor(active, cfg)          # 回放统一归母口径(扣非腿退归母,docstring 近似①)
    out = {**floor, "valuation_pass": True, "risk_red": [], "peg": None, "pb_pct": None}
    if not floor["passed"]:
        return out

    if arm != "no_risk":
        bal = balance.get((ev["period"], ev["code"]))
        if bal and _num(bal.get("cash")) is not None and _num(bal.get("total_assets")) \
                and _num(bal.get("total_assets")) > 0:
            cash_pct = _num(bal.get("cash")) / _num(bal.get("total_assets"))
            dr = _num(bal.get("debt_ratio"))
            rcfg = (cfg.get("risk", {}) or {})
            if (cash_pct is not None and dr is not None
                    and cash_pct >= float(rcfg.get("dual_high_cash_pct", 0.15))
                    and dr >= float(rcfg.get("dual_high_debt_ratio", 0.40))):
                out["risk_red"] = ["存贷双高(代理口径)"]
        if out["risk_red"]:
            return out

    if arm == "no_valuation":
        return out
    # 估值门: 统一 PEG 轨回放(PB 分位轨需逐股行业历史映射+长 bvps 史,回放统一简化为 PEG——
    # 注记在报告;周期股 PE 爆表样本会被此门挡掉,消融臂 no_valuation 对照其代价)
    price = price_getter(ev["code"])
    if price is None or len(price) == 0:
        out["valuation_pass"] = False
        out["valuation_note"] = "无价格"
        return out
    px_row = price[price.index.astype(str) <= ev["date"]]
    if len(px_row) == 0:
        out["valuation_pass"] = False
        out["valuation_note"] = "事件日前无价格"
        return out
    price_at = float(px_row["close"].iloc[-1])   # price_getter 契约=OHLCV DataFrame
    np_abs = report_np_abs.get(ev["code"], {})
    ttm = None
    shares_ev = shares.get(ev["code"])
    known = [rp for rp in np_abs if rp <= ev["period"]]
    if known:
        latest = max(known)
        ttm = vl.ttm_net_profit(np_abs, latest)
        if shares_ev is None and report_eps.get((ev["code"], latest)):
            # 股本兜底: np_abs/eps 反推(spot 被拦/未覆盖时自洽;两列累计口径,比值=总股本)
            eps_v = report_eps[(ev["code"], latest)]
            if eps_v > 0:
                shares_ev = np_abs[latest] / eps_v
    pe = vl.pe_ttm(price_at, shares_ev, ttm)
    peg = vl.peg(pe, floor["np_yoy"]) if floor["np_yoy"] else None
    out["peg"] = peg
    out["valuation_pass"] = peg is not None and peg <= float(cfg.get("peg_max", 1.0))
    if peg is None:
        out["valuation_note"] = "TTM/市值缺"
    return out


def replay_membership(events: list[dict], cfg: dict, price_getter, shares, report_np_abs,
                      balance, arm: str = "full",
                      report_eps: dict[tuple[str, str], float] | None = None) -> list[dict]:
    """状态机回放(纯): 逐事件过门 → 持仓区间 [{code, period, entry_date, exit_date}]。
    入/出都记**事件日**(收益计算层负责找次日开盘价,无前视)。"""
    member_of: dict[str, dict] = {}   # code → 当前持仓记录
    intervals: list[dict] = []
    for ev in events:
        g = gate_at_event(ev, cfg, price_getter, shares, report_np_abs, balance,
                          arm=arm, report_eps=report_eps or {})
        passed = g["passed"] and g["valuation_pass"] and not g["risk_red"]
        if passed:
            if ev["code"] not in member_of:
                member_of[ev["code"]] = {"code": ev["code"], "period": ev["period"],
                                         "entry_date": ev["date"]}
            else:  # 环升级仍在池: 更新锚定期(出场事件以最新环计)
                member_of[ev["code"]]["period"] = ev["period"]
        else:
            if ev["code"] in member_of:
                rec = member_of.pop(ev["code"])
                rec["exit_date"] = ev["date"]
                intervals.append(rec)
    for rec in member_of.values():   # 期末仍持有 → 开区间(到数据末端)
        rec["exit_date"] = None
        intervals.append(rec)
    return intervals


def next_trading_day_open(price: pd.Series, date: str) -> Optional[tuple[str, float]]:
    """事件日之后首个交易日的开盘价(纯;T+1 执行)。price 需含 open 列的 df(index=date str)。
    无后续交易日(数据末端/停牌) → None。"""
    idx = price.index.astype(str)
    future = price[[d > date for d in idx]]
    if len(future) == 0:
        return None
    d0 = future.index[0]
    o = future["open"].iloc[0]
    if o is None or (isinstance(o, float) and math.isnan(o)) or float(o) <= 0:
        return None
    return str(d0), float(o)


def window_returns(intervals: list[dict], price_dfs: dict[str, pd.DataFrame],
                   index_dfs: dict[str, pd.Series], start: str, end: str) -> dict:
    """窗口收益(纯): 池=成员区间收益等权平均;入/出=事件日**次日开盘**(T+1 执行,无前视;
    open 缺失退该日 close)。指数=窗口 close 简单收益(close 口径——指数无 open 偏差问题,
    index_daily 有 open 亦可用,口径已在报告注明)。Returns {pool, {index: ret}, n_members}。"""
    def _leg_ret(code: str, entry_date: str, exit_date: str | None) -> Optional[float]:
        df = price_dfs.get(code)
        if df is None or len(df) == 0:
            return None
        entry = next_trading_day_open(df, entry_date)
        if entry is None:
            return None
        e_date, e_px = entry
        if e_date > end:
            return None
        if exit_date is None or exit_date >= end:
            x_date, x_px = None, None
        else:
            leg = next_trading_day_open(df, exit_date)
            if leg is None:
                x_date, x_px = None, None
            else:
                x_date, x_px = leg
        if x_date is None:  # 持有到窗口末端 → 窗口内最后 close
            idx = df.index.astype(str)
            sub = df[idx <= end]
            if len(sub) == 0 or str(sub.index[-1]) <= e_date:
                return None
            x_px = float(sub["close"].iloc[-1])
        if x_px is None or x_px <= 0 or e_px <= 0:
            return None
        return x_px / e_px - 1.0

    active = [iv for iv in intervals
              if (iv["exit_date"] or "9999-12-31") > start and iv["entry_date"] < end]
    rets = []
    for iv in active:
        r = _leg_ret(iv["code"], max(iv["entry_date"], start),
                     min(iv["exit_date"], end) if iv["exit_date"] else None)
        if r is not None:
            rets.append(r)
    pool_ret = float(sum(rets) / len(rets)) if rets else None

    idx_ret = {}
    for sym, s in index_dfs.items():
        idx = s.index.astype(str)
        sub = s[(idx >= start) & (idx <= end)]
        if len(sub) >= 2:
            idx_ret[sym] = float(sub.iloc[-1] / sub.iloc[0]) - 1.0
    return {"pool": pool_ret, "indices": idx_ret, "n_members": len(active)}


def _median(xs: list[float]):
    """真中位数(纯): 偶数样本取两中值均值。空 → None。"""
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def aggregate(windows: list[dict]) -> dict:
    """聚合指标(纯): {n_windows, win_rate_all, per_index: {sym: {beat_rate, median_excess}},
    median_pool}——win = 当窗口池收益 > 该指数收益(严格)。池收益缺(无成员)的窗口剔除计数并
    透明报告。"""
    n = 0
    beats_all = 0
    per: dict[str, dict] = {}
    pools = []
    for w in windows:
        p = w.get("pool")
        if p is None:
            continue
        n += 1
        pools.append(p)
        all_beat = True
        for sym, r in (w.get("indices") or {}).items():
            d = per.setdefault(sym, {"beats": 0, "n": 0, "excess": []})
            d["n"] += 1
            if p > r:
                d["beats"] += 1
            else:
                all_beat = False
            d["excess"].append(p - r)
        if (w.get("indices") or {}) and all_beat:
            beats_all += 1
    per_out = {sym: {"beat_rate": d["beats"] / d["n"] if d["n"] else None,
                     "median_excess": _median(d["excess"])}
               for sym, d in per.items()}
    return {"n_windows": n, "win_rate_all": (beats_all / n) if n else None,
            "median_pool": _median(pools), "per_index": per_out}


# ---- 通用 event-study 助手(遗留兼容段;validate_m2_timing 复用,与 V8 回放核心无关) ----
def deviation_events(close: pd.Series, code: str, pct: float = 0.05,
                     min_above_days: int = 60, cooldown: int = 120) -> list[dict]:
    """单股超卖事件(纯,旧策略1 遗留): expanding 分位 ≤pct 的连续段起点,要求此前
    ≥min_above_days 根历史(分位尺子够长),同股相邻事件 <cooldown 交易日去重叠。
    Returns [{code, date}] 日期升序。"""
    from ..research.timing import deviation_series
    dev = deviation_series(close, 60).dropna()
    if len(dev) < min_above_days + 21:
        return []
    pct_series = dev.expanding().apply(
        lambda x: (x[:-1] < x[-1]).sum() / max(len(x) - 1, 1), raw=True)
    mask = (pct_series <= pct).to_numpy()
    events: list[dict] = []
    last_pos = -10**9
    n = len(mask)
    for i in range(n):
        if not mask[i]:
            continue
        if i > 0 and mask[i - 1]:
            continue  # 段内非起点
        if i < min_above_days:
            continue  # 分位尺子太短
        if i - last_pos < cooldown:
            continue
        last_pos = i
        events.append({"code": code, "date": str(dev.index[i]), "pos": i})
    return events


def forward_returns(close: pd.Series, event_date: str,
                    windows: tuple[int, ...] = (5, 20, 60)) -> dict | None:
    """事件日前向收益(纯)。close indexed by date;事件日不在序列 → 就近向后找首个存在日。
    Returns {f"ret_{N}": float|None};事件日本身无数据 → None。"""
    if close is None or len(close) == 0:
        return None
    idx = close.index
    pos = idx.get_loc(event_date) if event_date in idx else None
    if pos is None:
        after = [i for i, d in enumerate(idx) if str(d) >= event_date]
        pos = after[0] if after else None
    if pos is None:
        return None
    out: dict = {"pos": int(pos)}
    base = float(close.iloc[pos])
    for n in windows:
        out[f"ret_{n}"] = (float(close.iloc[pos + n]) / base - 1.0
                           if pos + n < len(close) else None)
    return out


def arm_stats(events: list[dict], windows: tuple[int, ...] = (5, 20, 60)) -> dict:
    """一臂统计(纯): {window: {n, win_rate, median_ret}}。events 含 f"ret_{N}" 键。"""
    out: dict = {}
    for n in windows:
        rets = [e[f"ret_{n}"] for e in events
                if e.get(f"ret_{n}") is not None and not _nan(e.get(f"ret_{n}"))]
        out[n] = {
            "n": len(rets),
            "win_rate": (sum(1 for r in rets if r > 0) / len(rets)) if rets else float("nan"),
            "median_ret": float(pd.Series(rets).median()) if rets else float("nan"),
        }
    return out


def baseline_stats(closes: dict[str, pd.Series], windows: tuple[int, ...] = (5, 20, 60),
                   sample_step: int = 10) -> dict:
    """无条件基线(纯): 全股全时点(每 sample_step 根抽 1)的前向收益——「随便哪天买」对照。"""
    pool: list[dict] = []
    for s in closes.values():
        if s is None or len(s) < max(windows) + 2:
            continue
        for pos in range(0, len(s) - max(windows), sample_step):
            rec = {"pos": pos}
            base = float(s.iloc[pos])
            for n in windows:
                rec[f"ret_{n}"] = float(s.iloc[pos + n]) / base - 1.0
            pool.append(rec)
    return arm_stats(pool, windows)


def conclusion_text(arms: dict[str, dict], baseline: dict,
                    windows: tuple[int, ...] = (5, 20, 60), focus: int = 20,
                    subject: str = "偏离极值") -> str:
    """诚实结论(纯): 各臂 focus 窗口胜率/中位 vs 基线,分离与否直说,不粉饰。
    subject 前缀由调用方给(偏离极值 / M2拐点...),多份 validator 共用本模板。"""
    parts: list[str] = []
    for name, st in arms.items():
        s = st.get(focus, {})
        if not s or _nan(s.get("win_rate")):
            parts.append(f"{name}:样本不足")
            continue
        parts.append(f"{name} {s['win_rate'] * 100:.0f}%/中位{s['median_ret'] * 100:+.1f}%"
                     f"(n={s['n']})")
    b = baseline.get(focus, {})
    btxt = (f"基线 {b['win_rate'] * 100:.0f}%/中位{b['median_ret'] * 100:+.1f}%"
            if b and not _nan(b.get("win_rate")) else "基线样本不足")
    best = ""
    try:
        wrs = {k: v[focus]["win_rate"] for k, v in arms.items()
               if v.get(focus) and not _nan(v[focus].get("win_rate"))}
        if wrs:
            top = max(wrs, key=wrs.get)
            gap = (wrs[top] - (b["win_rate"] if not _nan(b.get("win_rate")) else 0.5)) * 100
            if gap >= 8:
                best = (f"→ {top} 臂相对基线胜率差 +{gap:.0f}pp,温和分离;"
                        f"样本内规律,非因果,防过拟合解读")
            elif gap <= -3:
                best = f"→ 最好臂({top})仍不优于基线({gap:+.0f}pp):无 edge,温度计非开关"
            else:
                best = "→ 各臂与基线大体持平:胜率无 edge(时效/分位信息含量有限,温度计非开关)"
    except (KeyError, TypeError, ValueError):
        best = ""
    return f"{subject}→{focus}日前瞻: " + " | ".join(parts) + f" | {btxt} {best}"


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))
