"""Canonical driver-map for the JZ 西方经济 framework (ADR-0001 · Q8)。

A small, versioned map of the forecaster's recurring causal chain. Each Claim's `basis_nodes`
field references one or more node ids here. The map exists so the ledger can aggregate edge BY
driver ("is he better on 美元-driven calls than 估值-driven ones?") — the whole point of
verify-don't-trust. Free-text basis would kill that aggregation.

This is a *canonical* map (not auto-extracted): these videos recycle the same ~15 drivers, so a
hand-curated, small, stable DAG beats per-episode free-text. Nodes can evolve; keep it small.

Conventions:
  - node id  = snake_case key referenced by wm_claims.basis_nodes (comma-separated)
  - group    = coarse cluster for dashboard coloring
  - asset    = True if the node is a tradable asset that claims predict (gold/usd/oil/...);
               asset nodes carry a `series` hint (source, symbol) into western_macro_series.
  - EDGES    = causal (from → to); drawn on the dashboard's framework viz.
"""
from __future__ import annotations

# ---- canonical nodes (~15) ----
NODES = [
    # --- 上游:债务与化债工具 ---
    {"id": "debt_pressure", "label": "美债压力", "group": "debt",
     "note": "债务/GDP、净利息/GDP 双高;两种化债方式(GDP>10y / 通胀>10y)走向失败"},
    {"id": "debt_tools", "label": "化债工具", "group": "debt",
     "note": "FIMA repo / eSLR / 稳定币 / YCC / 低息置换高息 / QE 扩表"},
    # --- 利率层 ---
    {"id": "real_rate", "label": "实际利率", "group": "rates",
     "note": "流动性不足/资金紧 → 实际利率走高(加息解决不了)"},
    {"id": "term_premium", "label": "期限溢价", "group": "rates",
     "note": "买家不信美债 → 要求更高风险补偿"},
    {"id": "curve_2s10s", "label": "利率曲线 2s10s", "group": "rates", "asset": True,
     "series": ("ust", "US2S10S"), "note": "10y-2y 利差;陡峭化=长端涨短端跌"},
    # --- 汇率/商品(可结算标的)---
    {"id": "usd", "label": "美元指数 DXY", "group": "fx", "asset": True,
     "series": ("dxy", "DXY"), "note": "ICE DXY(6 腿重算);一根筋两头堵"},
    {"id": "gold", "label": "黄金", "group": "metal", "asset": True,
     "series": ("fut", "GC"), "note": "逆全球化+债务危机的长期配置"},
    {"id": "nonferrous", "label": "有色(铜)", "group": "metal", "asset": True,
     "series": ("fut", "LHC"), "note": "美元弱+补库周期→有色拉升"},
    {"id": "oil", "label": "原油", "group": "energy", "asset": True,
     "series": ("fut", "CL"), "note": "70-90 宽幅震荡;地缘/霍尔木兹"},
    # --- 权益 ---
    {"id": "us_equity", "label": "美股", "group": "equity", "asset": True,
     "series": ("usidx", ".INX"), "note": "AI 泡沫;巴菲特指标;资金流向依赖美元强势"},
    {"id": "semis", "label": "半导体", "group": "equity", "asset": True,
     "note": "SOXX/韩国;26 年杠杆问题、27 年基本面问题;反弹≠反转"},
    {"id": "a_share", "label": "A股", "group": "equity", "asset": True,
     "series": ("index_daily", "000300"), "note": "科技 vs 传统 两剧本;下跌买、禁定投"},
    # --- 结构性力量 ---
    {"id": "leverage", "label": "杠杆退潮", "group": "flow",
     "note": "CTA/对冲基金/韩股杠杆 ETF;Situational Awareness 爆仓=标志事件"},
    {"id": "valuation", "label": "估值", "group": "flow",
     "note": "巴菲特指标=总市值/GDP;2000 互联网泡沫对比(不可刻舟求剑)"},
    {"id": "policy", "label": "政策/事件", "group": "flow",
     "note": "TACO / 中期选举 / 美联储框架 / 美日联合干预 / 预期管理"},
]

# ---- causal edges (from → to) ----
EDGES = [
    ("debt_pressure", "debt_tools"),
    ("debt_tools", "real_rate"),
    ("debt_tools", "term_premium"),
    ("real_rate", "curve_2s10s"),
    ("term_premium", "curve_2s10s"),
    ("curve_2s10s", "usd"),
    ("curve_2s10s", "gold"),
    ("usd", "gold"),
    ("usd", "nonferrous"),
    ("usd", "oil"),
    ("policy", "usd"),       # 美日联合干预/预期管理直接压美元
    ("oil", "nonferrous"),   # 补库周期联动
    ("leverage", "semis"),
    ("leverage", "us_equity"),
    ("valuation", "us_equity"),
    ("real_rate", "us_equity"),
    ("semis", "us_equity"),
    ("us_equity", "a_share"),
    ("policy", "a_share"),
]

NODE_BY_ID = {n["id"]: n for n in NODES}
ASSET_NODES = [n for n in NODES if n.get("asset")]


def node_label(node_id: str) -> str:
    n = NODE_BY_ID.get(node_id)
    return n["label"] if n else node_id


def valid_node(node_id: str) -> bool:
    return node_id in NODE_BY_ID
