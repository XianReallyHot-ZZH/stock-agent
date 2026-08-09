# stock-agent

A股板块轮动 ETF 决策助手。规则引擎出决策、大模型出解释、每日微信报告。

## 快速命令

```bash
# 开发
python -m pytest tests/ -q                    # 跑全部测试（456 个）
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
python scripts/research_report.py --push-alerts  # 生成看板 + 推送信号提醒到微信（十类触发时，含周期反转 R1）

# 指数择时层（tracker，Phase 1-B · 只读诊断，基于课程 S12-13）
python scripts/backfill_index.py            # 回填 6 宽基日线(含上证综指000001) + 沪深300 PE/PB + 全市场 PB + 两市成交额（幂等）
python scripts/index_timing_report.py       # 生成指数择时看板（data/index_timing.html，八 section，深浅色可切）
python scripts/validate_volume_bottom.py    # ⑧地量 event-study 深度报告（data/volume_bottom_study.html）

# 个股层（tracker，Phase 2 · 只读诊断，三类分类+归因+戴维斯+避坑+预告链）
python scripts/backfill_stock_data.py             # 回填观察池 个股日线/估值(baidu PE·PB)/财报/分红/业绩预告 + 商品价(周期上游)（幂等，--daily/--val/--fin/--div/--forecast/--comm）
python scripts/stock_report.py                    # 生成个股诊断看板（data/stock_diagnose.html，告警区+个股卡片，深浅色可切；🤖按钮点击时实时生成AI评估）
python scripts/stock_report.py --push-alerts      # 生成看板 + 推送个股信号提醒到微信（A1/A2/A3/G1/G2/E3/E4/Q1/P1/M1/M2 触发时）
python scripts/ai_eval_server.py                  # 🤖AI评估本地服务（首个长驻·127.0.0.1:8765，看板🤖按钮点击时实时调LLM生成；先起它再点🤖）

# 西方宏观预测台账（western_macro · Phase 3 只读旁路 · ADR-0001）
# 两面: ①预测台账(claim→edge打分,度量预测者) ②宏观框架看板(北向目标·纯数据沿因果链跟踪分析,不再抠命中率)。永不喂引擎
python scripts/backfill_western_macro.py                          # 回填 UST/美股/外盘期货/外汇 + 6腿重算 DXY（幂等；外汇 push2his 本机重试）
python scripts/extract_western_claims.py --since 2026-08-01       # LLM 抽 DRAFT claim（幂等·跳过已抽；--force 重抽保 confirmed 状态）
python scripts/western_macro_report.py --extract                  # 结算到期 claim + 渲染 data/western_macro.html（看板 only·无微信）
python scripts/macro_framework_report.py                          # 渲染宏观框架看板 data/macro_framework.html（纯数据跟踪·沿因果链 利率→曲线→美元→金属→能源→权益·无claim/台账）
python scripts/backfill_gold_micro.py                             # 回填黄金微观紧缺数据(COMEX库存/CFTC非商业投机持仓/央行购金)→专表（幂等·只读 ADR-0001）
python scripts/backfill_economic_calendar.py                      # 回填经济日历(近7天已公布+未来45天排期·美国重要性≥2·数据真伪+催化剂)→专表

# 实盘
python scripts/run_morning_report.py --force   # 生成+推送晨报
python scripts/record_actual.py --executed     # 对账自律度

# 视频转文字（standalone 工具 · 不碰引擎/DB；详见 .claude/skills/video-transcribe/SKILL.md）
python scripts/transcribe_video.py --url "<视频链接>"                       # B站/YouTube/抖音 通用 → 文字稿（faster-whisper large-v3；首次下模型 ~3GB，走 ModelScope 国内 CDN）
python scripts/transcribe_video.py --url "<BV>" --cookie "<SESSDATA>"       # 充电视频/字幕快路径（B站 AI字幕秒级取，覆盖<80%判串台→自动回退 Whisper）
python scripts/transcribe_video.py --url "<链接>" --no-subtitle --device cpu # 强制 Whisper / 强制 CPU
```

## 架构

```
数据层(fetcher/store/manager) → 规则引擎(择时/信号/止损) → 报告(LLM/推送)
```

- **决策归规则引擎，解释归大模型**：模型不发明数字，只解释引擎已算出的结果
- 信号可插拔（`engine/signals/`），通过 `rotation.signal.name` 切换
- 大盘择时层（RegimeFilter A+B）是最高优先级
- **行业研究模块（`research/`）是只读旁路**：算 ETF 性价比（三类分类 Phase 1-A：价值=股息率+PE分位 / 成长=业绩+PE / 周期=筹码+趋势,板块PB无源→不估值待P2）。`scoring.py` 按 `etf_pool.yaml` 的 style 标签分流；三类分页看板 + 锚点导航。信号提醒（`tracker/alerts.py`）十类（D筹码×估值交叉/E1E2趋势/B1股息/A1A2业绩/F1大盘/R1周期反转），双通道（看板告警区+微信 `--push-alerts`）。筹码相位用非单调 6 相位表（文章「末期见底」逻辑：兑现中段最空、深回撤+卖盘枯竭=见底最看多）。**周期反转筛子**（`research/cyclical.py`，只读）：cyclic ETF 的「业绩同比×前期回撤×财报时效」综合分(0-100)，周期页「🔁反转候选」表 + R1 提醒（≥60 触发；案例=锂矿深跌+业绩爆发，时效=下个业绩窗口前须兑现）；**不喂引擎**（etf_earnings 无时点历史→回测前视偏差）
- **指数择时层（`tracker/`）是只读诊断旁路**：基于课程 S12-13 + 周期律/量价实证，算大盘估值开关（沪深300 同口径 PE+PB → 四档 zone，③带 PE/PB 时序图）·大小盘温差·蓝筹vs成长·60日线趋势/突破跌破/偏离极值·**⑦相对周期律**（创业板 vs 上证 点差在 5 年包络的位置 → 极点/中枢）·**⑧成交量地量监测**（两市成交额/MA250 → 地量 + 量底→价底 event-study，实证：仅时效成立、胜率无 edge），出本地交互式看板（`data/index_timing.html`，八 section，深浅色可切），**不喂交易引擎**
  - **突破/跌破 = 真穿越**：`last_ma_cross`(严格变号)+`fresh_cross_direction`(≤5 日内) + 偏离≥2%(grade≥2) 才算「有效突破/跌破」；仅在线上/下但无近期穿越 = 中性。`breakout_grade` 只给位置强度，**不是**突破事件。指数看板趋势表/信号区 + alerts E1/E2 + 个股卡 E3 三处一致
- **个股层（`tracker/stock_diagnose.py` + `stock_report.py` + `stock_commentary.py`，Phase 2）是只读诊断旁路**：个股级三类自动判定（增速→成长 / 高股息低PE→价值 / 利润波动→周期）+ 利润来源归因 S07（业绩/分红/估值三段，EPS 由 P/PE 反推）+ 戴维斯双击/双杀 S10（业绩方向×估值方向六档）+ 业绩含金量（归母 vs 扣非背离 → 一次性利润/纸面富贵识别，挂戴维斯 `quality_warning`；Tier-1：扣非按定义已剔除投资收益/公允价值，**险企投资收益进扣非→漏判**，待 Tier-2 投资收益占比）+ 提前埋伏筛选器（`positioning_score`：深跌×业绩拐头×含金量×未兑现×企稳 → 0-100 埋伏分,看板「🎯提前埋伏候选」表 + P1 提醒；策略=领先基本面(深跌+最新已报期业绩拐头)提前埋伏、财报兑现即离场；**不要求站上60日线**(那是滞后已兑现),改用企稳因子(近60日走平/回升=满分、急跌飞刀=重罚0.25不归零)防飞刀；预告=兑现出场,非入场)。**A 类领先信号**（`tracker/leading.py`,非价格·预判下期业绩):强形式周期=上游商品价(`commodity_price` 表,futures_zh_daily_sina 碳酸锂/铜/螺纹钢/黄金/原油,日频)→ `commodity_map` 映射;集成进 `earnings_outlook=commodity_alignment`(商品健康度×股价落后度=错杀度;替代旧 max(拐头,商品价))。看板「🧲商品A类面板」(5商品×同比/近60日/判定 向上·背离·向下 + 板块指引)+ M1 背离告警(商品同比涨但近期回落→减仓/卖出)+ M2 向下告警(同比转负→周期确认向下,卖出)。日频聪明钱(北上/主力资金)akshare 端点停滞/被拦,弱形式退到待补+ 避坑 S08（公告时间差 G2·两年复合增速·异常高增速最小值分母·预告链 G1）。数据栈 C0/C0.5/C0.6（价格/百度 PE·PB/sina 财报17指标/分红/eastmoney 业绩预告）。`alerts.evaluate_stocks` 十一条个股提醒（A1/A2/A3/G1/G2/E3/E4/Q1业绩含金量/P1提前埋伏/M1商品背离/M2商品向下）双通道（看板告警区+微信）。出 `data/stock_diagnose.html`（**按类型分 tab:周期/价值/成长**(默认周期;周期 tab 含🧲商品A类面板+📈商品价时序图+🎯埋伏表,价值/成长 tab 本期只卡片、后续各自扩展);告警**按 tab 拆**(个股级→各 tab 顶部,市场级→tab 栏上方全局条)+个股卡片+点📊弹模态看时序图[**周期股首图=映射商品价**+价格+偏离/PE/PB/业绩同比/S07归因/分红]，深浅色可切），**不喂交易引擎**。时序图 Plotly 懒渲染（图数据 JSON 嵌入、点开才 newPlot，可扩展多股票）。**🤖 AI 评估按钮**（`tracker/stock_commentary.py` + `scripts/ai_eval_server.py`）：点卡片 🤖 时前端 fetch 本地服务 `ai_eval_server.py`（首个长驻进程·http.server 绑 127.0.0.1:8765、持 .env key）实时调 LLM 生成五段评估（估值/业绩与归因/择时位置/风险与避坑/行动建议+条件化买卖标签）——基于全量诊断 + 投资心法三类打法（value/growth/cyclic 分流，多类注入全部命中打法；**周期打法已更新为商品驱动**:商品价因果领先/alignment=错杀度/背离=减仓/预告=兑现；facts 注入商品A类信号——品种/同比/近60日/健康度/alignment/背离预警）；守门只禁纯涨跌预测，无 key/失败/含禁词走规则模板兜底，服务未开则 🤖 报错提示。**纯服务、无预计算嵌入**（看板生成不调 LLM，点击时按需单股调）
- **视频转文字（`scripts/transcribe_video.py` + `.claude/skills/video-transcribe`，standalone 只读工具）**：给视频链接 → 文字稿（yt-dlp 拉音频 + faster-whisper large-v3 greedy）。B站 带 SESSDATA 时先试 **AI字幕快路径**（字幕覆盖<80%判串台→自动回退 Whisper）。关键坑已固化：模型走 **ModelScope**（HF/xet 国内 500、hf-mirror 对 LFS 跳回 HF=0字节）；GPU 自动探测 cublas（缺则挂同机 conda env 的 torch/lib，仍不行→CPU int8 兜底）；`KMP_DUPLICATE_LIB_OK` 防 libiomp 重复崩。**不碰交易引擎/DB/stockagent 包**；产物落 `data/transcripts/`（gitignored）。只到出文字，后续分析另做

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
