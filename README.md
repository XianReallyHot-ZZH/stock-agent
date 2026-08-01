# stock-agent

![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Tests](https://img.shields.io/badge/tests-454%20passing-brightgreen.svg)
![Data](https://img.shields.io/badge/data-AkShare-orange.svg)
![Status](https://img.shields.io/badge/status-shadow%20only-lightgrey.svg)

> 一个 **A股** 板块轮动 ETF 决策助手：规则引擎出决策、大模型出解释、每日微信报告；并带三套只读诊断看板（ETF 行业研究 / 指数择时 / 个股诊断）。

**决策归规则引擎，解释归大模型**——模型不发明数字，只解释引擎已算出的结果。四套模块共用同一数据底座（AkShare 多源 → SQLite），其中后三套是**只读诊断旁路**，不参与交易引擎。

> 📐 设计溯源见 [`DESIGN.md`](./DESIGN.md)（16 项决策、架构图、路线图）；
> 🔧 开发规范见 [`CLAUDE.md`](./CLAUDE.md)（快速命令、架构、代码风格、数据质量注意）。

---

## 目录

- [✨ 功能特性](#-功能特性)
- [🧭 设计哲学](#-设计哲学)
- [🏗️ 架构](#️-架构)
- [🚀 快速开始](#-快速开始)
- [📊 四大模块](#-四大模块)
- [🗓️ 日常使用](#️-日常使用)
- [🔧 配置](#-配置)
- [📡 数据源](#-数据源)
- [🧪 测试](#-测试)
- [📁 项目结构](#-项目结构)
- [📈 策略现状（诚实）](#-策略现状诚实)
- [📚 文档](#-文档)
- [🤝 贡献](#-贡献)
- [⚠️ 免责声明](#️-免责声明)
- [📄 许可](#-许可)

---

## ✨ 功能特性

- **ETF 板块轮动决策引擎**：满仓最强 N 个板块 + 大盘不好就避险（货币 ETF）+ 单仓双层止损；6 种可插拔信号；每日早晨生成「说人话」报告并推送微信/飞书/PushPlus。
- **ETF 行业研究看板**：按价值/成长/周期三类分流算性价比，十类信号提醒（筹码×估值交叉、趋势突破、股息、业绩预告、大盘开关、周期反转），交互式 HTML。
- **指数择时层看板**：沪深 300 估值开关（同口径 PE+PB 四档）、大小盘温差、蓝筹 vs 成长、60 日线趋势 / 真穿越突破跌破 / 偏离极值。
- **个股诊断看板**：三类自动判定 + S07 利润归因 + S10 戴维斯 + S08 避坑 + **A 类商品价领先信号**（碳酸锂/铜/螺纹钢/黄金/原油 → alignment 商品健康×股价落后度 → 埋伏分）+ **按类型分 tab**（周期含商品面板/时序图/埋伏表子 tab）+ 十一条信号提醒 + 🤖AI 评估（点击实时生成，周期打法含商品驱动逻辑）。
- **自律度对账**：记录目标持仓 vs 实际执行，量化自己的纪律。
- **纯函数 + 配置驱动 + 全测试覆盖**（454 个 pytest），回测与实盘共用同一引擎函数。

---

## 🧭 设计哲学

1. **决策归规则引擎，解释归大模型**——模型零预测，只翻译引擎输出。避免 LLM 幻觉造数。
2. **三套诊断看板是只读旁路**——研究/择时/个股诊断**不喂交易引擎**，只帮你「看清」，决策仍归引擎。
3. **大部分早晨的报告是「维持持有，无需操作」**——挡住交易欲，这本身就是核心价值。
4. **优先级硬规则**：止损 > 轮动 > 择时。
5. **T+1**：信号 T 收盘生成、T+1 开盘成交。

---

## 🏗️ 架构

```
                    ┌──────────────────────────────────────┐
  AkShare/Baostock  │ ① 数据层（15:30 收盘后批量拉取）        │
  (eastmoney→sina   │   日线 OHLCV + 交易日历 + 份额/估值/财报 │
   →baostock 三源)  │   → SQLite（拆分修正、幂等 upsert）     │
                    └──────────────────┬───────────────────┘
                                       ▼
  ┌────────────────────────────────────┐   ┌─────────────────────────────┐
  │ ② 规则引擎（纯函数，决策归此层）      │   │ ②′ 只读诊断旁路（不喂引擎）    │
  │   大盘择时(RegimeFilter A+B)        │   │   • ETF 行业研究 (research/)  │
  │   × 6 可插拔信号                    │   │   • 指数择时    (tracker/)    │
  │   × 双层止损(ATR/entry_stop)        │   │   • 个股诊断    (tracker/)    │
  │   → 组合决策 → Engine 编排           │   │   → 交互式 HTML 看板          │
  └──────────────────┬─────────────────┘   └─────────────────────────────┘
                     ▼
  ┌────────────────────────────────────┐
  │ ③ 报告：大模型写「说人话」晨报(零预测) │
  │ ④ 推送：企业微信 / 飞书 / PushPlus    │
  └──────────────────┬─────────────────┘
                     ▲
  ┌────────────────────────────────────┐
  │ 自律度对账：目标持仓 vs 实际执行       │
  └────────────────────────────────────┘
```

---

## 🚀 快速开始

**前置**：Python ≥ 3.10（代码用了 `dict | None` 等 3.10+ 语法）。

```bash
git clone https://github.com/XianReallyHot-ZZH/stock-agent.git
cd stock-agent
pip install -r requirements.txt

cp .env.example .env          # 按需填 LLM key + 推送 webhook（可全留空，报告走模板回退）
```

### 首次拉数据（DB 被 gitignore，需从零回填）

```bash
# ETF 轮动引擎所需（~12 分钟）
python scripts/update_data.py                         # 日线（幂等，~1 分钟）
python scripts/backfill_scale.py --start 2021-01-01   # ETF 份额历史（~10 分钟）
python scripts/fix_splits.py                          # 修拆分（运行一次）

# 三套看板所需（首次较久，之后增量）
python scripts/setup_research_dashboard.py            # ETF 研究看板一键（价格+份额+净值+PE+渲染）
python scripts/backfill_index.py                      # 6 宽基日线(含上证综指) + 沪深300 PE/PB + 全市场 PB
python scripts/backfill_stock_data.py                 # 观察池个股 日线/估值/财报/分红/预告
python scripts/backfill_stock_data.py --comm          # 商品价(碳酸锂/铜/螺纹钢/黄金/原油)
```

> 新机器冷启动（全套数据回填）约 1 小时，详见 [`.claude/skills/research-dashboard-setup/SKILL.md`](.claude/skills/research-dashboard-setup/SKILL.md)。

### 验证安装

```bash
python -m pytest tests/ -q          # 454 个测试全过即环境 OK
```

---

## 📊 四大模块

### 1. ETF 轮动决策引擎（核心）

规则引擎出决策，大模型出解释，每日推送。默认信号 `value_flow`（风险调整最优，详见[策略现状](#-策略现状诚实)）。

```bash
python scripts/run_backtest.py --start 2021-01-01 --plot data/equity.png   # 单次回测
python scripts/walk_forward.py                                             # 样本外验证
python scripts/sweep_params.py                                             # 参数扫描
python scripts/backtest_report.py --signal value_flow --output report.html # HTML 详细报告
python scripts/run_morning_report.py --force                               # 生成 + 推送晨报
python scripts/record_actual.py --executed                                 # 对账自律度
```

切换信号：编辑 `config/params.yaml`
```yaml
rotation:
  signal:
    name: value_flow   # momentum | reversion | bb_macd | share_flow | momentum_sf | value_flow
```

### 2. ETF 行业研究看板

按 `etf_pool.yaml` 的 `style` 标签分流算性价比（价值=股息率+PE 分位 / 成长=业绩+PE / 周期=筹码+趋势），十类信号双通道（看板告警区 + 微信 `--push-alerts`）。

```bash
python scripts/research_report.py             # 生成 data/research_report.html
python scripts/research_report.py --push-alerts   # 生成 + 推送信号提醒（十类触发时）
python scripts/dashboard_data_check.py --fix  # 查/补数据新鲜度
```

### 3. 指数择时层看板

沪深 300 估值开关（同口径 PE+PB 四档 zone）、大小盘温差、蓝筹 vs 成长仓位倾向、60 日线趋势 / **真穿越突破跌破**（近 5 日真正穿越 60 日线 + 偏离≥2% 才算「有效」，非「在线上」）/ 偏离极值。

```bash
python scripts/index_timing_report.py         # 生成 data/index_timing.html（八 section，深浅色可切）
```

### 4. 个股诊断看板

个股级三类自动判定 + S07 利润归因（业绩/估值/分红三段）+ S10 戴维斯 + S08 避坑 + 预告链 + **A 类商品价领先信号**（`commodity_alignment`：商品健康度×股价落后度 → 埋伏分）+ 业绩含金量（扣非背离→一次性利润识别）+ 十一条个股提醒（含 M1 商品背离/M2 商品向下/P1 提前埋伏）；**按类型分 tab**（周期/价值/成长，周期内再分商品周期/其他周期子 tab）；**点卡片 📊 弹模态看时序图**（周期股首图=映射商品价 + 价格+偏离/PE/PB/业绩/S07 归因/分红，Plotly 懒渲染）；**🤖 AI 评估**（点击实时调 LLM 生成五段评估，周期打法含商品驱动逻辑）。

```bash
python scripts/stock_report.py                # 生成 data/stock_diagnose.html
python scripts/stock_report.py --push-alerts  # 生成 + 推送个股提醒（A1/A2/A3/G1/G2/E3/E4/Q1/P1/M1/M2 触发时）
```

---

## 🗓️ 日常使用

| 时刻 | 命令 | 说明 |
|---|---|---|
| 每日 15:30 | `python scripts/run_eod.py` | 收盘数据更新（幂等自愈） |
| 每日 08:30 | `python scripts/run_morning_report.py` | 生成 + 推送晨报（基于前日收盘） |
| 每周 | `python scripts/record_actual.py --executed` | 对账自律度 |
| 按需 | `python scripts/research_report.py` 等 | 刷新三套诊断看板 |

> A 股交易日 9:30–11:30 / 13:00–15:00；报告 8:30 前基于前日收盘。Windows 可用「任务计划程序」设这两个定时任务。

---

## 🔧 配置

| 文件 | 作用 |
|---|---|
| `config/params.yaml` | 策略参数（K、动量窗口、止损、择时、各信号参数、三类分类阈值） |
| `config/etf_pool.yaml` | ETF 池（~27 只精选板块 ETF，带 `style` 标签） |
| `.env` | LLM key + 推送 webhook（由 `.env.example` 复制，**gitignored**） |

**LLM 可选**：留空则报告走模板回退。支持 DeepSeek / 智谱 GLM / OpenAI（按填的 `*_API_KEY` 自动识别，可用 `LLM_PROVIDER` 强制）。

---

## 📡 数据源

python -m pytest tests/ -q          # 454 个

数据为**不复权**原始价，需 `fix_splits.py` 修拆分后用于价格序列（真 NAV 不受影响）。

---

## 🧪 测试

```bash
python -m pytest tests/ -q          # 333 个
```

- **AkShare**（eastmoney → sina → baostock 三源容错）：日线、财报、分红、业绩预告、估值。
- ETF 份额：SSE `fund_etf_scale_sse` + SZSE `fund_etf_scale_szse`（双源）。
- 单位净值：`fund_etf_fund_info_em`（真 NAV，天然正确无需复权）。
- 行业 PE：`stock_industry_pe_ratio_cninfo`（证监会行业，按日快照）。
- 沪深 300 PE/PB：legulegu（仅沪深300/上证50/中证500，不支持创业板指/科创50）。
- 商品价：`futures_zh_daily_sina`（碳酸锂/铜/螺纹钢/黄金/原油 连续合约日线，A 类领先信号）。

纯函数优先（信号层无副作用，所有计算在最后一根 K 线评估）；每个新功能必须有 pytest 测试；回测和实盘共用同一引擎函数（`score_universe` / `check_exits` / `decide_target`）。

---

## 📁 项目结构

```
stock-agent/
├── config/            params.yaml(策略参数) · etf_pool.yaml(ETF 池)
├── stockagent/
│   ├── data/          多源 fetcher · SQLite store · 交易日历 · 拆分修正 · 份额/估值/财报
│   ├── engine/        指标 · 6 种信号 · 大盘择时(RegimeFilter A+B) · 止损 · 组合 · Engine
│   ├── backtest/      向量化回测 · 指标 · 决策门 · 参数扫描
│   ├── research/      ETF 行业研究(三类分类·性价比·9 信号·看板渲染)
│   ├── tracker/       指数择时(S12-13) + 个股诊断(S07/S10/S08) + alerts + 看板渲染
│   ├── report/        LLM 客户端 · 晨报生成(零预测)
│   ├── notify/        Notifier 接口 · 企业微信/飞书/PushPlus
│   └── scheduler/     jobs(eod/晨报) · 幂等自愈 runner
├── scripts/           update_data · run_eod · run_morning_report · run_backtest ·
│                      sweep_params · walk_forward · backfill_scale · fix_splits ·
│                      research_report · index_timing_report · stock_report · ...
├── tests/             单测(454)
├── docs/              PRD · 执行计划 · 课程笔记 · Phase 交接
├── DESIGN.md          产品设计(16 决策 + 架构 + 路线图 + 回测结论)
├── CLAUDE.md          开发规范(命令 + 架构 + 代码风格 + 数据质量)
└── .env.example       API keys / webhook 模板
```

---

## 📈 策略现状（诚实）

6 个信号已做 walk-forward 样本外验证（校正数据 + 全 bug 修复后）：

| 信号 | OOS 年化 | 回撤 | Sharpe | 定位 |
|---|---|---|---|---|
| **value_flow** | +5.86% | **-7.41%** | **0.79** | 风险调整最优（推荐 shadow） |
| momentum_sf | +10.05% | -18.4% | 0.58 | 高收益型 |
| momentum | +10.90% | -28.4% | 0.56 | 回撤偏大 |
| reversion | +0.80% | -3.3% | 0.25 | 极低回撤保本型 |
| share_flow | +2.61% | -21.2% | 0.25 | 机构动向 |
| bb_macd | +0.98% | -11.6% | 0.17 | 温和型 |
| 基准(沪深300) | +15.51% | -16.3% | — | — |

**全部仍未通过严格决策门**（收益 < 基准 +15.51%），但 `value_flow` 回撤条件已满足（-7.41% < -16.25%）、Sharpe 最高、多窗口 walk-forward 稳健——已设为默认信号，**仅推荐 shadow 观察，未实盘**。

---

## 📚 文档

- [DESIGN.md](./DESIGN.md) — 产品设计（16 项决策溯源、架构图、V1 边界、路线图）
- [CLAUDE.md](./CLAUDE.md) — 开发规范（快速命令、架构、代码风格、数据质量注意）
- [docs/PRD-A股综合跟踪工具.md](./docs/PRD-A股综合跟踪工具.md) — 综合跟踪工具 PRD
- [docs/PHASE2_HANDOFF.md](./docs/PHASE2_HANDOFF.md) — Phase 2 交接
- `.claude/skills/` — 各看板的冷启动/刷新 skill 说明

---

## 🤝 贡献

欢迎提 Issue / PR。请：

1. 先跑 `python -m pytest tests/ -q` 确保全绿；
2. 新功能必须配 pytest 测试；
3. 遵循 [CLAUDE.md](./CLAUDE.md) 的代码风格（纯函数优先、配置驱动、不硬编码参数）；
4. 提交信息用约定式前缀（`feat:` / `fix:` / `docs:` / `refactor:` …）。

---

## ⚠️ 免责声明

> **本项目不构成任何投资建议。**

- 代码与回测仅用于**学习与研究**。回测**只能证伪、不能证实**；过往表现不代表未来收益。
- 信号尚未跑赢基准，**仅处于 shadow 观察阶段，未用于实盘**。据此交易风险自负。
- 数据来自第三方（AkShare 等），可能存在延迟、缺失或错误；不保证准确性。
- 请遵守所在地法律法规。

---

## 📄 许可

[Apache License 2.0](./LICENSE) © 作者。
