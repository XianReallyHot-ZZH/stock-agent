"""标的分类器(价值/成长/周期)— Phase 1-A 填充。

Phase 1-B 占位:指数择时层不需要分类。ETF/个股层的「分类→选指标」统一框架在
Phase 1-A 实现:
  - ETF:读 etf_pool.yaml 的人工标签(主+次,软分类),csrc_industry 映射做校验;
  - 个股:用财务数据自动判定(增速高→成长、股息率高PE低→价值、利润波动大→周期)。

统一的是「分类→选指标」的规则,不统一的是「怎么打标签」(见 PRD §4)。
"""
from __future__ import annotations


def classify(symbol: str, store=None) -> tuple[str | None, list[str]]:
    """占位 — Phase 1-A 实现。返回 (主类型, [次类型])。

    Phase 1-A:
      value / growth / cyclic 之一为主;次类型为附加展示(如食品饮料: 主=价值, 次=[成长])。
    """
    # TODO(Phase 1-A): ETF → etf_pool.yaml style tags; 个股 → derive from ROE/增速/股息率.
    return (None, [])
