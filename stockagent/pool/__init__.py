"""候选个股池 (V8 pool · 第六看板) — 高业绩池(陈氏季度池),只读旁路(ADR-0001),永不喂引擎。

术语(docs/stock_pool/CONTEXT.md):
  候选池 = 本看板的筛选宇宙(全市场非 ST,spot 快照驱动,动态·数据推导);
  高业绩池 = 主输出(逐股状态机:三环地板过门入/不过门出,两轨估值 Top-N);
  观察池 = DataManager.STOCK_WATCHLIST(40 只,人工策展·个股诊断看板对象)。三词不混用。

模块(V8 主轴,全部纯函数除 screen/report):
  universe   宇宙推导(全市场非 ST)+ 行业 join
  gates      三环地板 + 逐股状态机判定(预告单腿/快报双轴/正式扣非双轴)
  valuation  两轨估值尺(PE_ttm 自算+PEG / PB 分位;point-in-time)
  risk       风险红黄旗(商誉/存贷双高代理/应收/并购代理;红旗硬剔黄旗复审)
  sector     行业构成 + 涌现簇信号(≥50%;50/50 对照已删——用户裁定)
  calendar   披露日历(正式报法定截止 + 当期锚定)
  prices     复权核心(raw 存储 + 分红表运行时前复权;PB 分子用 raw 价)
  study      验证器纯核心(状态机回放·指数对照·消融臂)
  screen     装配层(唯一读 store:粗筛→幸存者精筛→两轨排名)
  report     HTML 渲染(fat renderer)

退役留盘(2026-09-12 用户批准 Q1=A,代码休眠不删): scoring(S1偏离超卖)/fierce(S2猛×深跌)/
revision(E4修正动量)/pead/facechange/forecast_industry 及其验证器——实证结论留档 meta,
六表参数已从 params.yaml 移除。

隔离要点: 不改 research/__init__.py、不 re-export(删模块不炸别家);纯函数仅
numpy/pandas/标准库,跨包 import 不取(原 research/tracker 依赖随六表退役移除)。
"""
