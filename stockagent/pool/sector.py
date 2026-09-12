"""行业构成与涌现簇(纯)——行业暴露从池里涌现,不自上而下预判(V8 高业绩池, 2026-09)。

方法论对齐(图片6): 7月二季报预告筛完,发现高增速池里 >50% 来自商品周期(化工/锂电锂矿/
有色/石油/能源)——才有了商品周期仓位。行业选择同样从池里涌现;本模块只陈列事实:
  composition   行业分布表 + 类型分布(展示)
  emergent      涌现簇信号: 商品关联周期(type=cyclic 且有 commodity 映射)占比 ≥阈值 → 高亮
50/50 仓位对照**不做**(2026-09-12 用户裁定删除——ADR-0001 栅栏: 陈氏仓位纪律是他的交易层,
陈列有被误读为建议的风险)。看板只报「当前池构成是什么」,配比是仓位管理看板与用户自己的域。
"""
from __future__ import annotations

from collections import Counter


def composition(rows: list[dict], top_n: int = 15) -> dict:
    """池构成统计(纯)。rows = 池成员行(含 industry/type 字段)。
    Returns {by_industry: [{industry, n, share}], by_type: {type: n},
    n_total, n_untyped}——share 相对全池。"""
    n = len(rows)
    if n == 0:
        return {"by_industry": [], "by_type": {}, "n_total": 0, "n_untyped": 0}
    ind_cnt = Counter(str(r.get("industry") or "未映射") for r in rows)
    typ_cnt = Counter(str(r.get("type") or "未分类") for r in rows)
    by_ind = sorted(
        ({"industry": k, "n": v, "share": v / n} for k, v in ind_cnt.items()),
        key=lambda d: (-d["n"], d["industry"]))
    return {
        "by_industry": by_ind[:top_n],
        "n_industries": len(ind_cnt),
        "by_type": dict(typ_cnt),
        "n_total": n,
        "n_untyped": typ_cnt.get("未分类", 0),
    }


def emergent(rows: list[dict], threshold: float = 0.50) -> dict | None:
    """涌现簇信号(纯): 「商品关联周期」= type=cyclic 且 commodity_variety 非空的成员占比
    ≥ threshold → {share, n, n_total, industries: [...]} else None。
    未映射/无行业的成员不计入分子(诚实:类型未知不能猜)。阈值默认 50%(陈老师原文口径)。"""
    n = len(rows)
    if n == 0:
        return None
    members = [r for r in rows
               if r.get("type") == "cyclic" and isinstance(r.get("commodity_variety"), str)
               and r.get("commodity_variety")]
    share = len(members) / n
    if share < threshold:
        return None
    inds = sorted({str(r.get("industry") or "未映射") for r in members})
    return {"share": share, "n": len(members), "n_total": n, "industries": inds}
