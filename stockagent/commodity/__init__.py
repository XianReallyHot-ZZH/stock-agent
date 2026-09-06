"""大宗商品看板(第八看板 · 只读旁路 · 永不喂引擎 · 2026-09 从个股诊断拆出)。

商品周期观测温度计 + 周期股择时深化底座。模块:
  panel     品种面板数据组装(国际基准为主语/国内价对照;纯读 store)
  overview  📊 总览(官方中证商品指数 + 自算等权广度;南华 akshare 端点已死,ccidx 替代)
  ratios    ⚖ 比价矩阵(金银比/油金比/螺矿比…;黄金只做分母不做主语)
  figures   比价/总览 figure builders(品种时序图复用 tracker.stock_figures)
  targets   🎫 投资标的映射(二期:品种→大A可投标的+错配度=ETF NAV 涨幅−品种涨幅)
  fundamentals 🔬 基差/期限结构/库存纯函数(二期剩余·过了 event-study 礼遇:expanding 分位/期限斜率/前向收益)
  render    渲染 data/commodity.html:🚦雷达 → 📊总览 → 🧲面板 → 📈时序图 → ⚖比价 → 🎫标的 → 🔬基差库存

隔离纪律:commodity→tracker 单向 import 原语(judge_commodity/commodity_dev_stats 等),
与 pool/china_macro 同向;不改 tracker 内部、不 re-export。
传导链不动:commodity_map→leading.commodity_signal/alignment→positioning_score 仍吃国内价。
温度计非开关:全部 section 观测非信号,不出现买卖措辞,不接微信推送。
"""
