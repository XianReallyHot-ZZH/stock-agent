"""一致预期修正动量·个股版(纯)——E4 revision_momentum 的单股化(去权重/去成分聚合)。

同口径三件套(与 research/earnings.py 一致): 同财年守卫(fy1_year 不一致 → None,年末翻滚
期差分诚实不可算) / ±deadband 死区外才算方向 / n_reports 门(研报数不足不采)。
冷启动 4 份周快照(min_snapshots),不足 → 「累积中 N/4」诚实横幅(E4 同款)。
"""
from __future__ import annotations

import math

import pandas as pd


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def stock_revision(row_now, row_then, deadband: float = 0.01,
                   min_reports: int = 3) -> dict | None:
    """两份快照的同股同财年 forward-EPS 差分(纯)。row = stock_consensus 快照单行
    (Series/dict,[n_reports, eps_fy1, fy1_year])。

    Returns {rev, up, down} | None(财年翻滚 / 缺 EPS / 正基数不满足 / 研报数不足)。
    rev = eps_now/eps_then − 1(小数);up/down = 死区外的方向(微动是噪音)。
    """
    if row_now is None or row_then is None:
        return None
    fy_now, fy_then = row_now.get("fy1_year"), row_then.get("fy1_year")
    if _nan(fy_now) or _nan(fy_then) or int(fy_now) != int(fy_then):
        return None  # 财年翻滚期,差分无意义
    eps_now, eps_then = row_now.get("eps_fy1"), row_then.get("eps_fy1")
    if _nan(eps_now) or _nan(eps_then) or float(eps_then) <= 0:
        return None
    nr = row_now.get("n_reports")
    if _nan(nr) or float(nr) < min_reports:
        return None
    rev = float(eps_now) / float(eps_then) - 1.0
    return {"rev": rev, "up": rev > deadband, "down": rev < -deadband}


def revision_table(snap_now: pd.DataFrame, snap_then: pd.DataFrame,
                   dates_available: int, codes: list[str],
                   cfg: dict | None = None) -> dict:
    """universe 全员的修正动量表(纯)。snap_* = stock_consensus 快照(indexed by code)。

    Returns {rows, cold_start}:
      快照数 < min_snapshots → rows=[], cold_start={have, need}(渲染「累积中 N/4」横幅)
      rows = [{code, rev, up}] 降序;rev 百分数(显示友好),rank 留给调用方横截面排。
    """
    cfg = cfg or {}
    min_snapshots = int(cfg.get("min_snapshots", 4))
    deadband = float(cfg.get("deadband", 0.01))
    min_reports = int(cfg.get("min_reports", 3))
    if dates_available < min_snapshots or snap_now is None or snap_then is None \
            or not len(snap_now) or not len(snap_then):
        return {"rows": [], "cold_start": {"have": int(dates_available), "need": min_snapshots}}
    rows: list[dict] = []
    for code in codes:
        code = str(code)
        if code not in snap_now.index or code not in snap_then.index:
            continue
        r = stock_revision(snap_now.loc[code], snap_then.loc[code],
                           deadband=deadband, min_reports=min_reports)
        if r is None:
            continue
        rows.append({"code": code, "rev_pct": r["rev"] * 100.0, "up": r["up"], "down": r["down"]})
    rows.sort(key=lambda x: -x["rev_pct"])
    return {"rows": rows, "cold_start": None}
