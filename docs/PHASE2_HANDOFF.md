# Phase 2（个股层）Handoff — 清理上下文后恢复用

## ⚡ 快速恢复（先读这段，30 秒定位）

**状态（2026-07-25）**：Phase 2 个股层 **全部完成并已推送**（commit `754e9a6` 在 `origin/master`，本地与远程同步）。全套 **308 测试绿**。

**4 条命令验证一切在跑**：
```bash
python -m pytest tests/ -q                     # 308 passed
python scripts/backfill_stock_data.py          # 回填观察池 5 只(日线/估值/财报/分红/预告,幂等)
python scripts/stock_report.py                 # 生成 data/stock_diagnose.html(双击看) + 控制台摘要
python scripts/stock_report.py --push-alerts   # 同上 + 推送提醒(未配置渠道时优雅降级)
```

**文件地图**：
| 角色 | 文件 |
|---|---|
| 诊断库(核心) | `stockagent/tracker/stock_diagnose.py`（classify / valuation_zone / E3 / S07归因 / S10戴维斯 / S08避坑 / 预告链 + `diagnose_stock_full` 打包） |
| 提醒规则 | `stockagent/tracker/alerts.py::evaluate_stocks`（A1/A2/A3/G1/G2/E3/E4）+ `stock_diagnose.collect_stock_alerts` |
| 看板渲染 | `stockagent/tracker/stock_report.py`（卡片式 HTML,告警区+个股卡片,深浅色可切） |
| 数据抓取 | `stockagent/data/fetcher.py`（`fetch_stock_daily/valuation/financials/dividend/forecast_panel`） |
| 入库 | `stockagent/data/store.py`（stock_valuation/financials/dividend/forecast 表）+ `manager.py`（`update_stock_*`） |
| 脚本 | `scripts/backfill_stock_data.py`（--daily/--val/--fin/--div/--forecast）/ `scripts/stock_report.py`（--codes/--as-of/--push-alerts） |
| 配置 | `config/params.yaml` 的 `stock:` 段（classify / davis / pitfalls / valuation 阈值） |
| 测试 | `tests/test_stock_data.py` / `test_stock_diagnose.py` / `test_stock_report.py` / `test_alerts.py` |
| 看板输出 | `data/stock_diagnose.html`（已 commit，随仓库走） |

**观察池**：`DataManager.STOCK_WATCHLIST` = [600519 茅台 / 600036 招行 / 300750 宁德 / 000651 格力 / 688981 中芯]（C0 占位，待迁 `config/stock_pool.yaml`）。

**恢复后第一步**：继续开发 → 挑下面「可选增强」一个；验证 → 跑上面 4 条命令；懂某块 → 读对应 docstring（每个函数都有详细中文注释）+ 本文件下方的详细记录。

**数据约定 & 已知 wart**：
- `data.adjust='raw'`（全项目 ETF+个股同）；日价受 `history_years=6` 限（长持仓期 S07 归因需先扩 price 回填）；baidu PE/PB 有 IPO 起全史但**稀疏**（半月级，够算分位不能当日频）。
- `stock_dividend_yield` 偏高（raw 价被历年分红压低 + 一年 N 次分红节奏切换年 TTM 多吃 1 次）——分类鲁棒（价值也经干净 PE 分位识别），精算留 C2/B1。
- 业绩预告**稀疏**（仅显著变动才发，观察池 5 只只 2 只有 2024 年报预告）。

## 📋 可选增强（讨论过、未做，按价值排序）

1. **业绩含金量指标**（归母 vs 扣非）：库里已有 `np_deducted`（扣非净利润）。加一个 diagnose + 提醒——「归母涨但扣非没涨/下滑」=利润靠一次性收益撑，业绩含金量低（S08 雷）。**价值高**：直接补上 S08 利润质量维度。
2. **避坑行始终显示 2y CAGR**：当前只在触发硬异常(≥150%/腰斩)时显示可信增速。中芯例揭示——单年+36% vs 2年+2.2% 的落差本身有信息价值，应始终并排显示（或落差>X倍时软提示「疑似基数反弹」）。
3. **看板加 plotly 图**：当前是卡片快照。可加 PE/PB 历史分位曲线（baidu 全史）+ S07 归因柱状图（业绩/估值/分红）+ 戴维斯业绩-估值象限图。让个股看板和 ETF/指数看板风格一致。
4. **`min_profit_growth` 纳入周期判定**：目前只看利润波动 std，`min_profit_growth`（最差年）算了但没用。加「std≥阈值 AND 经历过明显下滑年」双保险，减少误判。
5. **`config/stock_pool.yaml` 正式化**：观察池迁出 manager 硬编码，带 name/行业标签，扩到 >5 只覆盖更多行业测分类鲁棒性。
6. **业绩快报 `stock_yjbb_em`**：补预告链第二环（预告→快报→正式报），快报给实际数做交叉校验。字段已探（净利润同比/营收同比/公告日）。

> 验证范式：用 **中芯 688981** 做多镜头交叉验证最直观——戴维斯(双击信号) + S07(过去一年+80%里96%靠估值) + 避坑(净利单年+36%但2年CAGR+2.2%=反弹) + E3(grade4强突破但91%偏热) → 三镜头汇聚「周期反转但主要由估值/情绪驱动,业绩兑现未跟上」。

---

## 项目现状

**Phase 1-B（指数择时）+ Phase 1-A（ETF 三类分类）全部完成**，代码 + 看板 + 测试(160) + 文档 + skills 都在远端。

### 关键文件位置
- **PRD**：`docs/PRD-A股综合跟踪工具.md`（产品定义，三层架构 + 三类分类 + 信号提醒）
- **执行计划**：`docs/EXECUTION_PLAN.md`（任务级，§4 是 Phase 2 骨架）
- **CLAUDE.md**：项目文档（含 tracker Phase 1-B + research Phase 1-A）
- **课程文档**：`docs/InvestmentCourseBegin/`（14 篇，gitignored）

### Phase 1 已完成

**Phase 1-B（指数择时层）**：
- 数据：5 宽基日线 + 沪深300 PE/PB + 全市场 PB
- 看板：`data/index_timing.html`（六 section + 深浅色 + 伸缩/hover）
- 命令：`backfill_index.py` / `index_timing_report.py`
- skill：`tracker-dashboard`

**Phase 1-A（ETF 三类分类）**：
- `etf_pool.yaml` 29 只加 `style`（value/growth/cyclic，主+次）
- `scoring.py` `analyze_etf` 按 style 分流（value/growth 用 PE 分位，cyclic 不估值待 PB）
- `alerts.py` 九条信号提醒（D 筹码×估值交叉 / E1E2 趋势 / B1 股息 / A1A2 业绩 / F1 大盘）
- 双通道：看板告警区 + 微信 `--push-alerts`
- 三类分页看板 + 锚点导航 + pool_summary 三类分布

## Phase 2（个股层）范围

### 目标
个股级诊断：三类自动判定 + 利润来源归因 + 避坑四类 + 戴维斯双击/双杀。

### C0 数据栈（从零建）
- **金矿已验证**：`stock_zh_valuation_baidu(symbol, indicator, period)` → PE(TTM)/PE(静)/PB/市现率/总市值，全市场 IPO 起（**免财报硬算 PE/PB**）。**无股息率**（需 `stock_history_dividend` derive）。
- **个股日线**：`stock_zh_a_daily(symbol="sh600519")`（sina，需修 `_szsh_prefix` bug：`6xx/68x/9xx→sh`）。
- **财务表**（如果 baidu 不够）：营收/扣非净利/ROE/毛利率 → 利润归因 + 三类自动判定需要。可能需 `stock_financial_*` 系列。

### C1 个股诊断
- 三类自动判定：增速高→成长、股息率高PE低→价值、利润波动大→周期
- 利润来源归因（S07 茅台拆解）：业绩/分红/估值各自贡献
- 避坑四类（S08）：公告时间差、复合增速、异常高增速、业绩预告链
- 戴维斯双击/双杀（S10）

### C2 个股提醒
A3(营收增速下滑) / G1(披露窗口) / G2(公告时间差) / E3(偏离极值套利) / E4(蓝筹vs成长背离)

### 关键设计约束（从 Phase 1 继承）
- **cyclic 用 PB**（Phase 1-A ETF 层缺 PB→Phase 2 个股层补真值后回填 ETF 层）
- **D 筹码×估值交叉**（不只看筹码，综合估值位置判断）
- **避坑**：异常高增速用最小值分母（S08）、两年复合增速
- **每个新功能有 pytest**（CLAUDE.md 硬约束）

### Phase 2 触发条件
Phase 1-A/1-B 跑通（已完成）+ 个股财务数据栈就绪（Phase 2 C0 第一步）。

## C1 第一刀 — 三类自动判定 + 估值分位/zone + E3 已完成（2026-07-22）

`stockagent/tracker/stock_diagnose.py` + `config/params.yaml` stock 段 + `tests/test_stock_diagnose.py`(19 纯函数测试)。全套 **267 passed**。

**实盘 sanity(观察池 5 只,全部数据已回填)**:
| 股票 | 主类/次类 | 关键特征 | 评 |
|---|---|---|---|
| 茅台 600519 | value | revCAGR+10.5% pePct4% divYld3.97% | ✅ 低 PE 分位·稳·高分红 |
| 招行 600036 | value | pe6.4 pb0.85 divYld偏高 | ✅ 破净深价值银行 |
| 格力 000651 | value | rev-3.5% pe7.8 高分红 | ✅ 成熟高分红 |
| 宁德 300750 | cyclic/+growth+value | profVol0.60 profCAGR+33% | ✅ 锂周期·次类保住成长信号 |
| 中芯 688981 | cyclic | profVol0.71 profCAGR-25% pe271 | ✅ 半导体周期·利润谷底 |

**复用(零重复)**:`engine.indicators.percentile_rank`(估值分位) + `tracker.indicators.{deviation_extremes,trend_state,breakout_grade,is_choppy}`(E3/趋势/突破) + `classifier.VALID_STYLES`(输出形状)。

**分类逻辑**(决策树,纯函数 `classify_stock(features, params)`):周期(利润 YoY 增速 std≥0.40,结构信号优先) > 成长(营收/净利 CAGR≥15%) / 价值(股息率≥3% 或 PE分位≤30%)。价值成长双触发按股息率高低定主。阈值全在 `params.yaml stock.classify`(可调)。

**已知 wart(已写进 docstring,C2/B1 再精算)**:`stock_dividend_yield` 偏高——① raw 价被历年分红压低(项目 adjust=raw)② 分红节奏切换年 TTM 多吃 1 次。分类器鲁棒(价值也经干净 PE 分位识别),不影响当前标签。

**C1 S07 利润来源归因 — 已完成（2026-07-22）**：`profit_attribution(p0,pe0,p1,pe1,div)` 纯函数 + `_as_of` + `diagnose_attribution(symbol,store,t0,t1)`。EPS 由 P/PE(TTM) 反推(避财报 TTM 滚动),三段可加(业绩=EPS增长 / 估值=价格回报−业绩残差 / 分红=期间每股现金÷P0),raw 价成立(除息日已在价格路径)。share 仅 total>0 给。
- 实盘验证(茅台 2020-2024):业绩 **+70.7%** 但 估值 **−94.4%**(PE 52→23 砍半)→ 总 −15.6%——典型「业绩涨估值杀」案例,S07 体感到位。
- ⚠️ 日价受 `history_years=6` 限,长持仓期归因需先扩 price 回填(baidu PE 有 IPO 起全史,只价是 6y)。
- 测试 +6,全套 **273 passed**。

**C1 S10 戴维斯双击/双杀 — 已完成（2026-07-22）**：`davis_signal(profit_yoy_latest,profit_yoy_prev,pe_change,pe_pct)` 纯函数 + `diagnose_davis(symbol,store)`。6 档:double_play(正增加速+PE扩张)/ double_play_setup(低PE+正增)/ double_play_watch(低PE+探底)/ double_kill(负增+PE收缩)/ double_kill_risk(高PE+负增)/ neutral。**关键:双杀是杀高估值,PE 已在低位(pe_lo)时不判 kill→归 watch(探底/双击前夜)**。
- 实盘验证:中芯=戴维斯双击(净利 +36% vs −23% + PE +75%,半导体周期反转);宁德/招行=双击买点;茅台/格力=双击观察(低 PE + 业绩探底)。
- 净利 YoY 用年报(期末 1231);PE 变化=今 vs pe_change_lookback_days 前;PE 分位=baidu 全史。阈值在 `params.yaml stock.davis`。
- 测试 +8,全套 **281 passed**。

**C1 S08 避坑(3/4) — 已完成（2026-07-22）**：`disclosure_deadline(period)` + `is_disclosed_by(period,asof)`(G2 公告时间差,法定最晚披露日保守上界防 lookahead) + `growth_quality(base,latest,prev_base)`(异常高增速≥150% / 低基数腰斩 → 标 abnormal,可信增速改用 2y CAGR 纠基数幻觉) + `diagnose_pitfalls(symbol,store,config,asof)`。阈值 `params.yaml stock.pitfalls`。
- 实盘验证:观察池 5 只全「正常」(大盘股无低基数/暴增,无误报);合成 [100,10,100] → +900% 被标异常,可信增速=0(2y CAGR 揭示没真涨)。
- 签名 `(symbol,store,config=None,asof=None)` 与其他 diagnose_* 一致(config 第 3 位参)。
- 测试 +8,全套 **289 passed**。
- **剩 S08 第 4 项「业绩预告链 G1」** 需补 `stock_yjyg/yjb`(预告/快报)数据源,后做。

**C1 S08-G1 业绩预告链 — 已完成（2026-07-22）**：`stock_forecast(symbol,report_period,yoy,type,announce_date,source)` 表 + `fetch_stock_forecast_panel(period)`(全市场 stock_yjyg_em,按 watchlist 过滤) + manager `update_stock_forecasts(periods)`(默认近 8 期) + backfill `--forecast`。诊断:`forecast_type_sentiment(type)`(多/空/中性) + `diagnose_forecast_chain(symbol,store)` → A1(预告 yoy 较上期下滑拐点)/ A2(类型多→空·戴维斯双杀前兆)/ G1(窗口)。
- 字段验证:`stock_yjyg_em` 预告类型 {预增/略增/续盈/扭亏=多, 预减/略减/首亏/续亏/增亏/减亏=空, 不确定},业绩变动幅度=yoy%,公告日期。**稀疏**——仅显著变动才发(观察池 5 只只 2 只有 2024 年报预告)。
- 测试 +6,全套 **295 passed**。

**C1 个股诊断全部完成 ✅**:three-class ✅ / 估值zone+E3 ✅ / S07 归因 ✅ / S10 戴维斯 ✅ / S08 避坑 4/4 ✅(公告时间差 G2 + 复合增速 + 异常高增速 + 预告链 G1)。`stock_diagnose.py` 是完整的个股诊断库,295 测试全绿。

**下一步可选**:① C2 个股提醒(把诊断接 `tracker/alerts.py` 双通道:A3 营收下滑/G1 披露窗口/G2 公告时间差/E3 偏离极值/E4 蓝筹vs成长背离 + 复用 A1/A2/D 筹码);② 看板/CLI(诊断结果可视化,目前只能 python -c 调)。

**C2 个股提醒(规则层)— 已完成（2026-07-22）**：`alerts.evaluate_stocks(stocks, index_diag, asof)` → A1/A2(预告链·复用 forecast_chain)+ A3(营收增速下滑>5pp·营收是利润之母)+ G1(预告公告90天内=披露窗口开)+ G2(法定披露截止45天内临近)+ E3(偏离60日线极值≤5%/≥95%·套利)+ E4(蓝筹vs成长背离·市场级)。阈值常量在 alerts.py 顶部。复用 `format_for_push`(双通道同源)。
- `stock_diagnose.collect_stock_alerts(symbols,store,index_diag,asof,names)` 装配入口(跑 diagnose_stock+pitfalls+forecast_chain → evaluate_stocks,lazy import alerts 防循环)。
- 实盘验证:茅台 A3(营收 16%→−1% 转负)、中芯 A3(28%→16% 减速)——真实基本面预警。format_for_push 出 `(⚠2 💡0)`。
- 测试 +9,全套 **304 passed**。
- **C2 规则层闭环**;剩「交付通道」:接进个股报告脚本(跑 collect_stock_alerts → 看板告警区 + 微信 --push-alerts),属「看板/CLI」步。

**Phase 2 总进度**:C0/C0.5/C0.6 数据栈 ✅ + C1 诊断全四块 ✅ + C2 提醒规则层 ✅。剩「看板/CLI」把诊断+提醒可视化 + 接推送通道。

**交付通道(看板/CLI + 推送)— 已完成(2026-07-22)**:`tracker/stock_report.py`(卡片式 HTML 渲染,告警区 + 个股卡片:分类/估值zone/戴维斯/关键指标/避坑/预告/E3,深浅色可切)+ `scripts/stock_report.py` CLI(--codes/--as-of/--push-alerts)+ `stock_diagnose.diagnose_stock_full` 打包全套诊断。`--push-alerts` 复用 `notify.broadcast`(WeCom/Feishu/PushPlus)+ `alerts.format_for_push`,未配置时优雅降级。
- 实跑验证:`python scripts/stock_report.py` → `data/stock_diagnose.html`(双击可看)+ 控制台摘要(5 只观察池分类/戴维斯/zone/提醒标记);茅台·中芯 A3 触发。
- 测试 +4,全套 **308 passed**。

## 🎉 Phase 2 个股层 — 全部完成

| 层 | 内容 | 状态 |
|---|---|---|
| **C0/C0.5/C0.6 数据栈** | price/估值(baidu PE·PB)/财报(17 指标)/分红/业绩预告 | ✅ |
| **C1 诊断** | 三类判定 / 估值zone+E3 / S07 归因 / S10 戴维斯 / S08 避坑4/4 / 预告链 | ✅ |
| **C2 提醒** | evaluate_stocks(A1/A2/A3/G1/G2/E3/E4) + collect_stock_alerts | ✅ |
| **交付** | stock_report.py 看板 + console + --push-alerts 微信双通道 | ✅ |

`stock_diagnose.py`(诊断库)+ `alerts.evaluate_stocks`(提醒)+ `stock_report.py`(看板)+ `backfill_stock_data.py`(数据)+ `stock_report.py`(报告)成完整闭环。**308 测试全绿**。观察池 5 只(茅台/招行/宁德/格力/中芯)实盘验证通过,信号合理(茅台·中芯营收 A3、中芯戴维斯双击、宁德 cyclic·次类成长)。

## C0 数据栈 — 已完成（2026-07-22）

**抓取层**（`stockagent/data/fetcher.py`）：
- `_szsh_prefix` bug 已修：旧 `5→sh, else→sz` 对个股全坏（上证主板 6xx/科创板 68x/B股 9xx 误判 sz）→ 新按首位分流 `5/6/9→sh, 0/1/3→sz, 8/4→bj`，**3 个 ETF 调用者行为不变**（测试守住）
- `fetch_stock_daily(symbol, adjust, start, end)` → `(df, source_tag)`：sina `stock_zh_a_daily`，复用 `_normalize` 出标准 OHLCV，prefix 自动、北交所拒收。**注意**：`start/end` 空串会让 sina 内部 DatetimeIndex 切片报错，故仅在显式给值时传入（省略=拉全历史）
- `fetch_stock_valuation(symbol, indicator)` → `df[date, value]`：百度金矿包装，indicator 经 `STOCK_VALUATION_INDICATORS` 白名单校验
- `STOCK_VALUATION_INDICATORS` = `{pe_ttm, pe_static, pb, pcf, market_cap}`。**市销率(TTM) baidu 实测返回 None → 故意排除**；无股息率（另从 `stock_history_dividend` derive，待 C1）

**字段验证结论**（探针：茅台 600519 + 招行 600036，2026-07-21）：
- `stock_zh_valuation_baidu` 列固定 `[date, value]`；5 指标全得
- `period='全部'` 从 **IPO 起**（茅台 2001-08-31）到最新，但**稀疏**（~600 点/25 年，半月级非日频）→ 够算历史分位，不能当日频价序列
- `stock_zh_a_daily` 列：`date/open/high/low/close/volume/amount/outstanding_share/turnover`（`_normalize` 裁成 6 列）；prefix 实测 sh600519/sh688981/sz000001/sz300750 全通，错前缀 sz600519 → KeyError

**入库层**（`store.py` + `manager.py`）：
- `stock_valuation(symbol,date,indicator,value,source)` 新表 + `upsert/get_series/last_date`（主键含 indicator，同日多指标共存）
- 个股日线**复用 `daily_prices`**（symbol TEXT，upsert_prices/get_series 现成；与 ETF 同表不同 symbol）——不新建表
- `DataManager.update_stock_daily(symbols)` → daily_prices（增量游标 `_backfill_start` + basis 守卫 `is_basis_consistent`，与 ETF 同）；`update_stock_valuation(symbols)` → stock_valuation（百度全量返回 → 全量幂等 upsert，无游标）
- `STOCK_WATCHLIST` = [600519 茅台 / 600036 招行 / 300750 宁德 / 000651 格力 / 688981 中芯]（C0 联调用，C1 迁 `config/stock_pool.yaml`）
- 脚本 `scripts/backfill_stock_data.py [--daily|--val|--codes]`（幂等，仿 `backfill_index.py`）

**测试**：`tests/test_stock_data.py`（10 个：prefix 个股/ETF/bj + 指标集 + store 往返/幂等/多指标/日期过滤）。全套 **242 passed**，零回归。

**已验证联调**：`python scripts/backfill_stock_data.py --codes 600519` → 日线 1453 行（2020-07..2026-07，history_years=6）+ 5 指标各 607 行（IPO 起）入库成功。

**约定**：`data.adjust='raw'`（全项目 ETF+个股同，sina 不复权 + fix_splits）；个股 fix_splits 尚未单独适配（大市值观察池少拆分，且 PE/PB 走 baidu 不受影响，C1 视需要再补）。

## C0.5 财务数据层 — 已完成（2026-07-22）

C0 只有比率(PE/PB),C1 的三类判定/利润归因/避坑/戴维斯都要**底层财务绝对值**。C0.5 补齐。

**字段验证结论**（探针：茅台 600519 + 招行 600036，2026-07-22）：
- `stock_financial_abstract(symbol)` → 透视表(选项/指标 + 报告期列)。7 个选项组,常用指标 **17 项,跨股名稳定**(茅台=招行 完全一致)。102 报告期(~25 年,IPO 起)。
- 常用指标含 C1 全部所需:营业总收入/归母净利润/扣非净利润/基本每股收益/每股净资产/经营现金流量净额 + ROE/毛利率/净利率。营收·利润**原始元单位**(显示 ÷1e8 转亿);ROE/毛利率为百分数原值。
- `stock_history_dividend_detail(symbol, indicator='分红')` → 公告日期/送股/转增/派息/进度/除权除息日/...。**派息=每10股X元** → `cash_per_share=派息/10`(已验证:茅台 2024 全年 547.58/10=54.76元/股,一年 2 次)。只存 `进度=实施`。
- ⚠️ `stock_history_dividend(symbol=...)` 签名已废(不接受 symbol)→ 用 `stock_history_dividend_detail`。

**抓取层**（`fetcher.py`）：
- `STOCK_FINANCIAL_METRICS` = 17 项 EN→CN 映射(revenue/net_profit/np_deducted/eps/bvps/ocf/roe/gross_margin/...)
- `fetch_stock_financials(symbol)` → 长表 `[report_period, metric, value]`(melt 常用指标)
- `fetch_stock_dividend(symbol)` → indexed by ex_date: cash_per_share/stock_div_10/trans_10/announce_date(过滤 实施)

**入库层**（`store.py`）：
- `stock_financials(symbol, report_period, metric, value, source)` 长表(主键含 metric)。`upsert` / `get_series(metric)` / `get_panel(metrics=)` 宽表面板(C1 多指标便利读) / `last_period`
- `stock_dividend(symbol, ex_date, announce_date, cash_per_share, stock_div_10, trans_10, source)`(主键 ex_date)。`upsert` / `get_series` / `last_date`。空记录(无分红股)返回 0 不报错
- manager: `update_stock_financials` / `update_stock_dividend`(都全量幂等,sina 每次返回全历史)
- `backfill_stock_data.py` 加 `--fin` / `--div`

**测试 + 联调**：`test_stock_data.py` 现 16 个(+6: 指标集/财报往返/面板 pivot/幂等/分红往返/空记录)。全套 **248 passed**。`--codes 600519 --fin --div` 实跑:财报 1585 行(102 期×16 指标,商誉全 NaN 被剔)+ 分红 30 行入库。

**注意**:财报是**累计**口径(一季报=单季,中报=H1 累计,三季报=9M 累计,年报=全年)。C1 算增速/TTM 要按报告期类型拆单季(S08 两年复合增速、S07 TTM EPS 滚动法),`_tmp_cmb_valuation.py` 有 TTM 滚动拆解的现成范式可抄。
