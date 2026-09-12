"""三环地板 + 逐股状态机(纯)——高业绩池的入场判定核心(V8, 2026-09)。

方法论(陈老师·重远投资观, docs/stock_pool/MANUAL_REVIEW.md 存档): 每季度程序筛选
「营收+扣非利润高增长」做成股票池;每季正式报/预告/快报都按最新业绩调整;不预测持续性
——增速不行了下季度自然被淘汰,保持组合由高业绩构成。

三环地板(逐环收紧,数据先天差异的诚实处理——非妥协):
  预告环  单腿: 预告净利幅度 ≥floor_np_yoy(预告只披露净利维度,无营收);
          type=扭亏(上年同期为负,幅度%无意义)单独处理=过地板但打「扭亏」黄旗进人工复审。
  快报环  双轴: np_yoy ≥floor 且 rev_yoy ≥floor_rev。
  正式环  双轴扣非: 利润轴优先扣非 yoy(sina 逐股精筛腿,幸存者才有);
          sina 缺 → 回退归母 np_yoy 并打「未精筛」旗(诚实降级,精筛腿拉过即消)。

状态机语义(Q12 锁定): 每只股票在**自己的披露日**(各环公告日)用当时可得数据过门,
过门即入池、下一次披露(下一环或下一期)不过门即出池。看板展示连续状态的当前切面;
本模块只做「某日某股的活跃环 + 地板判定」这一步,进出编排归 screen.py。
"""
from __future__ import annotations

import math
from typing import Optional

# 预告类型地板语义: 亏损族/减速族直接不过;扭亏特殊(幅度无意义);预增/略增按幅度数值判
FORECAST_LOSS_TYPES = ("首亏", "续亏", "增亏", "预减", "略减")


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def deducted_yoy(np_deducted: dict[str, float], period: str) -> Optional[float]:
    """扣非净利同比(%,纯): np_deducted = {report_period(YYYYMMDD): 累计绝对值}(sina 逐股全历史)。
    同期比较: 本期 vs 上年同期(累计口径,如 20260630 vs 20250630)。基数缺失/≤0 → None
    (上市不满一年/扭亏基数,交给上层旗标逻辑,不在这里造数)。"""
    if period[4:] == "1231":
        prev = f"{int(period[:4]) - 1}1231"
    else:
        prev = f"{int(period[:4]) - 1}{period[4:]}"
    cur, base = _num(np_deducted.get(period)), _num(np_deducted.get(prev))
    if cur is None or base is None or base <= 0:
        return None
    return (cur / base - 1.0) * 100.0


def resolve_active_ring(forecast: dict | None, express: dict | None, actual: dict | None,
                        asof: str) -> Optional[dict]:
    """单股在 asof 日的活跃环(纯): 三环取**公告日 ≤ asof 的最新落地者**(时间序,非固定优先)。
    每环 dict: {announce_date: 'YYYY-MM-DD', ...环数据};None/未落地=该环排除。
    Returns {'ring': 'forecast'|'express'|'actual', 'announce_date': str, **环数据} or None。"""
    cands = []
    for ring, row in (("forecast", forecast), ("express", express), ("actual", actual)):
        if not row:
            continue
        ann = str(row.get("announce_date") or "")[:10]
        if ann and ann <= asof:
            cands.append((ann, ring, row))
    if not cands:
        return None
    ann, ring, row = max(cands, key=lambda t: t[0])
    return {"ring": ring, "announce_date": ann, **{k: v for k, v in row.items()
                                                    if k != "announce_date"}}


def ring_floor(active: dict, cfg: dict,
               deducted: Optional[float] = None) -> dict:
    """活跃环的地板判定(纯)。active = resolve_active_ring 输出;deducted = 正式环的扣非同比
    (sina 精筛腿,幸存者才有;None=回退归母)。

    Returns {passed, np_yoy, rev_yoy, np_axis, flags}:
      np_yoy    利润轴数值(%)——正式环=扣非(可得时),其余环=归母/预告幅度
      rev_yoy   营收轴数值(%;预告环 None=数据先天缺失)
      np_axis   'deducted'|'reported'|'forecast'(口径注明用)
      flags     ['扭亏'|'未精筛']——黄旗,进人工复审不硬剔
    """
    floor_np = float(cfg.get("floor_np_yoy", 50.0))
    floor_rev = float(cfg.get("floor_rev_yoy", 20.0))
    ring = active.get("ring")
    flags: list[str] = []
    out = {"passed": False, "np_yoy": None, "rev_yoy": None, "np_axis": None, "flags": flags}

    if ring == "forecast":
        yoy, ftype = _num(active.get("yoy")), str(active.get("type") or "")
        out["np_yoy"], out["np_axis"] = yoy, "forecast"
        if ftype == "扭亏":
            flags.append("扭亏")
            out["passed"] = True   # 幅度无意义,过地板但带旗(增长质量最需要人看)
            return out
        if ftype in FORECAST_LOSS_TYPES:
            return out
        out["passed"] = yoy is not None and yoy >= floor_np
        return out

    if ring == "express":
        np_yoy, rev_yoy = _num(active.get("np_yoy")), _num(active.get("rev_yoy"))
        out.update(np_yoy=np_yoy, rev_yoy=rev_yoy, np_axis="reported")
        out["passed"] = (np_yoy is not None and np_yoy >= floor_np
                         and rev_yoy is not None and rev_yoy >= floor_rev)
        return out

    if ring == "actual":
        np_reported, rev_yoy = _num(active.get("np_yoy")), _num(active.get("rev_yoy"))
        if deducted is not None:
            np_yoy, axis = float(deducted), "deducted"
        else:
            np_yoy, axis = np_reported, "reported"
            flags.append("未精筛")  # sina 精筛腿未拉到——归母口径暂用,旗标可消
        out.update(np_yoy=np_yoy, rev_yoy=rev_yoy, np_axis=axis)
        out["passed"] = (np_yoy is not None and np_yoy >= floor_np
                         and rev_yoy is not None and rev_yoy >= floor_rev)
        return out

    return out
