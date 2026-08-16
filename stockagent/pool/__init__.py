"""候选个股池 (V7 pool · 第六看板) — 全覆盖池筛选漏斗,只读旁路(ADR-0001),永不喂交易引擎。

术语(docs/stock_pool/CONTEXT.md):
  候选池 = 本看板的筛选宇宙与输出(consensus 覆盖池 ∩ 非 ST,动态·数据推导,~2300 只);
  观察池 = DataManager.STOCK_WATCHLIST(40 只,人工策展·个股诊断看板对象)。两词不混用。

模块(全部纯函数除 screen/report):
  universe   宇宙推导 + 行业 join
  prices     复权核心(raw 存储 + 分红表运行时前复权 + 除权嫌疑带)
  scoring    策略1 偏离超卖复合分(触发+护栏+企稳)
  fierce     策略2 猛×深跌(周期=商品驱动 / 成长=三腿;独立于 P1 positioning_score)
  calendar   披露日历 v2(正式报截止 + 窗口A/B 状态机)
  revision   一致预期修正动量·个股版(E4 同口径)
  pead       预告超预期漂移(point-in-time 双腿,无前视)
  facechange 业绩变脸检测(跳档/连亏/趋势破位/拐头向上;同尾比较防累计口径失真)
  study      event-study 纯核心(验证器共用)
  screen     装配层(唯一读 store)
  report     HTML 渲染(fat renderer)

隔离要点: 不改 research/__init__.py、不 re-export(删模块不炸别家);纯函数仅
numpy/pandas/标准库,跨包 import 只取 research.timing / research.earnings /
tracker.stock_diagnose / tracker.leading 的纯原语。
"""
