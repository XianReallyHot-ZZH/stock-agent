"""西方宏观预测台账 — read-only 旁路 (ADR-0001)。

把 JZ《西方经济》转写语料变成可追踪的预测业绩记录:抽取可证伪 claim → 对朴素基准打分 →
随新集增长。度量一个自由裁量宏观预测者,绝不自动执行,永不喂 A 股轮动引擎。

子模块:
  drivers  — canonical 驱动图(claim.basis 引用其节点;看板可视化其因果链)
  extract  — LLM 抽取 transcript → draft claims/rules(人工确认闸门,见 ADR-0001 / Q9)
  score    — hit/baseline/edge/range/primary/rules-quarantine 评分 + 自动结算
  dashboard — HTML 看板(台账表 + 驱动图 + track-record 统计 + 结算 feed + 覆盖率)
"""
