# stock-agent

个人用的 **A股板块轮动 ETF 决策助手**：用规则引擎替你执行纪律，用大模型把信号翻译成每天早晨微信/飞书上的说人话报告，并追踪你的执行自律度。

> 设计依据见 [`DESIGN.md`](./DESIGN.md)（16 项决策溯源、架构图、V1 边界、路线图）。
> 项目开发规范见 [`CLAUDE.md`](./CLAUDE.md)（快速命令、架构、代码风格）。

---

## ⚠️ 当前状态

**6 个信号已验证**（walk-forward 样本外，校正数据 + 全 bug 修复后）：

| 信号 | OOS 年化 | 回撤 | Sharpe | 定位 |
|---|---|---|---|---|
| **value_flow** | **+5.86%** | **-7.41%** | **0.79** | **最优风险调整（推荐 shadow）** |
| momentum_sf | +10.05% | -18.4% | 0.58 | 高收益型 |
| momentum | +10.90% | -28.4% | 0.56 | 回撤偏大 |
| reversion | +0.80% | -3.3% | 0.25 | 极低回撤保本型 |
| share_flow | +2.61% | -21.2% | 0.25 | 机构动向 |
| bb_macd | +0.98% | -11.6% | 0.17 | 温和型 |
| 基准(沪深300) | +15.51% | -16.3% | — | — |

全部仍未通过严格决策门（收益 < 基准 +15.51%），但 **value_flow 的回撤条件已满足**（-7.41% < -16.25%），Sharpe 最高。推荐 value_flow 进 shadow 观察。

---

## 架构

```
数据层(AkShare多源→SQLite→拆分修正)
  → 规则引擎(大盘择时 A+B / 可插拔信号 / 双层止损)
  → 大模型(写报告,零预测)
  → 推送(企业微信/飞书/PushPlus)
                              ↑
              自律度对账(目标 vs 实际持仓)
```

- **决策归规则引擎，解释归大模型**：模型不发明数字，只解释引擎已算出的结果。
- 引擎分层：① 大盘择时（沪深300 band+confirm → 货币ETF避险）② 信号（6 种可插拔）③ 止损（ATR / 双层 / entry_stop）。
- 优先级硬规则：**止损 > 轮动 > 择时**。

---

## 安装

```bash
pip install -r requirements.txt
cp .env.example .env   # 按需填 LLM key + 推送 webhook
python scripts/update_data.py          # 首次拉数据（~1分钟）
python scripts/backfill_scale.py --start 2021-01-01  # 回填 ETF 份额（~10分钟）
python scripts/fix_splits.py           # 修拆分（运行一次）
python -m pytest tests/                # 验证（93 个测试）
```

---

## 日常使用（3 个命令）

| 时刻 | 命令 | 说明 |
|---|---|---|
| 每日 15:30 | `python scripts/run_eod.py` | 收盘数据更新（幂等） |
| 每日 08:30 | `python scripts/run_morning_report.py` | 生成 + 推送晨报 |
| 每周 | `python scripts/record_actual.py --executed` | 对账自律度 |

用 **Windows 任务计划程序** 设这两个定时任务。

---

## 信号切换

编辑 `config/params.yaml`：
```yaml
rotation:
  signal:
    name: value_flow   # momentum | reversion | bb_macd | share_flow | momentum_sf | value_flow
```

---

## 研究工具

```bash
python scripts/run_backtest.py --start 2021-01-01 --plot data/equity.png    # 单次回测
python scripts/sweep_params.py                                                 # 参数扫描
python scripts/walk_forward.py                                                 # 样本外验证
python scripts/backtest_report.py --signal value_flow --output report.html      # HTML 详细报告
python scripts/plot_shares.py                                                  # ETF 份额+净值交互图
python scripts/audit_pool.py                                                   # 审计池子缺什么
python scripts/verify_pool.py                                                  # 核对代码有效性
```

---

## 项目结构

```
stock-agent/
  config/         params.yaml(策略参数) · etf_pool.yaml(ETF池)
  stockagent/
    data/         多源 fetcher · SQLite store · 交易日历 · 拆分修正 · 份额管理
    engine/       指标 · 6种信号 · 大盘择时(RegimeFilter A+B) · 止损 · 组合 · Engine编排
    backtest/     向量化回测 · 指标 · 决策门 · 参数扫描
    report/       LLM客户端 · 晨报生成(零预测)
    notify/       Notifier接口 · 企业微信/飞书/PushPlus
    scheduler/    jobs(eod/晨报) · 幂等自愈 runner
    state.py      目标/实际持仓 · 发送日志 · 自律度
    netenv.py     代理绕过
  scripts/        update_data · run_eod · run_morning_report · run_backtest · sweep_params
                  walk_forward · walk_forward_multi · backfill_scale · fix_splits · plot_shares
                  backtest_report · verify_pool · audit_pool · record_actual · run_shadow
  tests/          93 个单测
  CLAUDE.md       开发规范
  DESIGN.md       产品设计文档(16决策+架构+路线图+回测结论)
  .env            API keys/webhook(gitignored)
```

## 许可
个人自用。**不构成投资建议**；回测只证伪、不证实，过往表现不代表未来收益。
