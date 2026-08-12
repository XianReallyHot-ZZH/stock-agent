"""Research module: read-only ETF 择时跟踪看板（timing tracking）.

转定位（原 性价比评估）：净值-MA 偏离度（分位 + 第几极值）+ 份额-净值剪刀差分化跟踪。
纯跟踪、不标买卖点。不喂交易引擎；产每-ETF 择时快照供 HTML 看板（scripts/research_report.py）消费。

子模块直接 import（不在 __init__ re-export），避免删除旧 scoring 模块时遗留导入链：
  - timing    纯函数：偏离度（复制自 tracker，隔离）+ 剪刀差检测 + timing_snapshot
  - earnings  业绩预告评分（信息列；data/manager.py 依赖此模块）
  - report    HTML 渲染
  - commentary  (休眠) 旧 LLM 解读，随性价比模型退役，留盘不调即无害
"""
