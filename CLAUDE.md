# stock-agent

A股板块轮动 ETF 决策助手。规则引擎出决策、大模型出解释、每日微信报告。

## 快速命令

```bash
# 开发
python -m pytest tests/ -q                    # 跑全部测试（129 个）
python scripts/run_backtest.py                 # 单次回测（默认信号）
python scripts/sweep_params.py                 # 参数扫描（全部信号）
python scripts/walk_forward.py                 # 样本外验证
python scripts/backtest_report.py --signal value_flow  # 详细 HTML 报告

# 数据
python scripts/update_data.py                  # 更新日线数据（幂等）
python scripts/backfill_scale.py --start 2021-01-01  # 回填 ETF 份额历史（SSE+SZSE）
python scripts/fix_splits.py                   # 修拆分（运行一次）
python scripts/plot_shares.py                  # 画份额+净值交互图（注意：净值轴=close价，旧bug保留）

# 行业研究（V3.1，只读·不碰引擎）
# 新机器/fresh clone 冷启动（DB 被 gitignore，需从零回填全部数据，~1hr；详见 .claude/skills/research-dashboard-setup/SKILL.md）
python scripts/setup_research_dashboard.py     # 一键：依赖检查+.env+价格+份额+净值+PE+渲染（幂等）
python scripts/setup_research_dashboard.py --skip-pe  # 快速预览：跳过~30min的PE回填（双因子排名照跑）
# 日常维护（数据已存在后；详见 .claude/skills/research-dashboard/SKILL.md）
python scripts/dashboard_data_check.py         # 查数据新鲜度（每只ETF的份额/净值/PE是否到最新交易日）
python scripts/dashboard_data_check.py --fix   # 自动补齐缺口到最新交易日（价格/份额/净值/PE）
python scripts/research_report.py              # 生成性价比 HTML 看板（data/research_report.html）

# 实盘
python scripts/run_morning_report.py --force   # 生成+推送晨报
python scripts/record_actual.py --executed     # 对账自律度
```

## 架构

```
数据层(fetcher/store/manager) → 规则引擎(择时/信号/止损) → 报告(LLM/推送)
```

- **决策归规则引擎，解释归大模型**：模型不发明数字，只解释引擎已算出的结果
- 信号可插拔（`engine/signals/`），通过 `rotation.signal.name` 切换
- 大盘择时层（RegimeFilter A+B）是最高优先级
- **行业研究模块（`research/`）是只读旁路**：算 ETF 行业性价比（估值PE分位+筹码动向+趋势），出本地 HTML 看板，**不喂交易引擎**。筹码相位用非单调 6 相位表（文章「末期见底」逻辑：兑现中段最空、深回撤+卖盘枯竭=见底最看多）

## 6 个可插拔信号

| 信号 | 入场逻辑 | OOS Sharpe | 文件 |
|---|---|---|---|
| **value_flow** | 低位+机构买入+企稳+双层止损 | **0.79** | `signals/value_flow.py` |
| momentum_sf | 动量+份额过滤 | 0.58 | `signals/momentum_sf.py` |
| momentum | 多周期动量 | 0.56 | `momentum.py` |
| reversion | RSI 超跌反弹 | 0.25 | `signals/reversion.py` |
| share_flow | 份额趋势 | 0.25 | `signals/share_flow.py` |
| bb_macd | 布林带+MACD | 0.17 | `signals/bb_macd.py` |

## 关键设计约束

- **T+1**：信号 T 收盘生成、T+1 开盘成交
- **数据源**：AkShare（eastmoney→sina→baostock 三源容错）；份额 SSE `fund_etf_scale_sse` + SZSE `fund_etf_scale_szse`（双源，深市不再缺历史）
- **行业研究数据**：单位净值 `fund_etf_fund_info_em`（真NAV，天然正确无需复权）；行业PE `stock_industry_pe_ratio_cninfo`（证监会行业，按日快照，历史~2023起约3年，cninfo 限流需重试）
- **不复权数据**：sina 原始价格，需 `fix_splits.py` 修拆分后使用（仅影响价格序列；真NAV不受影响）
- **决策门**：walk-forward 样本外 PASS（更低回撤 + 不输基准）才能用
- **A股交易日**：9:30-11:30 / 13:00-15:00；报告 8:30 前基于前日收盘

## 配置

- `config/params.yaml`：策略参数（K、动量窗口、止损、择时、各信号参数）
- `config/etf_pool.yaml`：ETF 池（~27 只精选板块 ETF，按规模选）
- `.env`：LLM key + 推送 webhook（gitignored）

## 代码风格

- 纯函数优先（信号层无副作用，所有计算在最后一根 K 线评估）
- 配置驱动（参数在 params.yaml，不在代码里硬编码）
- 每个新功能必须有 pytest 测试
- 回测和实盘共用同一引擎函数（score_universe / check_exits / decide_target）

## 数据质量注意

- 拆分修正：`fix_splits.py`（检测 >25% 单日跌幅/ >100% 单日涨幅）
- 持仓上限：回测严格 ≤K 持仓（已修 bug：rotated-out 标的必须 sell_all）
- 再平衡阈值：10%（避免每周微调产生的噪音交易）
