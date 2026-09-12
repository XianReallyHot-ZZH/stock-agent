"""风险筛红黄旗(纯)——模型缩小范围 → 人工排除的方法论落地(V8 高业绩池, 2026-09)。

分层判据(Q15 锁定): 红旗=信号本身即风险(硬剔,不进池);黄旗=需要人判断行业语境(复审,
进池但带旗)。阿里帖原文四条 + 本仓库数据可得性的诚实适配:
  红旗·商誉/净资产 >30%            sina goodwill ÷ zcfz 股东权益(逐股精筛腿)
  红旗·存贷双高代理                zcfz 货币资金/总资产 ≥15% 且 资产负债率 ≥40%
                                    (有息负债[短借+长借]批量端点无该列——**代理口径**,
                                     总负债含经营性应付会高估有息端,读图说明注明)
  黄旗·应收/营收 >50%              zcfz 应收 ÷ report_actual 营收累计(建筑军工行业性高)
  黄旗·商誉环比激增 >30%           sina goodwill 两期比(并购增长代理,人工查 F10 主业)
  黄旗·扭亏                        gates 层传入(首年增长质量最需要人看)
原第四条「营运资本/长期负债」无任何数据源 → 降级为人工检查项(MANUAL_REVIEW.md),不自动筛。
"""
from __future__ import annotations

import math
from typing import Optional


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (isinstance(f, float) and math.isnan(f)) else f


def goodwill_ratio(goodwill: Optional[float], equity: Optional[float]) -> Optional[float]:
    """商誉/净资产(纯)。sina goodwill × 亿元单位? 否——两源同 元 口径: sina goodwill(元) ÷
    zcfz equity(元)。任一缺或净资产 ≤0 → None(缺数据≠安全,黄旗「数据缺」另行呈现)。"""
    g, e = _num(goodwill), _num(equity)
    if g is None or e is None or e <= 0:
        return None
    return g / e


def goodwill_jump(goodwill_series: dict[str, float], period: str) -> Optional[float]:
    """商誉环比激增(纯): 本期 goodwill / 上年同期 − 1。sina 全历史逐期;基数 0/缺 → None
    (0→正的从无到有是并购落地,但基数 0 比值无意义——商誉绝对额小也无害,交人工)。"""
    y = int(period[:4])
    prev = f"{y - 1}1231" if period[4:] == "1231" else f"{y - 1}{period[4:]}"
    cur, base = _num(goodwill_series.get(period)), _num(goodwill_series.get(prev))
    if cur is None or base is None or base <= 0:
        return None
    return cur / base - 1.0


def risk_flags(balance: dict | None, goodwill_now: Optional[float],
               goodwill_series: dict[str, float], rev_abs: Optional[float],
               period: str, extra_yellow: list[str] | None = None,
               cfg: dict | None = None) -> dict:
    """红黄旗汇总(纯)。balance = zcfz 当期行 {cash, receivables, total_assets, equity,
    debt_ratio}(None=期未披露);goodwill_now = sina 当期商誉;goodwill_series = sina 全历史;
    rev_abs = report_actual 当期营收累计;extra_yellow = gates 层旗(扭亏等)。

    Returns {red: [str], yellow: [str], metrics: {...}, data_gaps: [str]}——
    metrics 带原值(看板悬停);red 非空 → 硬剔。"""
    cfg = cfg or {}
    rcfg = cfg.get("risk", {}) or {}
    red: list[str] = []
    yellow: list[str] = list(extra_yellow or [])
    gaps: list[str] = []
    metrics: dict = {}

    # 商誉红旗(sina 逐股;幸存者精筛腿未拉 → 黄旗「待精筛」,拉过即消)
    if goodwill_now is not None and balance and _num(balance.get("equity")):
        ratio = goodwill_ratio(goodwill_now, balance.get("equity"))
        metrics["goodwill_equity"] = ratio
        if ratio is not None and ratio > float(rcfg.get("goodwill_equity_red", 0.30)):
            red.append("商誉/净资产过高")
    elif goodwill_now is None:
        gaps.append("商誉未精筛")

    # 存贷双高代理(zcfz;期未披露 → 数据缺,不判)
    if balance and _num(balance.get("total_assets")) and _num(balance.get("total_assets")) > 0:
        cash = _num(balance.get("cash")) or 0.0
        ta = _num(balance.get("total_assets"))
        dr = _num(balance.get("debt_ratio"))
        cash_pct = cash / ta if ta > 0 else None
        metrics["cash_assets"] = cash_pct
        metrics["debt_ratio"] = dr
        if (cash_pct is not None and dr is not None
                and cash_pct >= float(rcfg.get("dual_high_cash_pct", 0.15))
                and dr >= float(rcfg.get("dual_high_debt_ratio", 0.40))):
            red.append("存贷双高(代理口径)")
    else:
        gaps.append("资产负债表未披露")

    # 应收黄旗(zcfz 应收 ÷ 营收累计;两腿任一缺 → 数据缺)
    if balance and _num(balance.get("receivables")) is not None and _num(rev_abs) \
            and _num(rev_abs) > 0:
        ratio = _num(balance.get("receivables")) / _num(rev_abs)
        metrics["receivable_rev"] = ratio
        if ratio > float(rcfg.get("receivable_rev_yellow", 0.50)):
            yellow.append("应收/营收过高")
    elif balance is None or _num(rev_abs) is None:
        if "资产负债表未披露" not in gaps:
            gaps.append("应收数据缺")

    # 商誉激增黄旗(并购增长代理)
    jump = goodwill_jump(goodwill_series, period) if goodwill_series else None
    if jump is not None:
        metrics["goodwill_jump"] = jump
        if jump > float(rcfg.get("goodwill_jump_yellow", 0.30)):
            yellow.append("商誉激增(并购代理)")

    return {"red": red, "yellow": yellow, "metrics": metrics, "data_gaps": gaps}
