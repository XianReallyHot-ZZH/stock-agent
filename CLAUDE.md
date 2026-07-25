# stock-agent

A股板块轮动 ETF 决策助手。规则引擎出决策、大模型出解释、每日微信报告。

## 快速命令

```bash
# 开发
python -m pytest tests/ -q                    # 跑全部测试（333 个）
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
python scripts/research_report.py              # 生成 ETF 三类分类看板（价值/成长/周期 + 信号提醒 + 锚点导航）
python scripts/research_report.py --push-alerts  # 生成看板 + 推送信号提醒到微信（九条触发时）

# 指数择时层（tracker，Phase 1-B · 只读诊断，基于课程 S12-13）
python scripts/backfill_index.py            # 回填 6 宽基日线(含上证综指000001) + 沪深300 PE/PB + 全市场 PB + 两市成交额（幂等）
python scripts/index_timing_report.py       # 生成指数择时看板（data/index_timing.html，八 section，深浅色可切）
python scripts/validate_volume_bottom.py    # ⑧地量 event-study 深度报告（data/volume_bottom_study.html）

# 个股层（tracker，Phase 2 · 只读诊断，三类分类+归因+戴维斯+避坑+预告链）
python scripts/backfill_stock_data.py             # 回填观察池 个股日线/估值(baidu PE·PB)/财报/分红/业绩预告（幂等，--daily/--val/--fin/--div/--forecast）
python scripts/stock_report.py                    # 生成个股诊断看板（data/stock_diagnose.html，告警区+个股卡片+🤖AI评估，深浅色可切）
python scripts/stock_report.py --push-alerts      # 生成看板 + 推送个股信号提醒到微信（A1/A2/A3/G1/G2/E3/E4 触发时）
python scripts/stock_report.py --no-llm           # 跳过🤖AI评估的LLM调用，走规则模板兜底（秒级预览/无 key）

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
- **行业研究模块（`research/`）是只读旁路**：算 ETF 性价比（三类分类 Phase 1-A：价值=股息率+PE分位 / 成长=业绩+PE / 周期=筹码+趋势,板块PB无源→不估值待P2）。`scoring.py` 按 `etf_pool.yaml` 的 style 标签分流；三类分页看板 + 锚点导航。信号提醒（`tracker/alerts.py`）九条（D筹码×估值交叉/E1E2趋势/B1股息/A1A2业绩/F1大盘），双通道（看板告警区+微信 `--push-alerts`）。筹码相位用非单调 6 相位表（文章「末期见底」逻辑：兑现中段最空、深回撤+卖盘枯竭=见底最看多）
- **指数择时层（`tracker/`）是只读诊断旁路**：基于课程 S12-13 + 周期律/量价实证，算大盘估值开关（沪深300 同口径 PE+PB → 四档 zone，③带 PE/PB 时序图）·大小盘温差·蓝筹vs成长·60日线趋势/突破跌破/偏离极值·**⑦相对周期律**（创业板 vs 上证 点差在 5 年包络的位置 → 极点/中枢）·**⑧成交量地量监测**（两市成交额/MA250 → 地量 + 量底→价底 event-study，实证：仅时效成立、胜率无 edge），出本地交互式看板（`data/index_timing.html`，八 section，深浅色可切），**不喂交易引擎**
  - **突破/跌破 = 真穿越**：`last_ma_cross`(严格变号)+`fresh_cross_direction`(≤5 日内) + 偏离≥2%(grade≥2) 才算「有效突破/跌破」；仅在线上/下但无近期穿越 = 中性。`breakout_grade` 只给位置强度，**不是**突破事件。指数看板趋势表/信号区 + alerts E1/E2 + 个股卡 E3 三处一致
- **个股层（`tracker/stock_diagnose.py` + `stock_report.py` + `stock_commentary.py`，Phase 2）是只读诊断旁路**：个股级三类自动判定（增速→成长 / 高股息低PE→价值 / 利润波动→周期）+ 利润来源归因 S07（业绩/分红/估值三段，EPS 由 P/PE 反推）+ 戴维斯双击/双杀 S10（业绩方向×估值方向六档）+ 避坑 S08（公告时间差 G2·两年复合增速·异常高增速最小值分母·预告链 G1）。数据栈 C0/C0.5/C0.6（价格/百度 PE·PB/sina 财报17指标/分红/eastmoney 业绩预告）。`alerts.evaluate_stocks` 七条个股提醒（A1/A2/A3/G1/G2/E3/E4）双通道（看板告警区+微信）。出 `data/stock_diagnose.html`（告警区+个股卡片+点📊弹模态看 6 张时序图[价格+偏离/PE/PB/业绩同比/S07归因/分红]，深浅色可切），**不喂交易引擎**。时序图 Plotly 懒渲染（图数据 JSON 嵌入、点开才 newPlot，可扩展多股票）。**🤖 AI 评估按钮**（`tracker/stock_commentary.py`）：点卡片 🤖 弹模态看大模型基于「全量诊断 + 投资心法三类打法（value/growth/cyclic 分流）」生成的五段评估（估值/业绩与归因/择时位置/风险与避坑/行动建议+条件化买卖标签）——守门只禁纯涨跌预测（允许条件化动作建议，宽于 research 推送场景，因是用户主动点开的只读参考不喂引擎），LLM 并行调用、无 key/失败/含禁词走规则模板兜底（`--no-llm` 全走模板），文本 JSON 嵌入 + textContent 渲染（防 XSS）

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
- **指数择时数据**：6 宽基日线 `stock_zh_index_daily`（sina，含上证综指 000001=⑦基准）；沪深300 PE/PB `stock_index_pe/pb_lg`（legulegu，仅沪深300/上证50/中证500，**不支持**创业板指/科创50/上证综指）；全市场 PB `stock_a_all_pb`；两市成交额 baostock（sh.000001+sz.399001 的 amount 求和=两市，历史到 1991，**仅 ⑧ 用**）。存 `index_daily`/`index_pe`/`index_pb`/`market_pb`/`market_turnover` 表（指数日线独立于 `daily_prices`，不复用 ETF 复权族）
- **ETF 三类分类**（Phase 1-A）：`etf_pool.yaml` 的 `style`/`style_alt` 标签（人工标 value/growth/cyclic，主+次）；ETF 分红 `fund_etf_dividend_sina`（覆盖稀疏，容忍缺失）。周期型板块 PB 无数据源→不估值（待 Phase 2 个股 PB 加权补真值）
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
- 股息率：`stock_dividend_yield` 按最近分红节奏（季/半年/年，最近 3 次间隔中位数判定）取最近 N 次年化，**非** 365 天窗口求和——频率切换年（年付→半年付）窗口会多吃近 2 倍。价为 raw 不复权（只压低历史价，当前价=现价，分母正确）
