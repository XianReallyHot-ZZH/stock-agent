"""货币条件 纯函数核心(只读诊断旁路 · 温度计非开关)。
(曾为指数择时看板 ⑪,2026-08-25 移至国内宏观看板 ①;模块留仓,消费方=china_macro ① 与
 validate_m2_timing,不受该看板 section 移除影响)

主流叙事「M2 定大盘」(增速上行=放水普涨 / 下行=只剩结构行情 / 触底=加仓点)的观测层落地。
事件定义 = 规则式月度状态机(无手画线,只用 trailing 信息 → point-in-time 天然成立——
事件月 T 月末即全部可知;真正的信息滞后是央行公布节奏,由 event_trade_date 显式处理):

  方向口径  sign(M2同比[t] − M2同比[t−MOM_K]),MOM_K=2 月动量——单月锯齿不打断
            (实证:严格环比口径在 18 年真实序列上只出 7/2/1 个事件,太薄;2 月动量出
             9/7/5,且 2025-10 见顶/2024-09 触底/2025 秋季假下行(12月反转打断)全部读对)
  down_confirm  连降 run 达 DOWN_RUN 月 →「下行确认」(水在退)
  bottom_turn   连降 ≥DOWN_RUN 的 run 之后,连升 run 达 TURN_RUN 月 →「触底回升」(加仓触发器)
  top_turn      连升 ≥DOWN_RUN 的 run 之后,连降 run 达 TURN_RUN 月 →「见顶回落」(逃顶触发器)

每 run 至多一次(run_len 逐月 +1,取等值即唯一)。已知局限(诚实):锯齿平台顶(如 2026-02
双顶后 3 月小跌 4 月反抽)会打断 run——事件偏保守,规则机械性强过拟合读法;不设对称
up_confirm 臂(见顶回落已覆盖上行段的结束点,无增量)。

公布滞后双口径(央行金融统计数据 ~次月 9-15 日发布,event-study 用):
  lag0   理想口径  事件月次月首个交易日(假设月末即知——edge 上界)
  lag15  公布口径  事件月次月 PUB_DAY(15) 日(含)后首个交易日(保守可交易口径——headline)

永不喂交易引擎;硬编码常量(贴合 house style,不读 params.yaml)。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

# ---- 模块常量(house style:不读 params.yaml) ----
MOM_K = 2                # 方向口径:MOM_K 个月动量(2=单月锯齿不打断;实证见模块 docstring)
DOWN_RUN = 4             # 连降(升) run 达此长度才算「段」确认
TURN_RUN = 2             # 大段之后的反向 run 达此长度才算「拐点」
PUB_DAY = 15             # 公布口径:次月 15 日(含)后首个交易日(央行 9-15 日发布,取保守端)
M1_BREAK = "2024-01-01"  # M1 新口径断点(纳入个人活期+支付备付金)——图注用;M1 不进研究
MIN_MONTHS = 24          # 状态机最小月数(再短 diagnose 不判 valid)
_EPS = 1e-9

_KIND_LABELS = {"down_confirm": "下行确认", "bottom_turn": "触底回升", "top_turn": "见顶回落"}


def kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, kind)


def _scan(yoy: pd.Series) -> tuple[list[dict], dict]:
    """内部共用扫描:三臂事件列表 + 末态。yoy=月度同比(Series,index='YYYY-MM-01',乱序容忍)。"""
    vals = pd.to_numeric(yoy, errors="coerce").dropna().sort_index()
    idx = [str(m) for m in vals.index]
    v = vals.to_numpy(dtype=float)
    events: list[dict] = []
    run_dir, run_len = 0, 0
    prev_dir, prev_len = 0, 0
    for i in range(MOM_K, len(v)):
        d = 1 if v[i] > v[i - MOM_K] + _EPS else (-1 if v[i] < v[i - MOM_K] - _EPS else 0)
        if d == 0:
            # 动量走平:归档当前 run、清零(prev 链保留——平台顶如 2025-07/08 的 8.8/8.8)
            if run_dir != 0:
                prev_dir, prev_len = run_dir, run_len
            run_dir, run_len = 0, 0
            continue
        if d != run_dir:
            if run_dir != 0:
                prev_dir, prev_len = run_dir, run_len
            run_dir, run_len = d, 0
        run_len += 1
        m = idx[i]
        if run_dir == -1:
            if run_len == DOWN_RUN:
                events.append({"kind": "down_confirm", "month": m, "pos": i,
                               "run": run_len, "yoy": float(v[i])})
            if prev_dir == 1 and prev_len >= DOWN_RUN and run_len == TURN_RUN:
                events.append({"kind": "top_turn", "month": m, "pos": i,
                               "run": run_len, "yoy": float(v[i])})
        elif run_dir == 1:
            if prev_dir == -1 and prev_len >= DOWN_RUN and run_len == TURN_RUN:
                events.append({"kind": "bottom_turn", "month": m, "pos": i,
                               "run": run_len, "yoy": float(v[i])})
    state = {
        "direction": {1: "up", -1: "down", 0: "flat"}.get(run_dir),
        "run": run_len,
        "prev_big": ({1: "up", -1: "down"}.get(prev_dir)
                     if prev_len >= DOWN_RUN else None),
        "last_event": dict(events[-1]) if events else None,
        "months_since_last": (len(idx) - 1 - events[-1]["pos"]) if events else None,
        "months": len(idx),
    }
    return events, state


def m2_episode_events(yoy: pd.Series) -> list[dict]:
    """M2 同比月度序列 → 三臂事件(月升序)。[{kind, month, pos, run, yoy}]。
    纯 trailing 规则 → point-in-time(事件月月末全部可知,公布滞后由 event_trade_date 处理)。"""
    return _scan(yoy)[0]


def episode_state(yoy: pd.Series) -> dict:
    """状态机当前态(看板 tile 用,与 m2_episode_events 同一规则)。"""
    return _scan(yoy)[1]


def state_label(state: dict) -> str:
    """末态 → 人读标签(如「下行确认·连降第6月」/「连降第2月(未满4月确认)」)。
    「连降/连升」为 2 月动量口径(单月锯齿不打断,见 MOM_K),纯展示。"""
    d, k = state.get("direction"), state.get("run") or 0
    pb = state.get("prev_big")
    if d == "flat":
        return "走平(动量零)"
    zh = "连升" if d == "up" else "连降"
    if d == "down":
        if pb == "up" and k >= TURN_RUN:
            return f"见顶回落·{zh}第{k}月"
        if k >= DOWN_RUN:
            return f"下行确认·{zh}第{k}月"
        if pb == "up":
            return f"{zh}第{k}月(见顶回落观察,{TURN_RUN}月触发)"
        return f"{zh}第{k}月(未满{DOWN_RUN}月确认)"
    if pb == "down" and k >= TURN_RUN:
        return f"触底回升·{zh}第{k}月"
    if k >= DOWN_RUN:
        return f"上行确认·{zh}第{k}月"
    if pb == "down":
        return f"{zh}第{k}月(触底观察,{TURN_RUN}月触发)"
    return f"{zh}第{k}月"


def event_trade_date(month: str, daily_index: pd.Index, mode: str = "lag15") -> Optional[str]:
    """事件月 month('YYYY-MM-01') → 双口径可交易日(纯)。
    daily_index=日线日期索引(升序);返回就近向后首个存在交易日(与 pool.study.forward_returns
    同式,这里显式返回供事件表展示)。次月无交易日(数据末端) → None。"""
    y, m = int(month[:4]), int(month[5:7])
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    if mode == "lag0":
        target = f"{ny:04d}-{nm:02d}-01"
    elif mode == "lag15":
        target = f"{ny:04d}-{nm:02d}-{PUB_DAY:02d}"
    else:
        raise ValueError(f"mode must be 'lag0'/'lag15', got {mode!r}")
    after = [d for d in daily_index if str(d) >= target]
    return str(after[0]) if after else None


def scissor_series(m1_yoy: pd.Series, m2_yoy: pd.Series) -> pd.Series:
    """M1−M2 同比剪刀差(%)。2024-01 起 M1 新口径——序列有断点,上层图必须带 M1_BREAK 注记。"""
    a = pd.to_numeric(m1_yoy, errors="coerce")
    b = pd.to_numeric(m2_yoy, errors="coerce")
    return (a - b).dropna()


def tsf_pulse_series(tsf_inc: pd.Series, m2_amt: pd.Series) -> pd.Series:
    """社融脉冲(%) = 社融增量 12 个月滚动和 ÷ 当期 M2 余额 ×100。
    代理口径(诚实):存量同比无免费源;TTM 消 1 月强季节;分母 M2 存量归一。源滞后货币约 2-3 月。
    (批次2.5 起真存量同比见 tsf_stock_yoy_series;本代理保留为其缺列时的回退。)"""
    inc = pd.to_numeric(tsf_inc, errors="coerce").sort_index()
    amt = pd.to_numeric(m2_amt, errors="coerce")
    ttm = inc.rolling(12, min_periods=12).sum()
    return (ttm / amt.reindex(ttm.index) * 100.0).dropna()


def tsf_stock_yoy_series(ts_stock: pd.Series) -> pd.Series:
    """社融存量同比(%) = 存量余额 ÷ 12 个月前存量 − 1(真口径·批次2.5)。
    原料 sf_month.stk_endval(2002-12 起:2002-2014 仅年末值/2015 季度值/2016-01 起月度连续)
    ——按 PeriodIndex 月度重索引后 shift(12):年末对年末恰好相隔 12 月→年度同比有效,
    非 12 月整倍数间隔的点(如季度值)对不上 12 月前→NaN 不硬算;index 'YYYY-MM-01' 升序。
    **口径断点规则**(实证 2017-01:155.99→184.14 万亿=+18% 单月跳升,统计范围切换未回溯
    2016 侧——存量同比 2017 官方约 12%,混口径算出 30%):相邻两月都在且 |MoM|>10% 判断点
    (社融存量真实单月变动仅 ~0.5-3%,10% 不可能是增量),断点后 12 个月内的同比(分母跨
    断点)全部丢弃——规则式无手画线,未来再遇口径切换自动免疫。"""
    s = pd.to_numeric(ts_stock, errors="coerce").dropna()
    if not len(s):
        return pd.Series(dtype=float)
    per = pd.PeriodIndex([str(m)[:7] for m in s.index], freq="M")
    aligned = pd.Series(s.to_numpy(dtype=float), index=per).groupby(level=0).last()
    full = aligned.reindex(pd.period_range(per.min(), per.max(), freq="M"))
    # 断点检测:shift(1) 无 pad(年度稀疏点两侧缺月→NaN 不误触);相邻月都在且 |MoM|>10%
    mom = full / full.shift(1) - 1.0
    is_break = (mom.abs() > 0.10).fillna(False)
    # yoy[t] 的分母 = full[t-12];分母窗口跨断点(t-11..t 任一月为断点)→同比不可比,丢弃
    poisoned = is_break.rolling(12, min_periods=1).max().astype(bool)
    yoy = (full / full.shift(12) - 1.0) * 100.0
    yoy[poisoned] = float("nan")
    out = pd.Series(yoy.to_numpy(dtype=float),
                    index=[f"{p.year:04d}-{p.month:02d}-01" for p in yoy.index])
    return out.dropna()
