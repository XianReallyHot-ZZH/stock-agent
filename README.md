# stock-agent

个人用的 **A股板块轮动 ETF 决策助手**：用规则引擎替你执行"满仓最强 3 个板块 + 大盘不好就避险 + 单仓止损"的纪律，用大模型把信号翻译成每天早晨微信/飞书上的说人话报告，并追踪你的执行自律度。

> 设计依据见 [`DESIGN.md`](./DESIGN.md)（含 16 项决策溯源、架构图、V1 边界、路线图）。

---

## ⚠️ 当前状态（务必先读）

**两个信号均未通过严格决策门**（样本外），但性质不同（见 DESIGN §14/§15）：
- **momentum（动量，V1 基线）**：样本外崩盘（-15%/-53%），**过拟合**，已证伪。
- **reversion（均值回归，V2.1 新增）**：样本外**正收益 +5.5%、回撤仅 -9.4%（基准一半）、Sharpe 0.58**——**不过拟合、防御性强**，但收益追不上强牛市，仍 FAIL 严格门。是迄今最值得 shadow 跟踪的信号。

**切换信号**（可插拔）：编辑 `config/params.yaml` 的 `rotation.signal.name`（`momentum` | `reversion`）。两个信号共用同一引擎/回测/报告，A/B 干净。

**因此目前只能用 SHADOW（纸盘）模式**：读报告、跟踪纸面业绩、**不要投入真金白银**。reversion 作为"低回撤防御/纪律工具"可重点观察（契合路线 A）。

---

## 架构

```
数据层(AkShare多源→SQLite) → 规则引擎(择时/轮动/止损) → 大模型(写报告,零预测) → 推送(企业微信/飞书/PushPlus)
                                                              ↑
                                              自律度对账(目标 vs 实际持仓)
```

- **决策归规则引擎，解释归大模型**：模型不发明数字，只解释引擎已算出的结果。
- 引擎三层：① 大盘择时（沪深300 / 120日线 → 货币ETF避险）② 轮动（趋势门槛 + 多周期动量，每周五取前 K=3）③ 单仓 -8% 移动止损（每日）。
- 优先级硬规则：**止损 > 轮动 > 择时**。

---

## 安装

```bash
# Python 3.10+，建议用 conda 环境
pip install -r requirements.txt

# 复制环境变量模板，按需填写（LLM key、推送 webhook）
cp .env.example .env
```

`.env` 关键项（全部可选，留空则降级）：
- `GLM_API_KEY` / `GLM_MODEL=glm-4-flash`：报告"简评"用大模型；留空则用规则模板（零预测，同样可用）。
- `WECOM_BOT_KEY` / `FEISHU_BOT_URL` / `PUSHPLUS_TOKEN`：三选一或多选，看你每天看哪个 App。

**首次拉数据**（~20 只 ETF + 沪深300 + 货币ETF，约 1 分钟）：
```bash
python scripts/update_data.py
```

---

## 日常流程（两个定时任务）

| 时刻 | 任务 | 命令 |
|---|---|---|
| 每个交易日 15:30 后 | 收盘数据更新（幂等、自动回填） | `python scripts/run_eod.py` |
| 每个交易日 08:30 | 生成 + 推送晨报（基于前日收盘） | `python scripts/run_morning_report.py` |

用 **Windows 任务计划程序** 设这两个定时任务（程序：你的 python.exe，参数：脚本绝对路径，起始于：项目根目录）。**机器需在 08:30 / 15:30 在线**；漏跑会在下次开机时自愈补跑（幂等）。

晨报示例：
```
📊 2026-07-02 周四 早盘报告
🟢 今日动作：无变动，维持持有
📊 组合：风险开 · 持仓 半导体ETF(33%) 人工智能ETF(33%) 证券ETF(33%)
📈 大盘：沪深300 4.85 在120日线(4.77)，正常
🔍 简评：系统按动量排序选中…以上为规则输出，不含任何涨跌预测。
```

---

## SHADOW 纸盘仪式（M5，当前唯一推荐模式）

策略未过决策门前，用它观察、不实盘：

```bash
# 1) 标记开始观察日
python scripts/run_shadow.py --since 2026-07-01

# 2) 之后每天看纸面业绩（复用回测引擎，从观察日到今）
python scripts/run_shadow.py --report
```

建议**纸盘 ≥ 4–8 周**，覆盖至少一次周五轮动 + 一次止损/择时触发，确认报告在"有动作/无动作"两种状态下都合理、且策略纸面业绩可接受后，再小额实盘。

---

## 回测 + 决策门 + 调参（M2）

```bash
# 单次回测 + 决策门（更低回撤下不输沪深300 才算 PASS）
python scripts/run_backtest.py --start 2021-01-01 --end 2026-07-02 --plot data/equity.png

# 参数网格扫描（K / 大盘均线 / 止损% / 趋势门槛），输出 data/sweep_results.csv
python scripts/sweep_params.py
```

**调优方向**（V1 失血主因 = risk_on 时段追进随后反转的动量 + 止损被扫 + 高换手）：
- 加均值回归/反转成分（A股动量弱、反转强）。
- 降换手（拉长轮动周期、加"趋势确认"过滤减少假突破）。
- 放宽止损（扫描显示 12% > 8%）。
- 见 DESIGN §3 V2 待办：相对强度、资金流确认、政策面。

策略通过决策门后，再去掉 SHADOW、转入小额实盘。

---

## 自律度对账（M4）

系统维护"目标持仓"（规则说该持什么）。你反馈"实际持仓"，下一份晨报显示**自律度**：

```bash
# 我完全跟单了今天的报告
python scripts/record_actual.py --executed

# 或手输实际持仓（symbol:权重）
python scripts/record_actual.py 512480:0.5 562500:0.5
```

不对账 = 放弃度量自律。至少每周一次。

---

## 排障

- **`ProxyError` / `RemoteDisconnected`**：你装了本地代理（Clash 7890）。本程序**已自动**把国内数据/推送/LLM 域名加入 `NO_PROXY` 直连（见 `stockagent/netenv.py`）。若 eastmoney 仍间歇限流，fetcher 会自动回退到 **Sina 源**（`fund_etf_hist_sina`，完整历史、不复权）→ 再回退 **Baostock**。
- **数据是不复权(raw)**：当前默认经 Sina 取 raw 价（分红未复权）。绝对收益略偏低；策略与基准同源，相对结论不变。eastmoney 解封后重跑 `update_data.py` 自动升级 hfq。
- **控制台 emoji 乱码/报错**：`setup_logging` 已强制 stdout 为 UTF-8。若仍乱码，设环境变量 `PYTHONUTF8=1`。
- **ETF 代码核对**：`python scripts/verify_pool.py` 核对池子代码/名称/流动性，按需改 `config/etf_pool.yaml`。
- **测试**：`python -m pytest tests/`（35 个单测，覆盖引擎规则/回测指标/报告/状态）。

---

## 项目结构

```
stock-agent/
  config/         params.yaml(策略参数) · etf_pool.yaml(ETF池)
  stockagent/
    data/         多源 fetcher · SQLite store · 交易日历 · 自愈更新
    engine/       指标 · 动量 · 择时 · 止损 · 组合 · Engine编排
    backtest/     向量化回测 · 指标 · 决策门 · 参数扫描
    report/       LLM客户端 · 晨报生成(零预测)
    notify/       Notifier接口 · 企业微信/飞书/PushPlus
    scheduler/    jobs(eod/晨报) · 幂等自愈 runner
    state.py      目标/实际持仓 · 发送日志 · 自律度
    shadow.py     纸盘模式
    netenv.py     代理绕过
  scripts/        update_data · run_eod · run_morning_report · run_backtest · sweep_params · verify_pool · record_actual · run_shadow
  tests/          35 个单测
  DESIGN.md       产品设计文档(16决策+架构+路线图+回测结论)
```

## 许可
个人自用。**不构成投资建议**；回测只证伪、不证实，过往表现不代表未来收益。
