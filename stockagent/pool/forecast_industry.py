"""预告行业选股 event-study 纯核心——行业预喜率过门 → 榜内 yoy Top-N → 持有到法定披露截止。

玩法(scripts/validate_forecast_industry.py 驱动,讨论锁定 2026-08-24): 最早信息=业绩预告
(三环第一环,条件强制 → 预告池天生偏极端是策略定义本身);逐日 as-of 重算行业聚合,预喜率
≥ 阈值且样本 ≥ N 条 → 行业过门;过门日快照 + 其后新到预喜股,榜内 yoy Top-N 次日开盘入场,
持有到法定截止日后首交易日开盘出场(截止日近似——正式报 announce_date 年报期被更正公告
污染不可用,真实披露散布在截止日前,持有期被系统性拉长,方向一致)。

臂(嵌套: strat ⊂ b2 ⊂ b1):
  strat   行业过门 + 榜内 yoy Top-N(主口径含扭亏,忠实「业绩最好」)
  ex_tk   同上但排名池排除扭亏(低基数 +1000% 噪音稳健臂)
  b2      行业过门、不选股(行业层独立增量 → 与 b1 差分)
  b1      全部预喜、无行业门(个股层独立增量 → 与 b2 差分)
入场时点: b1=自身公告日次日; strat/ex_tk/b2=过门日次日(过门日前已公告的股)或自身公告日
次日(其后新到)。同一股在不同臂入场日可能不同 → ret_own/ret_ind 分列。

无前视: as-of 含同日全部公告(按日期块边界判门,块内后排序的同行不漏);行业聚合分母=窗口内
全部预告(含预忧)。纯函数: 输入预告/行业映射/开盘价,无 I/O。
"""
from __future__ import annotations

import math
from bisect import bisect_right
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

BULL_TYPES = ("预增", "略增", "扭亏", "续盈")   # 预喜族(与 tracker/alerts 同口径)
TURNAROUND = "扭亏"


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def period_deadline(period: str) -> str:
    """法定披露截止日: 1231→次年4/30, 0331→当年4/30, 0630→当年8/31, 0930→当年10/31。"""
    y, tail = int(period[:4]), period[4:]
    if tail == "1231":
        return f"{y + 1}-04-30"
    if tail == "0331":
        return f"{y}-04-30"
    if tail == "0630":
        return f"{y}-08-31"
    if tail == "0930":
        return f"{y}-10-31"
    raise ValueError(f"bad report_period {period!r}")


def season_start(period: str) -> str:
    """预告合理窗起点 = 期终 −120 天(年报≈9/2 起,覆盖 Q3 后自愿预告;杀掉东财端点里
    FY2025 预告挂 2024-09-06 这类期前脏日期)。"""
    end = datetime.strptime(period, "%Y%m%d")
    return (end - timedelta(days=120)).strftime("%Y-%m-%d")


def season_filter(fc: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """窗口 [season_start, period_deadline] 外的行剔除。fc 需含
    [code, report_period, announce_date]。Returns (过滤后, 剔除行数)。"""
    rows = []
    for period, g in fc.groupby("report_period"):
        lo, hi = season_start(period), period_deadline(period)
        rows.append(g[(g["announce_date"] >= lo) & (g["announce_date"] <= hi)])
    out = pd.concat(rows) if rows else fc.iloc[0:0]
    return out.sort_values(["report_period", "announce_date", "code"]), len(fc) - len(out)


# ---- 行业回滚: 东财板块树(一股挂多层级) → code→一级根 ----

def _simpler(a: str, b: str) -> str:
    """同尺寸双子合并方向: 无 Ⅱ/Ⅲ 级标者优先(银行 ← 银行Ⅱ),都无则字典序小者。"""
    sa, sb = a.endswith(("Ⅱ", "Ⅲ")), b.endswith(("Ⅱ", "Ⅲ"))
    if sa != sb:
        return b if sa else a
    return min(a, b)


def industry_rollup(members: pd.DataFrame, min_overlap: float = 0.5) -> tuple[dict[str, str], dict[str, str]]:
    """东财板块成员包含关系 → (code→根行业, board→父板)。

    层级无现成表,用包含推断: Y 的父 = 满足 |X∩Y|/|Y| ≥ min_overlap 的更大板(平级同尺寸
    重合 ≥0.9 → _simpler 者为父,修 银行/银行Ⅱ 同尺寸双子);根 = 无父板。东财树缺
    基础化工/有色金属/纺织服饰顶节点 → 其子板块(化学原料/工业金属/服装家纺…)各自为根,
    粒度 ~55 组,对预喜率聚合是可用粒度。code → 其所属板中最小板(最具体分类)的根,
    与 store.industry_map 的「成分数最小者」口径一致。members=[industry, code]。"""
    sets = members.groupby("industry")["code"].apply(set).to_dict()
    names = sorted(sets, key=lambda n: (-len(sets[n]), n))
    parent: dict[str, str] = {}
    for y in names:
        ly = len(sets[y])
        best, best_key = None, None
        for x in names:
            if x == y:
                continue
            lx, r = len(sets[x]), len(sets[x] & sets[y]) / ly
            if lx > ly and r >= min_overlap:
                key = (len(sets[x]), x)          # 更大板优先,平手取名序(确定)
            elif lx == ly and r >= 0.9 and _simpler(x, y) == x:
                key = (0, min(x, y))
            else:
                continue
            if best_key is None or key > best_key:
                best, best_key = x, key
        if best is not None:
            parent[y] = best

    def root(n: str) -> str:
        seen = set()
        while n in parent and n not in seen:
            seen.add(n)
            n = parent[n]
        return n

    board_root = {n: root(n) for n in names}
    code_ind: dict[str, str] = {}
    for code, g in members.groupby("code"):
        smallest = min(g["industry"], key=lambda n: (len(sets[n]), n))
        code_ind[str(code)] = board_root[smallest]
    return code_ind, parent


# ---- 事件构建: 过门 + Top-N(as-of 无前视) ----

def build_trades(fc: pd.DataFrame, industry_of: dict[str, str], *,
                 min_forecasts: int = 5, bull_rate_min: float = 0.70,
                 top_n: int = 5) -> pd.DataFrame:
    """每 (code, period) 一笔交易事件(fc 已过窗口滤;需含
    [code, report_period, yoy, type, announce_date],全部类型——分母要预忧)。

    行业过门: 逐公告日块末判 n ≥ min_forecasts 且预喜率 ≥ bull_rate_min,首次满足日=Q。
    出行 = 全部预喜行,字段:
      trigger_own  自身公告日(b1 臂入场触发)
      trigger_ind  Q(过门日前已公告)或自身公告日(其后新到);行业未过门/未映射 → None
      in_b2/in_strat/in_ex_tk  臂旗(b1 = 全部行恒真)
    Top-N 按 (yoy 降序, code 升序) 确定性破平;排名池 = 触发日 as-of(含同日)已公告预喜股;
    扭亏行 yoy 缺失 → 不入 strat/ex_tk(不能排名),仍入 b1/b2。"""
    rows: list[dict] = []
    fc = fc.copy()
    fc["industry"] = [industry_of.get(str(c)) for c in fc["code"]]
    # 未映射行业(退市/新股不在快照)不得聚成一个伪行业组 → 直接出行、trigger_ind=None
    for _, r in fc[fc["industry"].isna()].iterrows():
        rows.append({"code": str(r["code"]), "period": str(r["report_period"]),
                     "industry": None, "type": str(r["type"]),
                     "yoy": r["yoy"], "announce": str(r["announce_date"]),
                     "trigger_own": str(r["announce_date"]), "trigger_ind": None,
                     "in_b2": False, "in_strat": False, "in_ex_tk": False})
    for (_period, ind), g in fc[fc["industry"].notna()].groupby(
            ["report_period", "industry"], sort=False):
        g = g.sort_values(["announce_date", "code"]).reset_index(drop=True)
        dates = g["announce_date"].to_numpy()
        bull = g["type"].isin(BULL_TYPES).to_numpy()
        cum_n = np.arange(1, len(g) + 1)
        rate = np.cumsum(bull) / cum_n
        # 块末边界: as-of 判门必须含同日全部公告 → 只在日期块末索引上评估
        block_end = np.searchsorted(dates, dates, side="right") - 1
        gate = (cum_n >= min_forecasts) & (rate >= bull_rate_min)
        gate_ends = np.unique(block_end)
        hit = gate_ends[gate[gate_ends]] if len(gate_ends) else np.array([], dtype=int)
        q_idx = int(hit[0]) if len(hit) else None
        q_date = dates[q_idx] if q_idx is not None else None
        yoy = pd.to_numeric(g["yoy"], errors="coerce").to_numpy(float)
        codes = g["code"].astype(str).to_numpy()
        tk = (g["type"] == TURNAROUND).to_numpy()

        def _in_top(i: int, j: int, exclude_tk: bool) -> bool:
            if _nan(yoy[i]):
                return False
            pool = bull[: j + 1].copy()
            if exclude_tk:
                pool &= ~tk[: j + 1]
            if not pool[i]:
                return False
            yv, cv = yoy[: j + 1][pool], codes[: j + 1][pool]
            yi = yoy[i]
            pos = int(((yv > yi).sum() + ((yv == yi) & (cv < codes[i])).sum()))
            return pos < top_n

        for i in range(len(g)):
            if not bull[i]:
                continue
            own = str(dates[i])
            pre_q = q_idx is not None and i <= q_idx
            trig_ind = str(q_date) if pre_q else (own if q_idx is not None else None)
            row = {"code": codes[i], "period": str(g["report_period"].iloc[i]),
                   "industry": ind, "type": str(g["type"].iloc[i]), "yoy": yoy[i],
                   "announce": own, "trigger_own": own, "trigger_ind": trig_ind,
                   "in_b2": trig_ind is not None,
                   "in_strat": False, "in_ex_tk": False}
            if trig_ind is not None:
                j = q_idx if pre_q else int(block_end[i])
                row["in_strat"] = _in_top(i, j, exclude_tk=False)
                row["in_ex_tk"] = _in_top(i, j, exclude_tk=True)
            rows.append(row)
    out = pd.DataFrame(rows)
    return out if len(out) else pd.DataFrame(columns=[
        "code", "period", "industry", "type", "yoy", "announce",
        "trigger_own", "trigger_ind", "in_b2", "in_strat", "in_ex_tk"])


# ---- 交易腿定价(纯,开盘价 dict + 市场日历传入) ----

def _next_td(trading_days: list[str], d: str) -> str | None:
    """d 之后(严格)的首个交易日;d 在末根之后 → None。"""
    k = bisect_right(trading_days, d)
    return trading_days[k] if k < len(trading_days) else None


def trade_returns(trades: pd.DataFrame, opens: dict[str, pd.Series],
                  trading_days: list[str], *, entry_slack: int = 5) -> pd.DataFrame:
    """两条腿各自定价(纯): entry = 触发日后首交易日开盘(停牌顺延 ≤ entry_slack 个市场日内
    首根 bar,再无 → no_price);exit = 法定截止日后首交易日开盘起的该股首根 bar(长停顺延
    复牌首日;数据尾前无 bar → open=未平仓)。entry bar 晚于 exit bar(入场太贴截止+停牌)
    → late 剔除。opens 为前复权 open 序列(indexed by date str,升序)。"""
    td_pos = {d: k for k, d in enumerate(trading_days)}
    deadline_exit: dict[str, str | None] = {}
    out = trades.copy()
    for leg, tcol in (("own", "trigger_own"), ("ind", "trigger_ind")):
        entry_col, exit_col, ret_col, hold_col, st_col = [], [], [], [], []
        for _, r in out.iterrows():
            entry_d = exit_d = ret = hold = None
            status = "na"                       # 无该腿触发(如 trigger_ind=None)
            trigger = r[tcol]
            if r["period"] not in deadline_exit:
                deadline_exit[r["period"]] = _next_td(
                    trading_days, period_deadline(r["period"]))
            nom_exit = deadline_exit[r["period"]]
            s = opens.get(str(r["code"]))
            if trigger is None or (isinstance(trigger, float) and math.isnan(trigger)):
                status = "na"
            elif nom_exit is None:
                status = "open"                 # 截止日已在数据尾之后(期未走完)
            elif s is None or len(s) == 0:
                status = "no_price"
            else:
                e_day = _next_td(trading_days, str(trigger))
                if e_day is None:
                    status = "open"
                else:
                    after = s[s.index >= e_day]
                    if len(after) and td_pos.get(str(after.index[0]), 10**9) \
                            - td_pos.get(e_day, 10**9) <= entry_slack:
                        entry_d = str(after.index[0])
                        x_after = s[s.index >= nom_exit]
                        if len(x_after) == 0:
                            status = "open"     # 截止后无 bar(长停/退市在途)
                        elif str(x_after.index[0]) <= entry_d:
                            status = "late"     # 停牌把入场拖过出场日
                        else:
                            exit_d = str(x_after.index[0])
                            ret = float(x_after.iloc[0]) / float(after.iloc[0]) - 1.0
                            hold = td_pos.get(exit_d, 0) - td_pos.get(entry_d, 0)
                            status = "closed"
                    else:
                        status = "no_price"     # 入场窗内无 bar(停牌/未上市/无数据)
            entry_col.append(entry_d)
            exit_col.append(exit_d)
            ret_col.append(ret)
            hold_col.append(hold)
            st_col.append(status)
        out[f"entry_{leg}"] = entry_col
        out[f"exit_{leg}"] = exit_col
        out[f"ret_{leg}"] = ret_col
        out[f"hold_{leg}"] = hold_col
        out[f"status_{leg}"] = st_col
    return out


def matched_baseline(opens_mat: pd.DataFrame, entry_dates: pd.Series,
                     exit_dates: pd.Series) -> np.ndarray:
    """逐笔同窗市场等权基线(纯): opens_mat = 日期×代码 前复权开盘矩阵(调用方 reindex
    + ffill);每笔 = mean(exit行 ÷ entry行 − 1)(双端有值的列)。日期不在矩阵索引 → NaN。"""
    out = np.full(len(entry_dates), np.nan)
    idx = opens_mat.index
    for k, (e, x) in enumerate(zip(entry_dates, exit_dates)):
        if e not in idx or x not in idx:
            continue
        er, xr = opens_mat.loc[e], opens_mat.loc[x]
        ok = ~(pd.isna(er) | pd.isna(xr)) & (er > 0)
        if ok.any():
            out[k] = float((xr[ok] / er[ok] - 1.0).mean())
    return out


def ret_summary(rets) -> dict:
    """收益分布摘要(纯): {n, win_rate, median, mean};空 → n=0 其余 NaN。"""
    r = [float(x) for x in rets if not _nan(x)]
    if not r:
        return {"n": 0, "win_rate": float("nan"), "median": float("nan"), "mean": float("nan")}
    s = pd.Series(r)
    return {"n": len(r), "win_rate": float((s > 0).mean()),
            "median": float(s.median()), "mean": float(s.mean())}
