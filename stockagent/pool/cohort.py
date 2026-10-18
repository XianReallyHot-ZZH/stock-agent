"""预告队列研究(v1 · 2026-10)——cohort study 纯核心(grill 定稿 2026-10-17)。

研究问题: 每季预告法定截止**截点**,「当季预告环过门」队列(**含池内续留者的再过门**
——买的是全体最新过门者,不是只买新面孔)在截点后首交易日开盘买入 → 本季正式报法定
截止(非交易日前移)开盘卖出的一个季报周期内,能否跑赢沪深300;规则精选有无增量;
输赢归因。三臂: A=队列全体等权 / B1=排序分前K / B2=B1再剔黄旗·扭亏·未精筛
(MANUAL_REVIEW 可程序化镜像)。v1=在库已结算期,不登记 CLAIMS_LEDGER——判据已批
(≥5/8 期超额>0=方向一致,≤4/8=证伪),v2 跑数前登记(两段式)。

截点口径(Q2 定稿): 年报 1/31、一季 4/15、半年 7/15、三季 10/15。与 research.earnings
._DISCLOSURE_WINDOWS 的差异: 该字典 0331 窗收在 4/30(看板把一季预告窗贴正式报季合并
处理);本研究按 earnings 模块注释的法定截止取 4/15——否则一季队列持有窗(4/15→4/30)
退化为零。差异在报告注记。

聚合口径(Q14): 期数等权为主(每期一票,胜率+平均超额;~2 周窗与 ~3 个月窗同权,短窗
噪声不放大);资金曲线连乘仅展示;不年化。交易口径(Q5/Q13): 买卖同为开盘价(分红
前复权),零成本注记。本模块纯函数(numpy/pandas/标准库),I/O 归 scripts 层。
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

import pandas as pd

# 法定预告披露截止(截点): period_tail → (月, 日);年报(1231)截止在次年
FORECAST_CUTOFFS = {"1231": (1, 31), "0331": (4, 15), "0630": (7, 15), "0930": (10, 15)}


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def forecast_cutoff(period: str) -> date:
    """某报告期预告的法定截止日=截点(纯)。畸形期 → ValueError(与 calendar.formal_deadline
    同风格)。年报(1231)截止在次年 1/31,其余在同年。"""
    if not isinstance(period, str) or len(period) != 8 or not period.isdigit():
        raise ValueError(f"bad report_period {period!r}")
    y, tail = int(period[:4]), period[4:]
    if tail not in FORECAST_CUTOFFS:
        raise ValueError(f"unknown period tail {tail!r}")
    m, d = FORECAST_CUTOFFS[tail]
    return date(y + 1, m, d) if tail == "1231" else date(y, m, d)


def cohort_events(events: list[dict], period: str, cutoff_iso: str) -> list[dict]:
    """某期预告队列事件(纯): ring=预告 ∧ period 匹配 ∧ 公告日 ≤ 法定截点。
    含续留者的再过门(Q3 定稿——不要求 out→in 转换);迟到预告(公告日>截点)不计。
    events = study.collect_events 输出(升序;健全性闸门已剔公告日早于报告期末的错标行)。
    每股每期至多一条(stock_forecast 主键),无需去重。"""
    return [ev for ev in events
            if ev["ring"] == "forecast" and ev["period"] == period
            and ev["date"] <= cutoff_iso]


def leg_return(adj_open: pd.Series, cutoff_iso: str, deadline_iso: str) -> Optional[dict]:
    """单股持有窗收益(纯,Q5/Q13): 买=截点后首个交易日开盘,卖=正式报法定截止日(非交易日
    前移至最后交易日)当日开盘,同季完成。adj_open=分红前复权 open(index=日期 str 升序)。
    窗内无交易日/买卖同日(长期停牌跨窗)/价格缺失或 ≤0 → None(剔除计数,不造数)。"""
    if adj_open is None or len(adj_open) == 0:
        return None
    idx = [str(d) for d in adj_open.index]
    future = [d for d in idx if d > cutoff_iso]
    in_win = [d for d in idx if cutoff_iso < d <= deadline_iso]
    if not future or not in_win:
        return None
    buy_day, sell_day = future[0], in_win[-1]
    if sell_day <= buy_day:
        return None
    b = adj_open.get(buy_day)
    s = adj_open.get(sell_day)
    if b is None or s is None or _nan(float(b)) or _nan(float(s)) \
            or float(b) <= 0 or float(s) <= 0:
        return None
    return {"ret": float(s) / float(b) - 1.0, "buy_date": buy_day, "sell_date": sell_day}


def index_window_return(index_df: pd.DataFrame, cutoff_iso: str, deadline_iso: str,
                        col: str = "open") -> Optional[dict]:
    """基准同窗收益(纯): 与个股持有窗同构——截点后首交易日 → 截止日(非交易日前移)。
    指数优先 open 口径(与个股买卖同为开盘),列缺时由调用方退 close。Returns
    {ret, buy_date, sell_date} or None。"""
    if index_df is None or len(index_df) == 0 or col not in index_df.columns:
        return None
    s = pd.to_numeric(index_df[col], errors="coerce").dropna()
    s = s[s > 0]
    if not len(s):
        return None
    leg = leg_return(s, cutoff_iso, deadline_iso)
    return leg


def adaptive_k(n: int, k_cap: int = 20, frac: float = 0.25) -> int:
    """精选臂容量(Q12 定稿): K = min(k_cap, floor(frac×队列)),至少 1(队列空 → 0)。
    B1/B2 共用同一 K(按队列规模算定,B2 在过滤后的可选中少中选优)。"""
    if n <= 0:
        return 0
    return max(1, min(int(k_cap), int(frac * n)))


def select_arm(members: list[dict], k: int, exclude_flagged: bool = False) -> list[str]:
    """精选臂成员码(纯): 按排序分升序取前 k(分=PEG,缺→排尾;并列按代码稳定)。
    exclude_flagged=True 即 B2: 剔预告地板旗(扭亏)/应收黄旗/未精筛——MANUAL_REVIEW
    可程序化镜像(Q11 定稿);黄旗与未精筛字段由脚本层 point-in-time 算好传入。"""
    if exclude_flagged:
        pool = [m for m in members
                if not (m.get("flags") or m.get("receivable_yellow") or m.get("unrefined"))]
    else:
        pool = list(members)

    def _key(m: dict):
        s = m.get("score")
        return (float("inf") if s is None or _nan(float(s)) else float(s), str(m["code"]))

    pool.sort(key=_key)
    return [str(m["code"]) for m in pool[:max(0, int(k))]]


def arm_return(leg_rets: list[Optional[float]]) -> Optional[float]:
    """臂期收益 = 成员腿收益等权平均(纯;无效腿由调用方剔除后传入;空 → None)。"""
    xs = [float(r) for r in leg_rets if r is not None and not _nan(r)]
    return sum(xs) / len(xs) if xs else None


def aggregate_arm(period_rows: list[dict]) -> dict:
    """期数等权聚合(纯,Q14 定稿)。period_rows 按报告期升序,每行
    {period, ret, index_ret, ...};ret/index_ret 任一缺的期不计数(透明报告,不造数)。
    Returns {n_periods, n_valid, wins, win_rate, mean_excess, median_excess, capital,
    index_capital, valid_periods}。win=期超额>0(严格)。资金曲线=逐期连乘(时间加权,
    仅展示口径——长窗期主导,不年化)。"""
    rows = [r for r in period_rows
            if r.get("ret") is not None and r.get("index_ret") is not None]
    excess = [float(r["ret"]) - float(r["index_ret"]) for r in rows]
    wins = sum(1 for e in excess if e > 0)
    capital = 1.0
    index_capital = 1.0
    for r in rows:
        capital *= 1.0 + float(r["ret"])
        index_capital *= 1.0 + float(r["index_ret"])
    return {
        "n_periods": len(period_rows), "n_valid": len(rows), "wins": wins,
        "win_rate": (wins / len(rows)) if rows else None,
        "mean_excess": (sum(excess) / len(excess)) if excess else None,
        "median_excess": _median(excess),
        "capital": capital - 1.0, "index_capital": index_capital - 1.0,
        "valid_periods": [str(r["period"]) for r in rows],
    }


def attribution(member_rows: list[dict], key: str, top_n: int = 12) -> list[dict]:
    """归因分组(纯,Q8): member_rows=已结算期×全成员明细行(含 excess/ret 与维度字段),
    按 row[key] 分组(缺失/空 → 「缺」),各组 {label, n, wins, win_rate, mean_excess,
    mean_ret},按 n 降序截 top_n。win=个股超额>0(vs 同窗基准)。excess 缺的行不计。"""
    groups: dict[str, dict] = {}
    for r in member_rows:
        if r.get("excess") is None or _nan(float(r["excess"])):
            continue
        raw = r.get(key)
        label = "缺" if raw is None or (isinstance(raw, float) and math.isnan(raw)) \
            or str(raw) == "" else str(raw)
        g = groups.setdefault(label, {"label": label, "n": 0, "wins": 0,
                                      "excess": [], "rets": []})
        g["n"] += 1
        if float(r["excess"]) > 0:
            g["wins"] += 1
        g["excess"].append(float(r["excess"]))
        g["rets"].append(float(r["ret"]) if r.get("ret") is not None else float("nan"))
    out = [{"label": g["label"], "n": g["n"], "wins": g["wins"],
            "win_rate": g["wins"] / g["n"],
            "mean_excess": sum(g["excess"]) / g["n"],
            "mean_ret": (sum(x for x in g["rets"] if not math.isnan(x))
                         / max(1, sum(1 for x in g["rets"] if not math.isnan(x))))}
           for g in groups.values()]
    return sorted(out, key=lambda d: (-d["n"], d["label"]))[:top_n]


def pb_percentile_asof(close: pd.Series, bvps_events: list[tuple], asof: str,
                       min_history: int = 120) -> Optional[float]:
    """周期 PB 自身历史分位·point-in-time(纯,臂C 用): close=raw close Series;bvps_events =
    [(可得日, period, bvps)] **已过滤可得日 ≤ asof**(公告日不可靠→保守法定截止,调用方管);
    价格截 asof → vl.pb_series 阶梯 → vl.pb_percentile。样本 <min_history → None(留白,
    与实盘同纪律——分位不可靠不判)。"""
    from . import valuation as vl
    if close is None or len(close) == 0 or not bvps_events:
        return None
    idx = close.index.astype(str)
    px = close[idx <= asof]
    if len(px) == 0:
        return None
    pb = vl.pb_series(px.astype(float), bvps_events)
    return vl.pb_percentile(pb, min_history)


def rank_two_track(gated: list[dict]) -> list[dict]:
    """两轨合并排序(纯,实屏同法): 已过各自估值门的成员,PEG 轨按 peg 升序 / PB 轨按 pb_pct
    升序,轨内名次百分位 = (名次+1)/轨大小,跨轨合并按该百分位升序(并列按代码稳定)。
    Returns [{**m, 'rank_pct'}]。轨为空 → 除零防护(百分位=1.0,单成员即第一)。"""
    pegs = sorted([m for m in gated if m.get("track") == "peg"],
                  key=lambda m: (float("inf") if m.get("peg") is None else float(m["peg"]),
                                 str(m["code"])))
    pbs = sorted([m for m in gated if m.get("track") == "pb"],
                 key=lambda m: (float("inf") if m.get("pb_pct") is None else float(m["pb_pct"]),
                                str(m["code"])))
    out: list[dict] = []
    for i, m in enumerate(pegs):
        out.append({**m, "rank_pct": (i + 1) / max(1, len(pegs))})
    for i, m in enumerate(pbs):
        out.append({**m, "rank_pct": (i + 1) / max(1, len(pbs))})
    out.sort(key=lambda m: (float(m["rank_pct"]), str(m["code"])))
    return out


def select_smallest_cap(members: list[dict], frac: float = 0.20) -> list[str]:
    """臂D·队内市值最小档(纯,2026-10-18 探索臂): 有截点市值的成员按 cap 升序取前
    max(1, floor(frac×有值数));无市值(股本反推缺)者不入池不猜;并列按代码稳定。
    阈值锚 = 连续池 mktcap_filter_pct=0.80 先例的队内化(剔除最大两成),**非本队列数据挑选**——
    比例扫描(10-50%)只验方向稳健性,不挑最优格。"""
    pool = [(float(m["cap"]), str(m["code"])) for m in members
            if m.get("cap") is not None and not _nan(float(m["cap"]))]
    if not pool:
        return []
    pool.sort(key=lambda t: (t[0], t[1]))
    k = max(1, int(len(pool) * frac))
    return [c for _cap, c in pool[:k]]


def select_pool_mirror(members: list[dict], top_n: int = 100,
                       peg_max: float = 1.0, pb_pct_max: float = 0.30) -> list[str]:
    """臂C·实盘镜面选臂(纯): 估值门(PEG 轨: peg≤peg_max 且非 None;PB 轨: pb_pct≤pb_pct_max
    且非 None——留白=不过门,与实屏「分位不可靠不判」同义) → 两轨合并排序 → 前 top_n。"""
    gated = [m for m in members
             if (m.get("track") == "peg" and m.get("peg") is not None
                 and float(m["peg"]) <= peg_max)
             or (m.get("track") == "pb" and m.get("pb_pct") is not None
                 and float(m["pb_pct"]) <= pb_pct_max)]
    ranked = rank_two_track(gated)
    return [str(m["code"]) for m in ranked[:max(0, int(top_n))]]


GOODWILL_RED_BAND = ">30%(红旗线)"


def wash_sale_ratio(assets_impair: Optional[float], base_income: Optional[float]) -> Optional[float]:
    """扭亏·上年计提占亏损比(纯,2026-10-18 MANUAL_REVIEW 3.1 头号检查项的程序化):
    |资产减值损失| / |上年同期归母净利|,仅上年亏损(<0)时有意义;减值为正(转回)按 0 计。
    任一缺 / 上年盈利 / 分母为 0 → None。口径: 资产减值损失(含商誉减值——洗大澡主武器),
    信用减值损失 2000 档未暴露 → 比值为**保守低估**。>~0.5 即「上年亏损大头是计提」的嫌疑区,
    人工按 3.1 翻年报确认。"""
    if assets_impair is None or base_income is None:
        return None
    a, n = float(assets_impair), float(base_income)
    if _nan(a) or _nan(n) or n >= 0 or abs(n) <= 0:
        return None
    return abs(min(a, 0.0)) / abs(n)


def goodwill_band(goodwill: Optional[float], equity: Optional[float]) -> str:
    """商誉/净资产档(纯,2026-10-17 补拉后入队筛选): <1% / 1-10% / 10-30% / >30%(红旗线) /
    未知(任一缺)。goodwill 传 0.0=「该期资产负债表行项为空≈无商誉」(tushare 全历史专项
    拉取后 NULL 行=0 的读法,调用方决定);None=不可判。equity ≤0/缺 → 未知。"""
    if goodwill is None or equity is None:
        return "未知"
    g, e = float(goodwill), float(equity)
    if _nan(g) or _nan(e) or e <= 0:
        return "未知"
    ratio = g / e
    if ratio < 0.01:
        return "<1%"
    if ratio < 0.10:
        return "1-10%"
    if ratio < 0.30:
        return "10-30%"
    return GOODWILL_RED_BAND


def bucket_labels(values: dict[str, Optional[float]], n_buckets: int = 10) -> dict[str, Optional[int]]:
    """分桶标签(纯,1=最小…n=最大;rank 百分位×n 向上取整): None 不参与分位、标 None。
    用于市值十分位 / PEG 五分位(组内相对,逐期算——跨期市值膨胀不混尺)。"""
    valid = {str(c): float(v) for c, v in values.items() if v is not None and not _nan(v)}
    out: dict[str, Optional[int]] = {str(c): None for c in values}
    if not valid:
        return out
    s = pd.Series(valid)
    ranked = (s.rank(pct=True) * n_buckets).apply(math.ceil).astype(int)
    for c in valid:
        out[c] = int(ranked[c])
    return out
