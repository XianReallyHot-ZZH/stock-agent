# 指数择时层 Handoff — 清理上下文后恢复用

## ⚡ 快速恢复（先读这段，30 秒定位）

**状态（2026-08-25）**：指数择时看板 `data/index_timing.html` 现 **10 节**（⑪货币条件已于 2026-08-25 移至国内宏观看板①,纯核心/验证器留仓;历史沿革：③PE/PB图/⑦周期律/⑧地量 为 2026-07 批次：⑦`5ff9087`/⑧`78d4f14`/③图`e01b3de`；⑨恐惧贪婪 `9f9b6ae`；2026-08-15 新增 ⑩关键位监测 + 支撑位实证 + A股观点台账，宽基 6→7 只·增中证1000 `1879979`；**2026-08-24 曾增 ⑪货币条件 M2/M1/社融 + M2拐点双口径实证**）。全量测试绿（942）。

**6 条命令验证一切在跑**：
```bash
python -m pytest tests/ -q                     # 全绿(942)
python scripts/backfill_index.py               # 7宽基(含000001·000852)+沪深300PE/PB+全市场PB+两市成交额+货币条件(幂等)
python scripts/index_timing_report.py          # → data/index_timing.html(10节,~10MB)
python scripts/validate_volume_bottom.py       # → data/volume_bottom_study.html(⑧地量 event-study 深度报告)
python scripts/validate_support_break.py       # → data/support_break_study.html(⑩关键位 event-study 深度报告)
python scripts/validate_m2_timing.py           # → data/m2_timing_study.html(M2拐点 event-study 深度报告;结论注入国内宏观看板①)
```

**文件地图**：
| 角色 | 文件 |
|---|---|
| 指标(纯函数) | `stockagent/tracker/indicators.py`：⑦`relative_spread_series/cycle_extremes/classify_cycle/relative_momentum/consecutive_run/linear_fit_line`；⑧`turnover_percentile/turnover_dry_events/volume_bottom_stats/turnover_new_low_years` |
| ⑩关键位原语 | `stockagent/tracker/support_levels.py`：`detect_platforms`(平台顶) / `pivot_lows+low_retest_events`(前低) / `breakout_retest_events` / `merged_events`(合并去重) / `_resolve_outcome`(三结局判定) / `forward_risk_rows+group_summary+bootstrap_median_diff`(event-study) / `monitor_snapshot`(⑩看板数据·状态机) |
| 货币条件原语(留仓,消费方=国内宏观看板①) | `stockagent/tracker/money_conditions.py`：`_scan`(共用扫描:三臂事件+末态) / `m2_episode_events` / `episode_state+state_label` / `event_trade_date`(lag0/lag15 公布日对齐) / `scissor_series` / `tsf_pulse_series` |
| 诊断组装 | `stockagent/tracker/diagnose.py`：`diagnose_relative_cycle` / `diagnose_turnover` / `diagnose_money_conditions`(留仓,消费方=国内宏观看板①,已不挂 diagnose_layer) / 挂 `diagnose_layer`；`_binom_p_one_sided`；`BROAD_INDICES`(7只·顺序=看板展示序) |
| 看板渲染 | `stockagent/tracker/dashboard.py`：`_valuation_figure`(③) / `_relative_cycle_figure`(⑦) / `_turnover_figure`(⑧) / `_key_levels_figure+_key_levels_html`(⑩) / 各 `_section_html` + `render_index_timing` 接线 |
| 提醒 | `stockagent/tracker/alerts.py`：E5(⑦周期极点) / V1(⑧地量) —— **仅经 research/stock 推送通路触发**(index_timing_report.py 无 `--push-alerts`) |
| 数据 | `data/store.py`(`market_turnover` 表 + `index_daily` 含 000001/000852 + `china_money_supply`/`china_tsf` 月度表,国内宏观①数据腿) / `fetcher.py::fetch_market_turnover`(baostock) + `fetch_china_money_supply/fetch_china_tsf`(金十) / `manager.py::update_market_turnover` + `update_china_money` |
| 脚本 | `scripts/backfill_index.py` / `scripts/validate_volume_bottom.py` / `scripts/validate_support_break.py` / `scripts/validate_m2_timing.py`(货币条件实证,留仓) |
| 台账 | `docs/CLAIMS_LEDGER.md`：A股观点预登记(claim→可证伪定义→到期结算;Claim 001=3700~3800强支撑,窗至2026-10-31) |
| 测试 | `tests/test_tracker_indicators.py` / `tests/test_alerts.py` / `tests/test_support_levels.py`(新·16个) / `tests/test_index_valuation_discipline.py`(⑩渲染) |
| skill | `.claude/skills/tracker-dashboard` / `dashboards`（已同步到 10 节） |

**恢复后第一步**：继续开发 → 挑下面「可选增强」；验证 → 跑上面 4 条命令；懂某块 → 读对应 docstring（中文注释详尽）+ 本文件「关键实证/决策弯路」段。

---

## 🔑 关键实证发现 + 决策弯路（清上下文会丢、必须靠这里重建的部分）

### ⑦ 相对周期律（创业板 vs 上证 点差）
- **spread = 上证综指 − 创业板指（点）**；headline = 当前点差在 **5 年包络 [min, max] 内的线性位置** `(cur−min)/(max−max)`（作者口径，**复现他全部三次调用**：上沿1350/下沿-250/现在中枢）；副指标 = 同窗口**秩分位**（历史稀有度，house 口径）；漂移 OLS ≈ **−40 点/年**（创业板结构性跑赢）。
- **弯路**：原计划 OLS 去趋势 + 秩分位 → 实测给「下沿极点」，与作者「中枢」相左（点差分布偏态，高位停留久把秩分位拉低）→ **改用包络位置**作 headline，秩分位降为副。
- 图通道线 = 上下沿的**线性趋势拟合**（看漂移方向 + 振幅收窄/展宽；非精确边缘，精度让位于趋势）。

### ⑧ 成交量地量监测（两市成交额）
- **地量 = 成交额 / MA250 ≤ 0.6**（regime 自适应，除以 1 年均值消除名义额长期上行）。
- **弯路 1**：原选「3 年新低」实测**只 2 个事件**（名义额长期上行 → 严格新低极罕见）→ 改 MA250 比值（134 事件）。
- **弯路 2**：又发现 **90s 幼年期（50/134 事件，成交额百万级 + 指数从 100 暴升）虚高 60 日胜率到 55%** → 加 `TURNOVER_MATURE_START=2000-01-01` 过滤。
- **诚实结论（成熟市场 sample~84）**：✅ **时效扎实**（量底→价底中位 ~31 交易日、最长 60）；❌ **胜率无 edge**（各 horizon ~50%，**非**评论员说的「高胜率」）。
- **极端子集**（近 >0.5 年最低）：60 日 ~67-75%，但样本 ~15、p≈0.09-0.15 **未显著** → callout 标「暗示非定律、不据此加仓」。lookback 分桶有梯度（42%→75%）但极端桶 N=4 噪声；ratio（干涸比）无梯度。
- 图双色：普通地量(琥珀小) / 极端地量(红大)；悬停显「近X年最低」。

### ⑩ 关键位监测 + 支撑位实证（2026-08-15）
- **规则选位（无手画线，防事后拟合）**：平台顶（40日窗振幅≤8% 的箱体上沿；突破=收盘>顶×1.005；突破后 ≥5 日收盘在带上方才算站稳，立即跌回=突破失败弃）+ 前低枢轴（两侧各10日严格更低；反弹 ≥5% 后才算支撑候选）。合并后全局 cooldown=20 去重。带=位±1%（带状非线状），破位=收盘<位−1%，收回=3日内收盘回带，守住=回踩后15日无破位。
- **实证（上证综指 2000~，事件59/可用58：真破32/守住22/假破4）**：✅ 真破确认后 **20日实现波动显著抬升**（19.9% vs 守住14.5%，bootstrap 90%CI 不含0，对比无条件基准13.9%）；❌ 60日波动/回撤中位/收益胜率均不分离 → 支撑位=**波动观察坐标**，非买卖信号（与⑧同构）。前向指标从**确认日**起算（决策一致），基准=全样本 stride=5 抽样。
- **弯路1**：初版只做平台顶 → 样本仅~15 且漏掉「双底/前低」类（当前 3760~3766 正是前低带）→ 补 `pivot_lows` 第二类（样本→59）。
- **弯路2**：枢轴「严格最低」条件初写反（`(w<v[i]).sum()==side*2` 恒 False→0事件），应为 `w>v[i]`；另：测试合成序列 V 底必须放在 index≥side，否则检测不到。
- **弯路3**：`pending`（前向数据不足）≠ 结局未定——状态机区分「回踩测试中(15日窗未走完)」「破位观察中(收回窗3日未走完)」与已定结局，防把刚破1~2天的事标成真破。
- **⑩ 看板**：`monitor_snapshot` 状态机 + 下方第一支撑（未破位·现价下最近）/ 上方第一压力（已破位·现价上最近·翻空为压）+ 近2年价格图（位横线做成 trace，可点图例隔离）+ 三幕剧本提示（幕0预承诺）。
- **⑩ 与台账口径不同、各司其职**：看板状态机破位阈值=位−1%（研究口径）；Claim 001 失效=收盘<3760 且3日不收（更严，叙事口径）。

### 货币条件 + M2 拐点实证（2026-08-24;⑪ section 已于 2026-08-25 移至国内宏观看板①,以下为历史记录）
- **起源**：主流叙事「M2 定大盘」（7月 M2 同比跌破 8% → 上证难回前高、加仓点=M2 触底）。数据跟踪（⑪ section）+ 一次 event-study 礼遇。
- **弯路1（事件定义）**：严格环比连升连降（k=1）在 18 年真实序列上只出 **7/2/1** 个事件——M2 月度锯齿（如 2025-04 +1.0 → 05 −0.1 → 06 +0.4）把 run 切碎 → 改 **2 月动量口径**（sign(v[t]−v[t−2])，k=2·连降 4 月=下行确认/大段后反向 2 月=拐点）：9/7/5 事件，且 2025-10 见顶/2024-09 触底/2025 秋假下行（12月反转打断）全读对。k=3 试过=34 事件过碎。**单月反抽不打断 run** 是 k=2 的卖点（有测试钉死）。
- **实证（上证 2008-2026，n=9/7/5，基线=每10日抽样「随便哪天买」）**：触底回升 lag0 60日胜率 **86%**/中位+4.5% → **公布口径 lag15 塌缩至 57%/+0.2%**——「等数据确认时行情已走完」被直接量化；下行确认 33%/−3.3% 是最稳的弱信号（水退确认后 60 日弱）；见顶回落 60%（conclusion_text 判「温和分离+10pp」但 n=5）。**温度计非开关**。
- **双口径对齐**：lag0=事件月次月首交易日（理想上界）；lag15=次月 15 日（含）后首交易日（央行 9-15 日发布·保守端）。两口径差值本身就是敏感性分析——`event_trade_date` 纯函数承载，`pool.study.forward_returns` 就近滚动非交易日。
- **口径坑**：金十 `macro_china_money_supply` 只有 2008 起（04-07 周期不在样本）；**M1 在 2024-01 换新口径**（68→112 万亿 +64.6% 跳变）——只展示不进研究；社融 `macro_china_shrzgm` 只有增量无存量同比 → 增量TTM/M2 脉冲代理，源滞后货币 2-3 月。
- **meta 活注入**：tracker 看板**首个 meta 读**——`validate_m2_timing.py` 写 `china_money_conclusion`，dashboard `get_meta` + 未运行占位（模板=pool/report.py:301）。跑完 validator 需重新生成看板才见注入。

### 数据源死胡同（探针确认，别再踩）
- ❌ `stock_market_activity_legu` = **当日快照**（12 行），非历史序列。
- ❌ sina `stock_zh_index_daily` 只 `volume`（成分股数）**无 amount**；即现有 `index_daily.volume` 不是金额、不是两市。
- ❌ eastmoney `push2his` 在本环境被**代理墙挡**（ProxyError）。
- ✅ **baostock `sh.000001`+`sz.399001` 的 `amount` = 交易所总成交额**（已验证 sz.399001 与 399106 完全相同 → 交易所总数非成分和；2024-10-08 两市=3.45 万亿吻合真实天量），两所求和 = 两市，历史到 1991。

---

## ⚠️ 数据约定 & 已知 wart
- **⑦ 点差符号**：spread=上证−创业板，某日创业板点位 > 上证时为**负**（tile 显负数）——框架（去漂移残差分位）符号无关、照常工作，但与评论员「上证跑赢 350 点」的措辞可能对不上，`.hint` 已说明。
- **⑧ 样本稀疏**（~84 次/20 年）→ 胜率统计 CI 宽，UI 一律标「经验参考非定律」+ 露样本数 + p 值，**绝不写成确定信号**。
- **000001 无 PE/PB**（同创业板/科创50/中证1000）→ ⑦ 只用其日线算点差；⑩ 也只用其日线+volume。
- **⑩ 破位量比用 `index_daily.volume`**（sina 股数口径、非金额——见上面⑧段的坑）→ 只作 同序列/自身MA20 的相对比较，不跨指数比、不当金额用。
- **baostock 偶发 login 失败 → 重跑**（幂等 upsert）；首次拉历史（到 1991）稍慢。
- **plotly.js 首加载由 ③ 承载**（HTML 最前的图）→ ⑦/⑧/① 都 `include_plotlyjs=False` 避免重复 ~3MB。
- **③ 图口径（2026-08-15 起）**：分位线/阴影/默认显示窗口统一**近 10 年**（对齐 tile 的 zone 分位）；全历史（含 2005-2010 泡沫段，PE 曾 50+/PB 7+，与现体制不可比）留给 rangeslider + 5年/10年/全部 按钮。**坑：plotly autorange 对全量数据算、不随 x 窗口收缩**（切窗口后 y 仍 0-50）→ 服务端显式设 10 年口径 y 范围，页面 `_JS._yfit` 监听 plotly_relayout（只响应含 x-range 的事件防成环）在切「全部」/拖滑块后按可见段动态重算 y。⑦/⑧ 图仍全历史口径。

## 📋 可选增强（讨论过、未做，按价值排序）
1. **⑧ 情绪反向指标**：融资余额 / 搜索指数等 → 缺数据源，暂缓。
2. **⑧ 基本面锚**：经济数据突变监测（地量 thesis 的证伪过滤）→ 偏定性，暂缓。
3. **E5/V1 接入 index_timing_report.py `--push-alerts`**：目前只走 research/stock 通路；要指数看板独立推送才加。
4. **⑧ 极端子集样本积累后重测显著性**：现 p≈0.09-0.15 未显著，几年后再看是否→显著。
5. **⑦ 短期风格轮动持续性展开**：现仅 mom(20日) + consecutive_run，可加「主导方向持续多久」(容忍 1 日反向)。
6. **⑧ 复查 2017-2018 地量**：是否被 2015 泡余的 rolling-min 压掉没触发（MA250 比值口径下应已捕获，可验证）。
7. **⑩ 台账结算自动化**：到期 claim 自动结算脚本（现手动填 docs/CLAIMS_LEDGER.md；Claim 001 观察窗至 2026-10-31）。
8. **⑩ 多指数关键位**：现仅上证综指；可扩沪深300/中证1000（样本×3，跨市场重复计数需留意）。
9. **⑩ 样本积累后重测**：60日波动/回撤的 CI 现跨0，几年后样本翻倍再看是否分离。

> 验证范式：`python scripts/validate_volume_bottom.py` 的「按极端度分桶」表 + dashboard ⑧ callout 双向对照，最快确认 ⑧ 逻辑没回归。
> ⑩ 验证范式：`python scripts/validate_support_break.py` 的 bootstrap 表（20日波动应分离✔）+ dashboard ⑩ 状态表/pending 双向对照。
