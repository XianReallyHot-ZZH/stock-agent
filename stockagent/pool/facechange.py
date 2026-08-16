"""业绩变脸检测(纯)——陈老师《中概股性价比》方法论的系统化。

三类向下 + 一类向上(np_yoy 口径,**同尾比较**防累计失真: Q2 含 Q1、不同长度报告期不硬比):
  gear_down   增速跳档(小米型): 最新同尾 − 上年同期 ≤ −gear_down_pp 且上年同期 ≥ 0
  trend_break 趋势破位(腾讯型): 同尾序列连续 ≥3 步下行且累计降幅 ≥ trend_break_pp
  consec_neg  连亏/连续负增长(美团型): 逐报告期(全尾部)连续 np_yoy<0 ≥ consec_neg_min 期
  turn_up     拐头向上(埋伏正因子): 同尾序列 ≥2 步下行后最新一步回升 ≥ turn_up_pp
direction: 'up'(turn_up 优先) / 'down'(任一向下检测器) / None(无信号或数据不足)。
down → 策略1 护栏第③闸;up → 变脸表正向列 + 策略2 成长猛 accel 腿的语义邻居。
"""
from __future__ import annotations

import math

import pandas as pd


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _same_tail_series(df: pd.DataFrame, tail: str) -> pd.Series:
    """报告期尾缀为 tail 的 np_yoy 序列(按年份升序,index=period)。"""
    periods = [p for p in df.index if str(p)[4:] == tail]
    return df.loc[sorted(periods), "np_yoy"].astype(float)


def facechange(actuals: pd.DataFrame, cfg: dict | None = None) -> dict:
    """检测器(纯)。actuals indexed by report_period(YYYYMMDD str) [np_yoy](近 8 期,升序)。

    Returns {valid, direction, kinds, detail, tail}:
      kinds    命中的检测器列表(gear_down/trend_break/consec_neg/turn_up)
      detail   一句话人话(表格列直接用)
      tail     [(period, np_yoy) × ≤4](展示近 4 报告期序列)
    数据不足(<2 期同尾或全空) → valid=False,direction=None(诚实缺,不误报)。
    """
    cfg = cfg or {}
    gear_pp = float(cfg.get("gear_down_pp", 20.0))
    trend_pp = float(cfg.get("trend_break_pp", 30.0))
    consec_min = int(cfg.get("consec_neg_min", 3))
    turn_pp = float(cfg.get("turn_up_pp", 10.0))

    out = {"valid": False, "direction": None, "kinds": [], "detail": "", "tail": []}
    if actuals is None or len(actuals) == 0 or "np_yoy" not in actuals.columns:
        return out
    df = actuals[["np_yoy"]].dropna()
    if len(df) == 0:
        return out
    out["valid"] = True
    out["tail"] = [(str(p), float(df.loc[p, "np_yoy"])) for p in df.index[-4:]]

    latest = str(df.index.max())
    tail = latest[4:]
    s = _same_tail_series(df, tail)
    prev_year = f"{int(latest[:4]) - 1}{tail}"
    kinds: list[str] = []

    # ① 跳档: 最新同尾 vs 上年同期
    if len(s) >= 2 and prev_year in s.index and not _nan(s.iloc[-1]) and not _nan(s.loc[prev_year]):
        delta = float(s.iloc[-1]) - float(s.loc[prev_year])
        if delta <= -gear_pp and float(s.loc[prev_year]) >= 0:
            kinds.append("gear_down")
            out["detail"] = (f"增速跳档 {float(s.loc[prev_year]):.0f}%→{float(s.iloc[-1]):.0f}%"
                             f"(−{abs(delta):.0f}pp)")

    # ② 趋势破位: 同尾序列最近 ≥3 步连续下行 + 累计降幅 ≥ trend_pp
    if len(s) >= 4:
        diffs = s.diff().dropna()
        if len(diffs) >= 3 and all(d < 0 for d in diffs.tail(3).tolist()):
            cum = float(s.iloc[-3]) - float(s.iloc[-1])
            if cum >= trend_pp:
                kinds.append("trend_break")
                out["detail"] = (f"同尾增速连续下行 {float(s.iloc[-3]):.0f}%→{float(s.iloc[-1]):.0f}%"
                                 f"(3步累计 −{cum:.0f}pp)")

    # ③ 连亏: 逐报告期(全尾部,按期排序)trailing 连续负增长
    seq = df.sort_index()["np_yoy"].tolist()
    n_neg = 0
    for v in reversed(seq):
        if v < 0:
            n_neg += 1
        else:
            break
    if n_neg >= consec_min:
        kinds.append("consec_neg")
        out["detail"] = f"连续 {n_neg} 个报告期净利负增长"

    # ④ 拐头向上: 同尾序列末两步——前一步下行、最新一步回升 ≥ turn_pp(要求此前有 ≥2 步下行背景)
    if len(s) >= 3:
        last2 = float(s.iloc[-1]) - float(s.iloc[-2])
        prior2 = float(s.iloc[-2]) - float(s.iloc[-3])
        if last2 >= turn_pp and prior2 < 0:
            kinds.append("turn_up")
            out["detail"] = (f"同尾增速拐头 {float(s.iloc[-2]):.0f}%→{float(s.iloc[-1]):.0f}%"
                             f"(+{last2:.0f}pp)")

    out["kinds"] = kinds
    if "turn_up" in kinds:
        out["direction"] = "up"
    elif any(k in kinds for k in ("gear_down", "trend_break", "consec_neg")):
        out["direction"] = "down"
    if not kinds:
        out["detail"] = "无变脸信号"
    return out
