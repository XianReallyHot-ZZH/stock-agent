# Phase 2（个股层）Handoff — 清理上下文后恢复用

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

## 恢复后的第一步
1. 读 `docs/PRD-A股综合跟踪工具.md` §7（个股层设计）+ `docs/EXECUTION_PLAN.md` §4（Phase 2 骨架）
2. 从 C0 开始：验证 `stock_zh_valuation_baidu` 字段 + 修 `fetch_stock_daily` prefix bug
3. 建 `stockagent/tracker/stock_diagnose.py`（个股诊断，复用 `tracker/indicators.py` + `tracker/classifier.py`）
