"""宏观 call 实时确认 (Live Call Confirmation · Phase 3 · ADR-0001 只读旁路).

现有 score.py 在 claim 的 horizon 到期时结算 hit/edge (事后判分)。本模块补**事中**视角:
对每条可结算 claim, 用规则判断数据*当前*是否在兑现它 —— 兑现中 / 背离 / 停滞 (未到期),
或 已兑现·{命中/未中/本事} (horizon 已过, 复用结算), 或 待人工/无数据。

为何要它: JZ 在**利率**(2Y/10Y/2s10s) 上用的是*方向语言* (上行/下降/陡峭化), 不是黄金那种
阶段语言; 而 edge 正在利率方向。本视图把「数据当前在哪 vs 他的方向判断」摊开, 用上真 edge,
是「阶段定位」哲学在方向语言资产上的正确落地 (黄金阶段定位器是阶段语言资产的落地)。

围栏不变: 只读诊断, 永不喂 A股轮动引擎 (ADR-0001)。

复用 (不重写):
  - stockagent.western_macro.score.{series_for, _value_at_or_before, _value_at_or_after, _parse_date, _DIR_THRESHOLD}
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

from .score import (_DIR_THRESHOLD, _parse_date, _value_at_or_after,
                    _value_at_or_before, series_for)

log = logging.getLogger(__name__)

# 状态 (中文, 给看板/CLI 用)
ON_TRACK = "兑现中"        # 未到期, 数据正向 claim 方向走
DIVERGING = "背离"         # 未到期, 数据反向走
STALLED = "停滞"           # 未到期, 基本没动 (< 阈值)
RES_HIT = "已兑现·命中"
RES_MISS = "已兑现·未中"
RES_EDGE = "已兑现·本事"
MANUAL = "待人工"          # horizon 过但未自动结算 (level/timing/event) 或数据不足
NO_SERIES = "无数据"
ALT_BRANCH = "备选"        # scenario 非主推分支 (不计分, 不确认)

_OPEN_STATUSES = (ON_TRACK, DIVERGING, STALLED)


def _direction_status(ret: float, claimed: Optional[str], thr: float = _DIR_THRESHOLD) -> tuple[str, float]:
    """方向 call 的实时状态 (ret=自发布日以来的归一化收益)。返回 (status, progress)。

    progress = |ret| (走得越远, 兑现越深)。flat call = 留在区间才算兑现。"""
    if claimed == "up":
        if ret > thr:
            return ON_TRACK, abs(ret)
        if ret < -thr:
            return DIVERGING, abs(ret)
        return STALLED, 0.0
    if claimed == "down":
        if ret < -thr:
            return ON_TRACK, abs(ret)
        if ret > thr:
            return DIVERGING, abs(ret)
        return STALLED, 0.0
    # flat / None: 留在 ±thr 内 = 兑现 (震荡符合预期), 超出 = 背离
    if abs(ret) <= thr:
        return ON_TRACK, 0.0
    return DIVERGING, abs(ret)


def live_call_confirmation(claim: dict, store, settlement: Optional[dict] = None,
                           asof: Optional[str] = None) -> dict:
    """一条 claim 的实时确认。settlement=该 claim 的到期结算 (可选, 已过 horizon 时用)。"""
    asset = claim.get("asset")
    ctype = claim.get("claim_type")
    claimed = claim.get("direction")
    ep = (claim.get("episode_date") or "")[:10]
    out = {"uid": claim.get("uid"), "asset": asset, "claim_type": ctype, "direction": claimed,
           "episode_date": ep, "horizon": claim.get("horizon"), "statement": claim.get("statement") or "",
           "status": None, "progress": None, "ret": None, "start": None, "current": None, "note": ""}

    s = series_for(asset, store)
    if s is None or len(s) == 0:
        out["status"] = NO_SERIES
        return out

    cur_asof = (asof or str(s.index[-1]))[:10]
    hdate = _parse_date(claim.get("horizon"))
    horizon_passed = bool(hdate) and hdate <= cur_asof

    # 备选情景分支不计分 → 不确认
    if ctype == "scenario" and claim.get("is_primary") == 0:
        out["status"] = ALT_BRANCH
        return out

    if horizon_passed:
        if settlement:
            if settlement.get("edge"):
                out["status"] = RES_EDGE
            elif settlement.get("hit"):
                out["status"] = RES_HIT
            else:
                out["status"] = RES_MISS
        else:
            out["status"] = MANUAL     # level/timing/event 到期但人工结算
        return out

    # ---- 未到期: 算实时进度 ----
    start = _value_at_or_after(s, ep)
    cur = _value_at_or_before(s, cur_asof)
    if start is None or cur is None or abs(start) < 1e-9:
        out["status"] = MANUAL
        return out
    ret = (cur - start) / abs(start)   # abs 分母 → 负值系列(2s10s 倒挂)方向也对
    out.update({"start": start, "current": cur, "ret": ret})
    note = f"{start:.2f}→{cur:.2f} ({ret:+.1%})"

    if ctype in ("direction", "scenario"):
        status, prog = _direction_status(ret, claimed)
        out["status"], out["progress"], out["note"] = status, prog, note
    elif ctype == "range":
        lo, hi = claim.get("range_low"), claim.get("range_high")
        if lo is None or hi is None:
            out["status"], out["note"] = MANUAL, note
        elif lo <= cur <= hi:
            out["status"], out["progress"], out["note"] = ON_TRACK, None, note + " · 在区间内"
        else:
            out["status"], out["progress"], out["note"] = DIVERGING, abs(ret), note + f" · 离区间({lo:.0f}-{hi:.0f})"
    elif ctype == "level":
        L = claim.get("level_value")
        if L is None:
            out["status"], out["note"] = MANUAL, note
        else:
            # 朝目标 L 的进度: 已走距离 / 总距离
            total = L - start
            done = cur - start
            prog = (done / total) if abs(total) > 1e-9 else 0.0
            touched = (cur >= L) if (claimed == "up" or (claimed != "down" and total > 0)) else (cur <= L)
            if touched and claimed in ("up", "down"):
                out["status"], out["progress"], out["note"] = ON_TRACK, 1.0, note + f" · 触及{L:.0f}"
            elif (prog > 0) if total > 0 else (prog < 0):   # 朝目标走
                out["status"], out["progress"], out["note"] = ON_TRACK, max(0.0, min(abs(prog), 1.0)), note
            else:
                out["status"], out["progress"], out["note"] = DIVERGING, abs(ret), note
    else:  # timing / event-horizon → 无法自动确认
        out["status"], out["note"] = MANUAL, note
    return out


def live_confirmation_overview(store, asof: Optional[str] = None,
                               states: tuple = ("draft", "confirmed")) -> dict:
    """全量 claim 的实时确认总览: 计数 + 按标的聚合 (JZ 当前净方向 + 兑现比)。

    返回 {asof, total, counts:{status:count}, open_total, by_asset:{asset:{...}}, rows:[...]}。
    """
    claims = [c for c in store.get_wm_claims() if c.get("state") in states]
    sett = {s["claim_uid"]: s for s in store.get_wm_settlements()}
    rows = [live_call_confirmation(c, store, sett.get(c["uid"]), asof) for c in claims]
    counts = Counter(r["status"] for r in rows)
    open_rows = [r for r in rows if r["status"] in _OPEN_STATUSES]

    by_asset: dict[str, dict] = {}
    for r in open_rows:
        a = by_asset.setdefault(r["asset"], {
            "open": 0, ON_TRACK: 0, DIVERGING: 0, STALLED: 0,
            "rets": [], "directions": []})
        a["open"] += 1
        a[r["status"]] += 1
        if r["ret"] is not None:
            a["rets"].append(r["ret"])
        if r["direction"]:
            a["directions"].append(r["direction"])

    # 每标的的净方向 + 平均兑现收益
    for a, d in by_asset.items():
        rets = d.pop("rets")
        dirs = d.pop("directions")
        d["net_direction"] = _net_direction(dirs)
        d["avg_ret"] = (sum(rets) / len(rets)) if rets else None

    return {
        "asof": asof,
        "total": len(rows),
        "open_total": len(open_rows),
        "counts": dict(counts),
        "by_asset": by_asset,
        "rows": rows,
    }


def _net_direction(directions: list[str]) -> Optional[str]:
    """一组方向的主导方向 (up/down/flat/分歧/None)。"""
    if not directions:
        return None
    c = Counter(directions)
    up, down = c.get("up", 0), c.get("down", 0)
    if up and not down:
        return "看涨"
    if down and not up:
        return "看跌"
    if up > down:
        return "偏看涨"
    if down > up:
        return "偏看跌"
    if c.get("flat", 0) and not up and not down:
        return "看平"
    return "分歧"
