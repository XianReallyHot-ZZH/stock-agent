"""event-study 纯核心(验证器共用)——偏离极值三臂 vs 无条件基线。

仓库实证文化(支撑位/地量的教训: 时效可能成立、胜率未必有 edge): 任何「提醒机会」类主张
都要过 event-study 礼遇,结论(含无 edge)写 meta 供看板读图说明注入。

  deviation_events   事件定义: expanding 分位 ≤pct 的超卖段起点(防前视),段间隔冷却去重叠
  forward_returns    事件日前向 N 日收益
  arm_stats          一臂事件的胜率/中位收益
  baseline_stats     无条件基线(全股全时点抽样)
  conclusion_text    诚实结论模板(可能打脸,那正是这套的用处)
"""
from __future__ import annotations

import math

import pandas as pd

from ..research.timing import deviation_series


def deviation_events(close: pd.Series, code: str, pct: float = 0.05,
                     min_above_days: int = 60, cooldown: int = 120) -> list[dict]:
    """单股超卖事件(纯): expanding 分位 ≤pct 的连续段起点,要求此前 ≥min_above_days 根历史
    (分位尺子够长),同股相邻事件 <cooldown 交易日去重叠(一段阴跌磨底只算一次)。
    Returns [{code, date}] 日期升序。"""
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


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def conclusion_text(arms: dict[str, dict], baseline: dict,
                    windows: tuple[int, ...] = (5, 20, 60), focus: int = 20,
                    subject: str = "偏离极值") -> str:
    """诚实结论(纯): 各臂 focus 窗口胜率/中位 vs 基线,分离与否直说,不粉饰。
    subject 前缀由调用方给(偏离极值 / PEAD...),两份 validator 共用本模板。"""
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
