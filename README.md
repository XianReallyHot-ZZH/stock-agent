# stock-agent

![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Tests](https://img.shields.io/badge/tests-874%20passing-brightgreen.svg)
![Data](https://img.shields.io/badge/data-AkShare-orange.svg)
![Status](https://img.shields.io/badge/status-shadow%20only-lightgrey.svg)

> 一个 **A股** 板块轮动 ETF 决策助手：规则引擎出决策、大模型出解释、每日微信报告；并带六套只读诊断看板（ETF 行业研究 / 指数择时 / 个股诊断 / 宏观框架 / 仓位管理 / 候选个股池）。

**决策归规则引擎，解释归大模型**——模型不发明数字，只解释引擎已算出的结果。七套模块共用同一数据底座（AkShare 多源 → SQLite），其中后六套是**只读诊断旁路**，不参与交易引擎。

> 📐 设计溯源见 [`DESIGN.md`](./DESIGN.md)（16 项决策、架构图、路线图）；
> 🔧 开发规范见 [`CLAUDE.md`](./CLAUDE.md)（快速命令、架构、代码风格、数据质量注意）。

---

## 目录

- [✨ 功能特性](#-功能特性)
- [🧭 设计哲学](#-设计哲学)
- [🏗️ 架构](#️-架构)
- [🚀 快速开始](#-快速开始)
- [📊 六大模块](#-六大模块)
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
- **ETF 行业研究·择时跟踪看板**：每只 ETF 跟踪 ① 净值-MA60 偏离度（分位+第几极值）② 份额-净值剪刀差分化 ③ 筹码方向（份额申赎多窗口投票→偏离×筹码四象限提醒）④ 板块资金流向（行业级分组净申赎+增量vs存量分解+📡申赎异动横幅：窗口 1/3/6/12 月可切·今日/当时双口径·🌊连环潮汐聚类·下钻事件标记）⑤ 行业业绩预期（业绩预告广度 + 一致预期增速 g=指数官方成分×东财研报EPS + 预告→快报→正式报三环披露时钟 + 4周修正动量）；价值/成长/周期 真 tab 分页 + 表头排序 + 逐标的折叠明细（含业绩预期链状态条），深浅色可切，纯跟踪·不标买卖点。交互式 HTML。
- **指数择时层看板**：沪深 300 估值开关（同口径 PE+PB 四档）、大小盘温差、蓝筹 vs 成长、60 日线趋势 / 真穿越突破跌破 / 偏离极值；另含 ⑦ 相对周期律、⑧ 成交量地量、⑨ 恐惧贪婪指数（5 成分 0-100 温度计）、⑩ 关键位监测（规则选位支撑测试状态机；实证：破位后 20 日波动抬升）、⑪ 货币条件（M2/M1 同比 + 2 月动量拐点状态机；实证：公布滞后吃掉几乎全部触底回升 edge——温度计非开关）。
- **个股诊断看板**：三类自动判定 + S07 利润归因 + S10 戴维斯 + S08 避坑 + **A 类商品价领先信号**（碳酸锂/铜/螺纹钢/黄金/原油 → alignment 商品健康×股价落后度 → 埋伏分）+ **按类型分 tab**（周期含商品面板/时序图/埋伏表子 tab）+ 十一条信号提醒 + 🤖AI 评估（点击实时生成，周期打法含商品驱动逻辑）。
- **宏观框架看板**：沿 JZ 因果链（利率→曲线→美元→金属→能源→权益）做纯数据跟踪与分析；🥇**黄金阶段定位器**（价格结构+微观紧缺+利率美元驱动→阶段+驱动三栏+置信度+操作建议）；🔬黄金微观紧缺（COMEX 库存/CFTC 投机+商业持仓/央行购金/实际利率/期限溢价）；📰经济日历（美国高重要性事件·数据真伪 surprise+催化剂·对黄金影响）。
- **仓位管理看板**：估值档 × 预案表对照器——沪深300 PE+PB 10 年滚动分位四档逐日回放（2005 起，与指数看板 ④ 同口径）+ 档位统计（历史占比/前向 1y·3y 收益中位/年化波动）+ 切换事件台账 + 环境注记（恐贪/地量/关键位）；预案表 = `params.yaml position_plan` 用户自定义的档位→权益仓位%区间，看板只对照「现在在哪格」，温度计非开关·不喂引擎。
- **候选个股池看板**：研报覆盖池（~2300 只·consensus∩非ST）的**筛选漏斗**——六张策略表：①偏离超卖复合分（自身百分位触发+护栏+企稳，防飞刀）②业绩预期猛×深跌（周期=商品驱动+g确认 / 成长=三腿；披露窗口A全类型+B仅周期）③一致预期修正动量（个股版 E4）④PEAD 预告超预期（point-in-time 双腿无前视）⑤业绩变脸监测（跳档/趋势破位/连亏/拐头，陈老师方法论系统化）⑥戴维斯双击候选（stage-2 按需腿）；raw+分红运行时前复权防除息假极值；配 event-study 验证器（结论含无 edge 也注入读图说明）。
- **自律度对账**：记录目标持仓 vs 实际执行，量化自己的纪律。
- **纯函数 + 配置驱动 + 全测试覆盖**（874 个 pytest），回测与实盘共用同一引擎函数。

---

## 🧭 设计哲学

1. **决策归规则引擎，解释归大模型**——模型零预测，只翻译引擎输出。避免 LLM 幻觉造数。
2. **六套诊断看板是只读旁路**——研究/择时/个股诊断/宏观框架/仓位管理/候选个股池**不喂交易引擎**，只帮你「看清」，决策仍归引擎。
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
  │                                    │   │   • 仓位管理     (tracker/)    │
  │   → 组合决策 → Engine 编排           │   │   → 5 套交互式 HTML 看板      │
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

# 五套看板所需（首次较久，之后增量；仓位管理看板零新增回填，直接用 backfill_index 的沪深300 PE/PB）
python scripts/setup_research_dashboard.py --skip-pe  # ETF 研究看板一键（价格+份额+净值+业绩预期底座+渲染；--skip-pe 省PE ~30min）
python scripts/backfill_index.py                      # 7 宽基日线(含上证综指·中证1000) + 沪深300 PE/PB + 全市场 PB + 两市成交额 + 上交所融资融券(⑨恐惧贪婪)
python scripts/backfill_stock_data.py                 # 观察池个股 日线/估值/财报/分红/预告
python scripts/backfill_stock_data.py --comm          # 商品价(碳酸锂/铜/螺纹钢/黄金/原油)

# 宏观框架看板所需（首次 ~5 分钟）
python scripts/backfill_western_macro.py               # UST/美股/外盘期货/外汇 + DXY 6腿重算
python scripts/backfill_gold_micro.py                  # COMEX库存/CFTC持仓/央行购金/FRED实际利率/NYFed期限溢价
python scripts/backfill_economic_calendar.py           # 经济日历(美国高重要性事件)
```

> 新机器冷启动（全套数据回填）约 1 小时，详见 [`.claude/skills/research-dashboard-setup/SKILL.md`](.claude/skills/research-dashboard-setup/SKILL.md)。

### 验证安装

```bash
python -m pytest tests/ -q          # 874 个测试全过即环境 OK
```

---

## 📊 六大模块

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

### 2. ETF 行业研究·择时跟踪看板

每只 ETF 跟踪 ① 净值-MA60 偏离度（当前偏离 + 历史百分位分位 + 第几极值）② 份额-净值剪刀差分化 ③ 筹码方向（份额申赎 5/10/20/30/60 日投票 → 偏离×筹码四象限提醒）④ 板块资金流向（行业级 25 组净申赎 + 增量vs存量分解 + 📡申赎异动横幅：窗口 1/3/6/12 月可切（默认 1 月）+ 今日尺度/当时口径双口径（当时=point-in-time·长窗⊇今日尺度）+ ≥3月窗按ETF汇总/🌊连环潮汐行 + 点条目下钻日度申赎图带▲▼事件标记）⑤ **行业业绩预期**（业绩预告 bull/bear 广度 + 一致预期增速 g=指数官方成分权重×东财研报EPS 次年/当年−1·研报数≥3·覆盖权重门40% + 明细面板「⛓业绩预期链」三环披露时钟（预告→快报→正式报+快报落点±10pp）+ 4周一致预期修正动量（周度快照差分·冷启动4周·A5下修告警只提醒））。纯跟踪、不标买卖点、人决策。顶部「偏离度极端区」横幅（超卖绿/超买红）+ 价值/成长/周期 真 tab 分页排名（记住上次选择·表头点击排序含预期g）+ 逐标的明细折叠面板（默认收起·置顶展开·摘要 chips·下拉快速跳转·回顶部；懒渲染、rangeslider + 快捷窗口按钮），深浅色可切。置顶 ETF 改 `config/params.yaml` 的 `research.pinned_etfs`。

```bash
python scripts/research_report.py             # 生成 data/research_report.html（纯可视化，无 LLM/无告警推送）
python scripts/dashboard_data_check.py --fix  # 查/补数据新鲜度（价格/份额/净值；成分/一致预期快照/三环链 周度自动；PE 不必补）
```

### 3. 指数择时层看板

沪深 300 估值开关（同口径 PE+PB 四档 zone）、大小盘温差、蓝筹 vs 成长仓位倾向、60 日线趋势 / **真穿越突破跌破**（近 5 日真正穿越 60 日线 + 偏离≥2% 才算「有效」，非「在线上」）/ 偏离极值；另含 ⑦ 相对周期律（创业板 vs 上证点差包络位置）、⑧ 成交量地量监测、⑨ 恐惧贪婪指数（动量/流动性/波动率/估值/杠杆 5 成分 → 0-100 复合，温度计非开关）、⑩ 关键位监测（平台顶+前低规则选位 → 支撑测试状态机 + 下/上第一档；实证：破位·未收后 20 日波动抬升、胜率无 edge，温度计非开关；配套 A股观点预登记台账 `docs/CLAIMS_LEDGER.md`）。

```bash
python scripts/index_timing_report.py         # 生成 data/index_timing.html（十一 section（含 ⑨恐惧贪婪·⑩关键位·⑪货币条件），深浅色可切）
```

### 4. 个股诊断看板

个股级三类自动判定 + S07 利润归因（业绩/估值/分红三段）+ S10 戴维斯 + S08 避坑 + 预告链 + **A 类商品价领先信号**（`commodity_alignment`：商品健康度×股价落后度 → 埋伏分）+ 业绩含金量（扣非背离→一次性利润识别）+ 十一条个股提醒（含 M1 商品背离/M2 商品向下/P1 提前埋伏）；**按类型分 tab**（周期/价值/成长，周期内再分商品周期/其他周期子 tab）；**点卡片 📊 弹模态看时序图**（周期股首图=映射商品价 + 价格+偏离/PE/PB/业绩/S07 归因/分红，Plotly 懒渲染）；**🤖 AI 评估**（点击实时调 LLM 生成五段评估，周期打法含商品驱动逻辑）。

```bash
python scripts/stock_report.py                # 生成 data/stock_diagnose.html
python scripts/stock_report.py --push-alerts  # 生成 + 推送个股提醒（A1/A2/A3/G1/G2/E3/E4/Q1/P1/M1/M2 触发时）
```

### 5. 宏观框架看板（Phase 3 · 北向目标）

沿 JZ《西方经济》因果链（利率→曲线→美元→金属→能源→权益）做**纯数据跟踪与分析**，不再纠结预测准不准。旗舰 = 🥇**黄金阶段定位器**：价格结构（MA60+偏离分位→5 阶段）+ 微观紧缺（COMEX 库存/CFTC 投机+商业持仓/央行购金）+ 利率美元驱动 → `bull_intact`（央行购金+期限溢价/实际利率趋势）判底层 → 阶段精炼（牛市回调 vs 终局底）+ 置信度 + 条件化操作建议（双向防守）。另含 📰**经济日历**（美国高重要性事件·公布 vs 预期 surprise + 未来 45 天催化剂 + 对黄金影响结论）。数据含 FRED 实际利率/通胀预期 + NY Fed ACM 期限溢价（非-AkShare 免费源）。

```bash
python scripts/macro_framework_report.py          # 生成 data/macro_framework.html（纯数据·无LLM·深浅色可切）
# 只刷宏观一个看板（~3 分钟）：详见 .claude/skills/macro-dashboard/SKILL.md
# 或全刷 5 个看板：python /dashboards（详见 .claude/skills/dashboards/SKILL.md）
```

### 6. 仓位管理看板（第五看板 · 只读对照）

**估值档 × 预案表对照器**：沪深300 同口径 PE+PB 10 年滚动分位 → 四档（低位·可激进 / 中位·中性 / 结构分化·宜观望 / 高位·宜保守，与指数看板 ④ 估值开关同口径、parity 锁死）**逐日回放**（2005 年起，原 ④ 只有当前快照）+ 档位统计（历史占比 / 前向 1y·3y 收益中位 / 年化波动，逐日样本·末端不足窗口丢弃）+ 档位切换事件台账（只记事实不做涨跌复盘）+ 环境注记 chips（⑨恐贪/⑧地量/⑩关键位，只参照不改档）。**预案表 = 你在 `config/params.yaml` 的 `position_plan` 自定义的档位→权益仓位%区间**——看板只回答「现在在哪格、离哪条线多远」，不发明买卖建议（思想来源《股市仓位管理》主仓/超配分仓；温度计非开关·不喂引擎）。层级=大资产配置层（权益 vs 现金总比例），权益内轮动择时归引擎 RegimeFilter，两层不混。

```bash
python scripts/position_report.py               # 生成 data/position.html（数据腿=backfill_index.py 的 index_pe/pb，零新增回填）
```

### 7. 候选个股池看板（第六看板 · 只读筛选漏斗）

研报覆盖池（~2300 只）六策略筛选：①偏离超卖（自身百分位+护栏+企稳）②猛×深跌（分类型+披露窗口A/B）③修正动量 ④PEAD ⑤变脸监测 ⑥双击候选；raw+分红运行时前复权。冷启动一次性 ~2-3.5h（可断点续跑），此后日度增量由 `dashboard_data_check --fix` 自动带。

```bash
python scripts/backfill_stock_pool.py --all     # 冷启动：spot→行业→日线→分红（重跑=续跑）
python scripts/stock_pool_report.py             # 生成 data/stock_pool.html（--no-stage2 跳过候选腿）
python scripts/validate_deviation_extreme.py    # 策略1 实证（三臂 vs 基线，结论注入看板）
python scripts/validate_pead.py                 # PEAD 实证（两臂 vs 基线，结论注入看板）
```

---

## 🗓️ 日常使用

| 时刻 | 命令 | 说明 |
|---|---|---|
| 每日 15:30 | `python scripts/run_eod.py` | 收盘数据更新（幂等自愈） |
| 每日 08:30 | `python scripts/run_morning_report.py` | 生成 + 推送晨报（基于前日收盘） |
| 每周 | `python scripts/record_actual.py --executed` | 对账自律度 |
| 按需 | `python scripts/research_report.py` 等 | 刷新六套诊断看板（`/dashboards` 全刷 或 `/macro-dashboard` 只刷宏观） |

> A 股交易日 9:30–11:30 / 13:00–15:00；报告 8:30 前基于前日收盘。Windows 可用「任务计划程序」设这两个定时任务。

---

## 🔧 配置

| 文件 | 作用 |
|---|---|
| `config/params.yaml` | 策略参数（K、动量窗口、止损、择时、各信号参数、三类分类阈值） |
| `config/etf_pool.yaml` | ETF 池（36 只：行业映射 29 + 宽基/跨境/风格卫星 7，带 `style` 标签 + `index_code` 跟踪指数码表（业绩预期成分底座）+ 行业组 `group`；黄金已移除，由宏观框架看板跟踪） |
| `.env` | LLM key + 推送 webhook（由 `.env.example` 复制，**gitignored**） |

**LLM 可选**：留空则报告走模板回退。支持 DeepSeek / 智谱 GLM / OpenAI（按填的 `*_API_KEY` 自动识别，可用 `LLM_PROVIDER` 强制）。

---

## 📡 数据源

python -m pytest tests/ -q          # 874 个

数据为**不复权**原始价，需 `fix_splits.py` 修拆分后用于价格序列（真 NAV 不受影响）。

---

## 🧪 测试

```bash
python -m pytest tests/ -q          # 874 个
```

- **AkShare**（eastmoney → sina → baostock 三源容错）：日线、财报、分红、业绩预告、估值。
- ETF 份额：SSE `fund_etf_scale_sse` + SZSE `fund_etf_scale_szse`（双源）。
- 单位净值：`fund_etf_fund_info_em`（真 NAV，天然正确无需复权）。
- 行业 PE：`stock_industry_pe_ratio_cninfo`（证监会行业，按日快照）。
- 沪深 300 PE/PB：legulegu（仅沪深300/上证50/中证500，不支持创业板指/科创50）。
- 商品价：`futures_zh_daily_sina`（碳酸锂/铜/螺纹钢/黄金/原油 连续合约日线，A 类领先信号）。
- 上交所融资融券：`stock_margin_sse`（⑨恐惧贪婪·杠杆成分；深市总量历史不可得，仅沪市）。

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
│   ├── research/      ETF 行业研究·择时跟踪(偏离度/剪刀差/筹码/资金流向/业绩预期·看板渲染)
│   ├── tracker/       指数择时(S12-13) + 个股诊断(S07/S10/S08) + alerts + 看板渲染
│   ├── report/        LLM 客户端 · 晨报生成(零预测)
│   ├── notify/        Notifier 接口 · 企业微信/飞书/PushPlus
│   └── scheduler/     jobs(eod/晨报) · 幂等自愈 runner
├── scripts/           update_data · run_eod · run_morning_report · run_backtest ·
│                      sweep_params · walk_forward · backfill_scale · fix_splits ·
│                      research_report · index_timing_report · stock_report · ...
├── tests/             单测(874)
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
