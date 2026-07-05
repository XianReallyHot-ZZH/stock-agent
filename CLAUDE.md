# stock-agent

A股板块轮动 ETF 决策助手。规则引擎出决策、大模型出解释、每日微信报告。

## 快速命令

```bash
# 开发
python -m pytest tests/ -q                    # 跑全部测试（93 个）
python scripts/run_backtest.py                 # 单次回测（默认信号）
python scripts/sweep_params.py                 # 参数扫描（全部信号）
python scripts/walk_forward.py                 # 样本外验证
python scripts/backtest_report.py --signal value_flow  # 详细 HTML 报告

# 数据
python scripts/update_data.py                  # 更新日线数据（幂等）
python scripts/backfill_scale.py --start 2021-01-01  # 回填 ETF 份额历史
python scripts/fix_splits.py                   # 修拆分（运行一次）
python scripts/plot_shares.py                  # 画份额+净值交互图

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
- **数据源**：AkShare（eastmoney→sina→baostock 三源容错）；份额用 SSE `fund_etf_scale_sse`
- **不复权数据**：sina 原始价格，需 `fix_splits.py` 修拆分后使用
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
