# stock-agent

A股板块轮动 ETF 决策助手。规则引擎出决策、大模型出解释、每日微信报告。

## 快速命令

```bash
# 开发
python -m pytest tests/ -q                    # 跑全部测试（925 个）
python scripts/run_backtest.py                 # 单次回测（默认信号）
python scripts/sweep_params.py                 # 参数扫描（全部信号）
python scripts/walk_forward.py                 # 样本外验证
python scripts/backtest_report.py --signal value_flow  # 详细 HTML 报告

# 数据
python scripts/update_data.py                  # 更新日线数据（幂等）
python scripts/backfill_scale.py --start 2021-01-01  # 回填 ETF 份额历史（SSE+SZSE）
python scripts/fix_splits.py                   # 修拆分（运行一次）
python scripts/fix_share_scale.py              # 修 fix_splits 份额前复权残留错位行（运行一次，2026-08 已跑）
python scripts/plot_shares.py                  # 画份额+净值交互图（注意：净值轴=close价，旧bug保留）

# 七看板总入口壳页（左侧导航 iframe 装载七看板 · data/index.html · 无数据依赖秒级）
python scripts/dashboard_home.py               # 生成 + 打开（七看板 HTML 各自生成后刷新即见；记住上次选择；绿点=今日/黄点=过期/灰点=未生成给命令）

# 候选个股池（pool，第六看板 · 只读筛选旁路 · 全覆盖池六策略漏斗，详见 docs/stock_pool/CONTEXT.md）
python scripts/backfill_stock_pool.py --all    # 冷启动：spot→consensus兜底→行业→日线(~1.5-3h断点续跑)→分红；日度增量由 dashboard_data_check --fix 自动带（~45-75min）
python scripts/stock_pool_report.py            # 生成 data/stock_pool.html（六表：①偏离超卖复合分 ②猛×深跌[窗口A/B] ③修正动量个股版 ④PEAD ⑤变脸监测 ⑥双击候选+stage-2；--no-stage2 跳过候选腿）
python scripts/validate_deviation_extreme.py   # 策略1 event-study（三臂 vs 基线 → data/deviation_extreme_study.html + 结论写 meta 注入看板读图说明）
python scripts/validate_pead.py                # PEAD event-study（surprise 两臂 → data/pead_study.html + 结论注入）
python scripts/validate_forecast_industry.py   # 预告行业选股 event-study（行业预喜率≥70%门+榜内yoy Top5→持有到法定截止近似·strat/ex_tk/b2/b1 嵌套四臂 vs 逐笔同窗等权基线 → data/forecast_industry_study.md；2026-08 首跑结论:绝对收益=β 超额中位为负、分层单调改善但无稳健 edge·2025H1 单期贡献几乎全部正超额,温度计非开关）

# 行业研究（只读·不碰引擎；择时跟踪看板）
# 新机器/fresh clone 冷启动（DB 被 gitignore，需从零回填；PE 已不用故 ~30min；详见 .claude/skills/research-dashboard-setup/SKILL.md）
python scripts/setup_research_dashboard.py --skip-pe  # 一键：依赖+.env+价格+份额+净值+渲染（PE 已不用，跳过省~30min；幂等）
# 日常维护（数据已存在后；详见 .claude/skills/research-dashboard/SKILL.md）
python scripts/dashboard_data_check.py         # 查数据新鲜度（每只ETF的份额/净值是否到最新交易日；PE 行可忽略）
python scripts/dashboard_data_check.py --fix   # 自动补齐缺口到最新交易日（价格/份额/净值；PE 不必补）
python scripts/research_report.py --backfill consensus  # 一致预期周度快照（E0·全市场~2300只落 stock_consensus；修正动量 E4 的历史积累，冷启动4周；详见 docs/EXECUTION_PLAN-ETF业绩预期.md）
python scripts/research_report.py --backfill chain      # 业绩三环链回填（E3·快报+正式报 8期全市场；预告面板随 --backfill earnings 落库；--fix 已周度自动带）
python scripts/backfill_constituents.py       # 指数成分+官方权重刷新（E1·中证官网月度快照→index_constituents；etf_pool.yaml 的 index_code/index_expect 码表+名称哨兵；月度节奏）
python scripts/research_report.py              # 生成 ETF 择时跟踪看板（偏离度+剪刀差+四象限提醒+板块资金流向+行业业绩预期[预告广度/一致预期g/三环链/修正动量]，价值/成长/周期 三类 tab；置顶 research.pinned_etfs）
# --push-alerts / --no-llm 已退役（仅可视化），保留 flag 向后兼容；改置顶 ETF 在 config/params.yaml 的 research.pinned_etfs

# 指数择时层（tracker，Phase 1-B · 只读诊断，基于课程 S12-13）
python scripts/backfill_index.py            # 回填 7 宽基日线(含上证综指000001·中证1000 000852) + 沪深300 PE/PB + 全市场 PB + 两市成交额 + 上交所融资融券(⑨恐惧贪婪·杠杆成分) + 货币条件 M2/M1/社融(⑪·金十月频)（幂等）
python scripts/index_timing_report.py       # 生成指数择时看板（data/index_timing.html，十一 section（含 ⑨恐惧贪婪·⑩关键位·⑪货币条件），深浅色可切）
python scripts/position_report.py           # 生成仓位管理看板（data/position.html，第五看板·估值档×预案表对照+档位统计+切换事件；数据腿=backfill_index.py 的 index_pe/pb 零新增；预案表在 params.yaml position_plan）
python scripts/validate_volume_bottom.py    # ⑧地量 event-study 深度报告（data/volume_bottom_study.html）
python scripts/validate_support_break.py    # 关键支撑位 event-study 深度报告（data/support_break_study.html · 平台顶+前低规则选位,守住vs破位的前向二阶矩:20日波动分离✔/回撤中位·胜率无edge · 温度计非开关）
python scripts/validate_m2_timing.py        # ⑪M2拐点 event-study 深度报告（data/m2_timing_study.html · 双口径防前视:理想lag0 vs 公布lag15——2026-08 首跑:触底回升 lag0 60日上涨概率86%→公布口径塌缩至57%中位+0.2%「等数据确认行情已走完」/下行确认33%最稳弱信号/见顶回落60%;n=9/7/5 温度计非开关;结论写 meta 活注入⑪读图说明）
# A股观点预登记台账 docs/CLAIMS_LEDGER.md（claim→可证伪定义→到期结算,给主流观点打分;配套上面的支撑位实证）

# 个股层（tracker，Phase 2 · 只读诊断，三类分类+归因+戴维斯+避坑+预告链）
python scripts/backfill_stock_data.py             # 回填观察池 个股日线/估值(baidu PE·PB)/财报/分红/业绩预告 + 商品价(周期上游)（幂等，--daily/--val/--fin/--div/--forecast/--comm）
python scripts/stock_report.py                    # 生成个股诊断看板（data/stock_diagnose.html，告警区+个股卡片，深浅色可切；🤖按钮点击时实时生成AI评估）
python scripts/stock_report.py --push-alerts      # 生成看板 + 推送个股信号提醒到微信（A1/A2/A3/G1/G2/E3/E4/Q1/P1/M1/M2 触发时）
python scripts/ai_eval_server.py                  # 🤖AI评估本地服务（首个长驻·127.0.0.1:8765，看板🤖按钮点击时实时调LLM生成；先起它再点🤖）

# 国内宏观看板（china_macro，第七看板 · 只读旁路 · 与宏观框架=海外宏观对称 · 规划见 docs/EXECUTION_PLAN-国内宏观.md）
python scripts/backfill_china_macro.py        # 回填 利率四腿(Shibor 2015起/FDR007定盘 2020-09起按年分段/LPR 1991起/中债期限结构 1990起) + 央行资产负债表(月频1993起,OMO/MLF余额)（幂等·金十源）
python scripts/china_macro_report.py          # 生成 data/china_macro.html（三 section：①货币信用=⑪完整版[M2/M1/剪刀差/社融脉冲/状态机+实证结论meta读] ②利率与流动性[FDR007/Shibor/LPR/10Y−2Y+OMO月度净投放近似] ③政策日历[政治局4/7/12月·中央经济工作会议·货政报告·两会·LPR·金融数据公布 下次时点+倒计时]；深浅色可切）

# 国内宏观看板（china_macro · 第七看板 data/china_macro.html · 只读旁路 · 与宏观框架=海外宏观对称 · 规划与端点真相见 docs/EXECUTION_PLAN-国内宏观.md）
- **`stockagent/china_macro/` + `scripts/china_macro_report.py`**：中国本土宏观观测层，三 section——①货币信用（M2/M1 同比+剪刀差+社融脉冲+episode 状态机+实证结论 meta 读=指数择时 ⑪ 的完整版）②利率与流动性（FDR007=DR 系定盘·央行政策目标利率区[2020-09 起]/Shibor[2015 起 8 期限]/LPR[1991 起]/中债期限结构 10Y−2Y[1990 起]+央行资产负债表「对其他存款性公司债权」月度差分=OMO/MLF 净投放滞后近似[1993 起·月频]）③政策日历（`policy.py` 纯函数·硬编码典型时点：政治局经济会议 4/7/12 月·中央经济工作会议·货政报告季度·两会·LPR 每月 20 日·金融数据公布每月 10-15 日→下次时点+倒计时；只放事实，观点结算归 docs/CLAIMS_LEDGER.md）。数据腿 `backfill_china_macro.py`（五表 shibor_daily/repo_fix_daily/lpr_monthly/cn_bond_daily/cb_balance_monthly，金十源幂等；FDR 按年分段拉；已并入 dashboard_data_check --fix[利率>2天·央行表>35天门控]）。**待补三项**（免费源缺，诚实标注不硬凑）：日度 OMO 净投放/票据转贴利率/国债发行明细（地方债明细有）。**纪律**：先行代理未过 event-study 礼遇前一律观察项、不出现「预测/信号」措辞（M2 拐点实证已示公布滞后吃掉几乎全部 edge）；**永不喂引擎；隔离要点**：china_macro→tracker 仅 import money_conditions/diagnose 纯函数（与 pool→tracker 同向），不 re-export

# 西方宏观预测台账（western_macro · Phase 3 只读旁路 · ADR-0001）
# 两面: ①预测台账(claim→edge打分,度量预测者) ②宏观框架看板(北向目标·纯数据沿因果链跟踪分析,不再抠命中率)。永不喂引擎
python scripts/backfill_western_macro.py                          # 回填 UST/美股/外盘期货/外汇 + 6腿重算 DXY（幂等；外汇 push2his 本机重试）
python scripts/extract_western_claims.py --since 2026-08-01       # LLM 抽 DRAFT claim（幂等·跳过已抽；--force 重抽保 confirmed 状态）
python scripts/western_macro_report.py --extract                  # 结算到期 claim + 渲染 data/western_macro.html（看板 only·无微信）
python scripts/macro_framework_report.py                          # 渲染宏观框架看板 data/macro_framework.html（纯数据跟踪·沿因果链 利率→曲线→美元→金属→能源→权益·无claim/台账）
python scripts/backfill_gold_micro.py                             # 回填黄金微观紧缺(COMEX库存/CFTC投机+商业持仓/央行购金实物/FRED实际利率+通胀预期/NYFed期限溢价)→专表
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
- **行业研究模块（`research/`）是只读旁路**：已从「性价比评估」转定位为 **ETF 择时跟踪**——纯跟踪、不标买卖点、人决策综合多看板。每只 ETF 跟踪 ① 净值-MA60 偏离度（`research/timing.py`：当前偏离 + 历史百分位分位 + 第几极值；偏离度纯函数复制自 `tracker/indicators.py` 做隔离，NAV 用 acc_nav 复权连续）② 份额-净值剪刀差分化（`scissor_divergence`：returns 口径、自适应窗口 20-120、双向、±5% 地板）③ 筹码方向（`chip_direction`：份额申赎 5/10/20/30/60 日近端等差加权投票（权重 5/4/3/2/1·阈值2·±1% 死区）→ 增/减/持平，机构行为代理；排名表筹码列含多窗口序列+悬停明细；另有「日申赎」列=最新交易日净申赎额(亿)+份额日增减%+**方向内**历史分位（`timing.latest_daily_flow`·当日脉搏·单日噪音大与筹码列的中期票互补·分位按方向各自统计：申购日比申购日/赎回日比赎回日·某方向样本<30日退绝对值双向·与📡横幅同式同源同日数字互证·着色≥95%红=净申购侧罕见大额/绿=净赎回侧）；与偏离度极端区交叉出「偏离度×筹码四象限提醒」横幅：超卖+增=🟢机会/超买+减=🟠风险/超卖+减=🔴严重警告/超买+增=🔵关注，持平不入格；排名表筹码列+明细 chips；**份额系指标（②③）共用 `split_adjusted_shares` 前复权前置**——份额×unit_nav 反向断崖检测拆分/份额折算（阈值 20%/25% 反号；分红无份额跳变不触发、巨额真实申赎净值正常波动不触发），原始份额跨拆分日会读出 +100% 假"申赎"污染最长 60 日筹码投票，2026-08 修复）④ 板块资金流向（份额视角，`research/flow.py` 纯函数：**行业级分组 25 组**=`etf_pool.yaml` 的 `group` 字段——每个行业独立、只合并真正相近的（银行1/券商保险2/医药2/恒生医疗1·跨境与大A分开/酒1/农业2/家电1/电子2/软件AI2/通信1/传媒1/机器人1/新能源3/有色1/煤炭1·商品周期不同源分开/中游材料3=钢铁+建材+化工/电力1/工程机械1/军工1/房地产1/汽车1/成长宽基2/港股科技2=恒科+中概·美股与港股分开/纳指1/红利1）；组级 20 日净流入滚动（亿元=Δ份额×当日 unit_nav·NAV 日历 ffill 对齐）时序图（25色·updatemenus 6态全量切换=窗口5/20/60日×绝对亿元/占**当日**组规模%——逐日分母 `flow.group_aum_series`（拆分前复权回退真实份额×unit_nav，防历史 AUM 高估2~4倍），单组时%曲线形状也与亿态不同；%态y轴刻度带%后缀；无状态按钮各带完整y数组+轴/图标题，默认20日·亿、初始视图1年）+ 增量vs存量分解 tile（净/毛轮动强度+流入广度→增量普涨/增量聚焦/存量轮动/净赎回/缩量观望，温度计非开关·阈值在 params research.flow）；**组×月热力图已移除**（2026-08：ROC 方差与组规模成反比，共享色标被小组高波动吃满，长历史视角由线图%态+全部范围覆盖；`flow.group_monthly_matrix` 纯函数留盘休眠）+ **📡 申赎异动横幅**（`flow.daily_flow_events`：**窗口可切 1/3/6/12月·默认1月 + 口径可切 今日尺度/当时口径**（2026-08 多窗改版：服务端一次扫 max(scan_windows)=250日×双口径——分位 as-of-today 全历史与窗口无关，各窗只是日期切片；report 层把各「口径×窗口」台账**预渲染成 HTML 字典**嵌 FLOW_EV JSON，客户端切窗口=换 innerHTML+条带图 relayout x 范围、切口径=restyle 双轨数据，零重算；台账 top-N 随窗放大 8/12/16/24；≥3月窗附「按ETF汇总」行=次数+累计净额看谁被持续申购/赎回 + **🌊连环潮汐行**（二期·`flow.cluster_flow_events`：同ETF同方向·间隔≤5交易日聚一潮=机构分批建仓/撤退脚印·按次数+|累计净额|排前6）；条带图嵌全量事件窗外自然裁掉；**当时口径**（二期·rank_mode="expanding"=point-in-time：事件日只用其之前历史算分位——「当时看来异常」的真历史，长窗下 ⊇ 今日尺度[早前事件被其后更极端流动挤出99%的，当时口径抓得回]）；**下钻标记**（二期·`flow_daily_figure` flow_events 参数：日度净申赎图柱区上方 ▲申购/▼赎回 固定标记轨·悬停带方向内分位·与横幅同源互证，从横幅点条目跳下来第一眼对上「那天发生了什么」）） 单 ETF 事件=日增减%≥自身**方向**历史99%分位（申购日比申购日·赎回日比赎回日·方向内幅度分位，2026-08 从绝对值口径改版——与排名表「日申赎」列统一·同日数字互证；某方向样本<30日退绝对值双向分位标 kind=abs）且 ≥1亿·金额地板滤小钱·min_history≥250·全历史分位·最新日带「最新」徽标·点条目跳该 ETF 日度申赎图·无命中常驻显示「安静窗口」占位（完全无 flow payload 才省略）·窗口配置 params research.flow.alert scan_days/scan_windows）；板块间"流向"是推断非观测（资金来源无标签·精选池代表性偏差·读图说明④注明）；组构成三处可见：组 chips 悬停显示成员代码 + 「行业组构成」可展开明细 + 排名表类型列组副行；无 group 字段或数据不足 → section 静默省略）⑤ 行业业绩预期（信息层·`research/earnings.py` 扩展：上=业绩预告聚合 bull/bear 广度、下=**一致预期增速 g**=Σ(etf_pool.yaml `index_code` 指数官方成分权重 × 东财研报 EPS 次年/当年−1)·研报数≥3·财年滚动对齐·周度整表快照 `stock_consensus` → **4周差分修正动量**（E4·同财年对齐防年末翻滚假信号·冷启动4周诚实显示累积中N/4·触发 A5 下修告警只 warn）·覆盖权重门 40%（房地产 38% 诚实拦下）·水平值口径非变化量；明细面板「⛓ 业绩预期链」状态条（预告→快报→正式报三环披露时钟+快报落点±10pp，E3）；顶部「📈 业绩预期提醒」横幅（2026-08·常驻占位·提醒区只收事件不收状态：A5=一致预期4周加权下修<-3%且覆盖≥40%·与 tracker/alerts.py A5 同口径三处同步；交叉=偏离极端分位×预告广度=🟠超买≥95%×空广度≥5%[单家小权重预亏是噪音] / 🟢超卖≤5%×预喜label·需 earnings_period 落本窗口；披露窗口门控 `earnings.disclosure_window` 时钟=开窗~截止+14天grace·窗口外只占位报下窗口时点[预告季度一跳·防壁纸化]；占位行承载时效状态：E4 冷启动进度N/4+还需周数/激活无命中/下窗口时点+上窗口命中摘要；预期g水平值=状态[横截面排序才有意义]不进横幅；**📖历史窗口台账**（折叠·同横幅底部）：`earnings.cross_hit_spans` 逐日 point-in-time 回放[预告按公告日截断·无前视]→每ETF连续命中区间，关闭窗结果冻结缓存 meta kv[earn_cross_replay_期]，开窗窗实时；**只记事实不含后续涨跌**[横幅不做荐股复盘·命中消失=价格脱离极端区即出口]；预告历史=`--backfill earnings --period 20251231,20260331` 逗号多期[全市场·8期已回填]，Store.forecast_period_counts 挑全市场级期[≥100行]）；`--backfill consensus/chain` 周度 / `backfill_constituents.py` 月度，均已并入 `dashboard_data_check --fix`；**INFORMATIONAL 永不喂引擎**）。`report.py` 渲染 `data/research_report.html`：顶部「偏离度极端区」横幅（超卖绿/超买红，A 股红=涨/超买·绿=跌/超卖）+「偏离度×筹码四象限」横幅 +「📈 业绩预期提醒」横幅（A5 下修+偏离×预告广度交叉·窗口门控·见⑤）+ 价值/成长/周期 真 tab 分页排名表（记住上次选择；表头点击排序 名称/类型/偏离/筹码/日申赎/预期/成交）+ 逐标的明细折叠面板（details 默认收起·置顶默认展开·summary 摘要 chips；份额净值图叠加剪刀差窗口 + 偏离度图带 ▲▼ 第几极值标记 + 日度净申赎图（`flow_daily_figure`：柱=净申赎额亿元=Δ份额×当日 unit_nav·拆分前复权防假巨柱·红申购/绿赎回 + 线=份额日增减%右轴·分母随时间变），IntersectionObserver 懒渲染 v2——滚动中只入队、停稳 ~130ms 后 rAF 分帧逐张画（渲染不与滚动争帧）+ render 800px/purge 3000px 滞回双窗口、rangeslider + 1月/6月/1年/3年/全部 快捷按钮、悬停年月日；顶部下拉快速跳转+右下回顶部）。深浅色可切（默认浅色，与其他三看板同模式；懒渲染下 toggle=purge+按新主题重画，图内「现在/第N低高」文字标签随主题 restyle）。置顶 ETF 在 `config/params.yaml` 的 `research.pinned_etfs`（默认 创业板159915/科创50 588000，逐标的明细最前并标 ⭐）。**已退役**：PE/三因子综合分（`scoring.py` 删）、周期反转筛子（`cyclical.py` 删）、LLM 解读（`commentary.py` 留盘休眠）、告警推送（`--push-alerts` no-op、不发微信）。**不喂交易引擎**；**隔离要点**：删 scoring 必须同步清 `research/__init__.py` 的 re-export，否则 `DataManager→research.earnings` 连锁崩掉其他三看板
- **指数择时层（`tracker/`）是只读诊断旁路**：基于课程 S12-13 + 周期律/量价实证，算大盘估值开关（沪深300 同口径 PE+PB → 四档 zone，③带 PE/PB 时序图）·大小盘温差·蓝筹vs成长·60日线趋势/突破跌破/偏离极值·**⑦相对周期律**（创业板 vs 上证 点差在 5 年包络的位置 → 极点/中枢）·**⑧成交量地量监测**（两市成交额/MA250 → 地量 + 量底→价底 event-study，实证：仅时效成立、胜率无 edge）·**⑨恐惧贪婪指数**（动量/流动性/波动率/估值/杠杆 5 成分各做 5 年滚动百分位 → 0-100 等权复合，温度计非开关·同 ⑧ 实证无 edge，**永不喂引擎**）·**⑪货币条件**（`tracker/money_conditions.py`：M2/M1 同比月度(金十 2008 起)·**2月动量 run 状态机**(连降 4 月=下行确认/大段后反向 2 月=触底回升·见顶回落；单月锯齿不打断——严格环比口径实测 18 年只出 7/2/1 事件太薄)·M1−M2 剪刀差(2024-01 M1 换新口径含个人活钱——断点注记·只展示不进研究)·社融脉冲(增量 TTM/M2 代理·存量同比无免费源)——「M2 定大盘」主流叙事的纯观测层；实证 `validate_m2_timing.py` **双口径防前视**：公布滞后吃掉几乎全部触底回升 edge（lag0 60日胜率 86%/中位+4.5% → 可交易 lag15 塌缩至 57%/+0.2%「等数据确认时行情已走完」）·下行确认 33% 最稳弱信号·见顶回落 60%；n=9/7/5 温度计非开关；结论写 meta `china_money_conclusion` 活注入 ⑪ 读图说明——tracker 看板首个 meta 读），出本地交互式看板（`data/index_timing.html`，十一 section，深浅色可切），**不喂交易引擎**。**关键支撑位实证**（`tracker/support_levels.py` + `validate_support_break.py`：平台顶+前低枢轴两类**规则选位**（无手画线），守住/破位·收回/破位·未收三结局，前向二阶矩从确认日起算对比无条件基准；结论：**破位确认后 20 日实现波动显著抬升（分离✔）**、回撤中位数与方向胜率不分离——支撑位=观察坐标/温度计非买卖信号；当前进行中的支撑测试自动列 pending，如 2026-07-17 上证 3766 回踩；已进看板 **⑩关键位监测** section：`monitor_snapshot` 状态机「回踩测试中/破位观察中(收回窗未走完)/已守住/假破·已收回/已破位·未收(·波动窗口内)」+ 下方第一支撑/上方第一压力 tile + 近2年价格+关键位横线图 + 三幕剧本提示）+ **A股观点预登记台账** `docs/CLAIMS_LEDGER.md`（主流话术→可证伪 claim→到期结算，对抗不可证伪叙事）
  - **突破/跌破 = 真穿越**：`last_ma_cross`(严格变号)+`fresh_cross_direction`(≤5 日内) + 偏离≥2%(grade≥2) 才算「有效突破/跌破」；仅在线上/下但无近期穿越 = 中性。`breakout_grade` 只给位置强度，**不是**突破事件。指数看板趋势表/信号区 + alerts E1/E2 + 个股卡 E3 三处一致
- **仓位管理看板（`tracker/position.py` + `scripts/position_report.py`，第五看板 data/position.html · 只读对照旁路）**：《股市仓位管理》（重远投资观·陈老师）主仓/超配分仓思想的环境侧落地——**估值档 × 用户预案表对照器**：看板只回答「现在在哪格、离哪条线多远」，不发明买卖建议、**永不喂引擎**。①逐日估值档回放 `valuation_zone_series`（沪深300 同口径 PE+PB 10 年滚动分位→四档 low/mid/split/high；判定语义与 `diagnose.diagnose_valuation` 完全对齐、parity 测试锁死；原函数只有当前快照，此为逐日序列补层；首 252 交易日留白=启动期分位不可靠）②预案表 `config/params.yaml position_plan`（档位→权益仓位%区间，**用户自定义**，示例默认值标注请修改；配置错误看板顶红条不静默）③档位统计 `zone_stats`（历史占比/前向 1y·3y 收益中位/前向年化波动，逐日样本·末端不足窗口丢弃不外推——仓库实证口径：**温度计非开关**，文章「高确定性/90% 回归」话术按自家 event-study 降权、仅作方法论出处注记）+ 档位切换事件台账（只记事实不做涨跌复盘）+ 环境注记 chips（⑨恐贪/⑧地量/⑩关键位·只参照不改档位）。层级=**大资产配置层**（权益 vs 现金总比例）；权益内轮动择时归引擎 RegimeFilter，两层不混（二期候选：持仓对账 actual_holdings/预案表回放 511990 现金腿/主超配拆分/切换推送）。数据腿=index_pe/index_pb/index_daily（backfill_index.py 已回填，零新增）；深浅色可切默认浅色
- **个股层（`tracker/stock_diagnose.py` + `stock_report.py` + `stock_commentary.py`，Phase 2）是只读诊断旁路**：个股级三类自动判定（增速→成长 / 高股息低PE→价值 / 利润波动→周期）+ 利润来源归因 S07（业绩/分红/估值三段，EPS 由 P/PE 反推）+ 戴维斯双击/双杀 S10（业绩方向×估值方向六档）+ 业绩含金量（归母 vs 扣非背离 → 一次性利润/纸面富贵识别，挂戴维斯 `quality_warning`；Tier-1：扣非按定义已剔除投资收益/公允价值，**险企投资收益进扣非→漏判**，待 Tier-2 投资收益占比）+ 提前埋伏筛选器（`positioning_score`：深跌×业绩拐头×含金量×未兑现×企稳 → 0-100 埋伏分,看板「🎯提前埋伏候选」表 + P1 提醒；策略=领先基本面(深跌+最新已报期业绩拐头)提前埋伏、财报兑现即离场；**不要求站上60日线**(那是滞后已兑现),改用企稳因子(近60日走平/回升=满分、急跌飞刀=重罚0.25不归零)防飞刀；预告=兑现出场,非入场)。**A 类领先信号**（`tracker/leading.py`,非价格·预判下期业绩):强形式周期=上游商品价(`commodity_price` 表,futures_zh_daily_sina 13种·碳酸锂/铜/铝/锌/螺纹钢/铁矿石/焦煤/黄金/白银/原油/玻璃/纯碱/生猪,日频)→ `commodity_map` 映射;集成进 `earnings_outlook=commodity_alignment`(商品健康度×股价落后度=错杀度;替代旧 max(拐头,商品价))。看板「🧲商品A类面板」(13商品×同比/近60日/判定 向上·背离·向下 + 板块指引)+ M1 背离告警(商品同比涨但近期回落→减仓/卖出)+ M2 向下告警(同比转负→周期确认向下,卖出)。日频聪明钱(北上/主力资金)akshare 端点停滞/被拦,弱形式退到待补+ 避坑 S08（公告时间差 G2·两年复合增速·异常高增速最小值分母·预告链 G1）。数据栈 C0/C0.5/C0.6（价格/百度 PE·PB/sina 财报17指标/分红/eastmoney 业绩预告）。`alerts.evaluate_stocks` 十一条个股提醒（A1/A2/A3/G1/G2/E3/E4/Q1业绩含金量/P1提前埋伏/M1商品背离/M2商品向下）双通道（看板告警区+微信）。出 `data/stock_diagnose.html`（**按类型分 tab:周期/价值/成长**(默认周期;周期 tab 含🧲商品A类面板+📈商品价时序图+🎯埋伏表,价值/成长 tab 本期只卡片、后续各自扩展);告警**按 tab 拆**(个股级→各 tab 顶部,市场级→tab 栏上方全局条)+个股卡片+点📊弹模态看时序图[**周期股首图=股价vs商品价双轴叠加·带rangeslider+1月/6月/1年/3年/全部快捷窗口+季度分界竖线(1/4/7/10月首日·年份线加重)**+价格+偏离/PE/PB/业绩同比/S07归因/分红]，深浅色可切），**不喂交易引擎**。时序图 Plotly 懒渲染（图数据 JSON 嵌入、点开才 newPlot，可扩展多股票）。**🤖 AI 评估按钮**（`tracker/stock_commentary.py` + `scripts/ai_eval_server.py`）：点卡片 🤖 时前端 fetch 本地服务 `ai_eval_server.py`（首个长驻进程·http.server 绑 127.0.0.1:8765、持 .env key）实时调 LLM 生成五段评估（估值/业绩与归因/择时位置/风险与避坑/行动建议+条件化买卖标签）——基于全量诊断 + 投资心法三类打法（value/growth/cyclic 分流，多类注入全部命中打法；**周期打法已更新为商品驱动**:商品价因果领先/alignment=错杀度/背离=减仓/预告=兑现；facts 注入商品A类信号——品种/同比/近60日/健康度/alignment/背离预警）；守门只禁纯涨跌预测，无 key/失败/含禁词走规则模板兜底，服务未开则 🤖 报错提示。**纯服务、无预计算嵌入**（看板生成不调 LLM，点击时按需单股调）
- **候选个股池（`stockagent/pool/` + `scripts/stock_pool_report.py`，第六看板 data/stock_pool.html · 只读筛选旁路）**：大池子**筛选漏斗**，区别于个股诊断看板的观察池逐只深挖（术语见 docs/stock_pool/CONTEXT.md：候选池=动态数据推导 ~2300 只 vs 观察池=人工策展 40 只）。**宇宙**=研报覆盖池（stock_consensus 最新快照 n_reports≥3）∩ 全市场 spot 快照非 ST（60/68/00/30 代码段，排北交所/B股）；行业三类粗分=东财行业板块成分月更（industry_member 表）× `config/stock_industry.yaml`（板块→{type, commodity}，未映射→type=None 诚实降级不入策略2，`--industry` 跑完打印未映射板块清单）。**复权核心**：raw 存储 + 分红表运行时前复权（`pool/prices.py` 交易所除权公式，factor 越界跳过+flag 宁可不调不加噪；`unexplained_cliffs` 嫌疑带=单日跌 0.8-8% 且 ±5 日无分红事件 → ⚠️徽标不入 stage-2、周更自愈；真崩盘带外照常触发）——S1/S2 一律吃调整后序列。**六表**：①偏离超卖复合分（`scoring.py`：自身 MA60 偏离历史百分位 ≤5% **且 cur_dev<0** 触发[稳态上行股 dev 恒定的并列退化防御] + 护栏三闸[预亏族预告/预期下修>3%/变脸向下，缺数据放行记flag] + 企稳加分[stabilize_factor 原语义]，0.6深度+0.4企稳；「新」徽标=超卖段起点≤5交易日）②业绩预期猛×深跌（`fierce.py`，独立于 P1 不复用乘法结构：深跌=250日回撤≥40%；周期猛=商品健康×股价落后[leading.commodity_alignment]+consensus g≥10%确认[纯研报g筛周期=买在预期顶]；成长猛=前瞻g分位/4周上修/已报增速加速三腿≥2[同尾比较防累计口径失真]；价值无猛；窗口A[预告/快报落地→正式报截止]全类型+窗口B[季中预估]仅周期[商品可观测]——`calendar.py` 补编码正式报法定截止 4/30·4/30·8/31·10/31，Q1 期窗口B 构造性为空）③一致预期修正动量个股版（`revision.py` E4 单股化，冷启动 N/4 诚实横幅）④PEAD 预告超预期（`pead.py` 双腿 leg C=年报期 consensus 隐含[公告日+公告日-365d 两份快照 point-in-time]/leg A=上年同期实际 np_yoy[公告早于本公告]，无前视边界测试钉死）⑤业绩变脸监测（`facechange.py` 陈老师方法论系统化：跳档≤-20pp/趋势破位3步-30pp/连亏3期/拐头+10pp，同尾序列；down 喂 S1 护栏）⑥戴维斯双击候选（stage-2：每策略 Top-30 候选周度按需拉 baidu 估值+sina 财报腿 → 复用 `diagnose_davis`，S1 排除除权嫌疑）。**验证器同批**：`validate_deviation_extreme.py`（三臂 raw/企稳/企稳+护栏 vs 无条件基线，expanding 分位防前视事件+120日冷却）、`validate_pead.py`（surprise≥+10/≤-10 两臂），结论（含无 edge）写 meta 注入看板读图说明——支撑位/地量的「温度计非开关」礼遇。数据腿：spot 日更单调用（stock_spot 表）/行业月更 45 天门/价格日度增量（update_stock_daily 复用，dashboard_data_check --fix 第 9 步 gate=pool_prices_ready，未冷启动打印命令不阻塞）/分红周更。深浅色可切默认浅色、单页锚点导航（六表交叉引用不藏 tab）、sortable 表。**永不喂引擎；隔离要点**：pool 不改 research/__init__ 不 re-export；纯函数仅 import research.timing/tracker 原语
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
- **指数择时数据**：7 宽基日线(含中证1000) `stock_zh_index_daily`（sina，含上证综指 000001=⑦基准）；沪深300 PE/PB `stock_index_pe/pb_lg`（legulegu，仅沪深300/上证50/中证500，**不支持**创业板指/科创50/上证综指）；全市场 PB `stock_a_all_pb`；两市成交额 baostock（sh.000001+sz.399001 的 amount 求和=两市，历史到 1991，**仅 ⑧ 用**）。存 `index_daily`/`index_pe`/`index_pb`/`market_pb`/`market_turnover`/`market_margin` 表（指数日线独立于 `daily_prices`，不复用 ETF 复权族）；⑪货币条件 = 金十 `macro_china_money_supply`(2008 起 223 月·全量重拉 upsert) + `macro_china_shrzgm`(社融增量·源滞后 2-3 月)存 `china_money_supply`/`china_tsf` 表（月频，每月中旬出上月值后 `backfill_index --money` 或 dashboard_data_check --fix 月度门控自动带）；⑨恐惧贪婪·杠杆成分 = 上交所融资余额 `stock_margin_sse`（按年分段拉，单次封顶 2000 行；深市总量历史 akshare 不可得，v1 仅沪市）
- **ETF 三类标签**：`etf_pool.yaml` 的 `style`/`style_alt`（人工标 value/growth/cyclic，主+次）仅作择时跟踪看板的分页分组（不再用于估值）；偏离度/剪刀差与类别无关。PE/PB/分红/筹码 已不在本看板使用
- **决策门**：walk-forward 样本外 PASS（更低回撤 + 不输基准）才能用
- **A股交易日**：9:30-11:30 / 13:00-15:00；报告 8:30 前基于前日收盘

## 配置

- `config/params.yaml`：策略参数（K、动量窗口、止损、择时、各信号参数）
- `config/etf_pool.yaml`：ETF 池（36 只：申万行业映射 29 + 宽基/跨境/风格卫星 7，按规模+流动性精选；黄金已移除——由宏观框架看板专门跟踪）
- `.env`：LLM key + 推送 webhook（gitignored）

## 代码风格

- 纯函数优先（信号层无副作用，所有计算在最后一根 K 线评估）
- 配置驱动（参数在 params.yaml，不在代码里硬编码）
- 每个新功能必须有 pytest 测试
- 回测和实盘共用同一引擎函数（score_universe / check_exits / decide_target）

## 数据质量注意

- 拆分修正：`fix_splits.py`（检测 >25% 单日跌幅/ >100% 单日涨幅）；份额序列的拆分连续性在计算时处理（`timing.split_adjusted_shares`，份额×unit_nav 反向断崖检测），`fix_share_scale.py`（run-once）修过 fix_splits 前复权只覆盖 25 散点日期导致的错位行（2026-08 已跑；后续日更写原始值，不再需要重跑）
- 持仓上限：回测严格 ≤K 持仓（已修 bug：rotated-out 标的必须 sell_all）
- 再平衡阈值：10%（避免每周微调产生的噪音交易）
- 股息率：`stock_dividend_yield` 按最近分红节奏（季/半年/年，最近 3 次间隔中位数判定）取最近 N 次年化，**非** 365 天窗口求和——频率切换年（年付→半年付）窗口会多吃近 2 倍。价为 raw 不复权（只压低历史价，当前价=现价，分母正确）
