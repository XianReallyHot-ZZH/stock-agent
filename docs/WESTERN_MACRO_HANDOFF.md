# 西方宏观预测台账 — Handoff (Phase 3 · 只读旁路 · ADR-0001)

> 恢复入口。清空上下文后先读本文件 + `docs/western_macro/CONTEXT.md` + `docs/adr/0001`。

## ⚡ 当前状态 & 立即任务
**Slice 1 全部完成并经审计**(数据层+LLM抽取+评分+看板+抽样审计+confirm闸门,全打通有测试)。
**黄金阶段定位器 MVP 完成**(stage.py + wm_gold_stage_eval.py + 看板块 + 32 测试;闸门 56% 未过→降级辅助参考,详见末段)。
**宏观 call 实时确认视图完成**(live.py + 看板块 + 15 测试;方向语言资产的「定位」落地,用上真 edge)。
**★ 宏观框架看板完成(北向目标达成)**(`framework.py` + `macro_framework_report.py` + 9 测试;`data/macro_framework.html`)—— 把博主思维框架做成**纯数据跟踪+分析**看板:沿因果链 利率→曲线→美元→金属→能源→权益 铺开,每资产 `asset_analysis` 给趋势/位置/结构标签(price=5阶段 / yield=水平分位高/中/低+趋势 / curve=倒挂+陡峭/趋平) + 总览表 + 分节点图 + 因果框架图。**不含 claim/台账/命中率**(用户定调:不再纠结准不准)。黄金阶段定位器已抽进金属节点。
- **战略转向(用户 2026-08-09 定调)**:prediction-ledger 实验已给结论(edge 在利率方向 / 美股看空死穴 / 黄金阶段 56%),**继续抠命中率无意义**。北向目标 = 框架数据看板(本段),ledger 收敛为「预测台账(历史档案)」。实时确认块意义有限,置顶 ledger 看板备忘、随新转写稿迭代。
- **当前快照(2026-08-09,框架看板实证)**:美债2Y/10Y/30Y 全「高位·上行」;2s10s「正常·陡峭化」(倒挂已修正);DXY「回调下跌」;黄金「反弹初期」;白银/原油「回调下跌」;铜/美股「趋势上行」,道指「头部区域」(偏离90%)。
- **立即任务(待用户拍板)**:① 框架看板日常迭代(加资产/指标,如真实利率 TIPs、期限溢价、CPI/就业等宏观指标);② 把框架看板接入 `dashboards` skill 一键刷新;③ 黄金阶段 P2(多时间尺度筑底/回调判别)仍可做但非优先。

## 👤 用户投资哲学(决定怎么 build,务必内化)
- **不要预测精确时点/价位**(永远买卖不到极值);追求**大概率方向 + 大致区间 + 大致操作**。
- 核心能力 = 通过**数据分析+可视化+投资逻辑**,判断特定标的**当前所处的"阶段"**,做到**投资时心中有数、判断有逻辑和数据支撑**。
- 推论:北向目标不是"价位预测器",是**「阶段定位器」**。JZ 自己通篇说的也是阶段语言(筑底/反弹/头部/回调/震荡)。提炼他的框架 = 提炼他的阶段判断逻辑。

## 🧭 战略路线(三阶段,前段喂后段)
- **Phase 1(已完成)**:跟踪 JZ 预测 → 量出 edge。结论:edge 在**利率(美债2Y/2s10s)**;**美股看空是死穴**;**黄金看多但择时差**。
- **Phase 2(待做,可并入 Phase 3)**:置信加权 + 活态流水线 + 深统计(calibration/time-decay/regime)。
- **Phase 3(北向目标,立即启动 MVP)**:把**被验证有 edge 的框架部分**编成规则 → **阶段定位器**:每标的当前阶段 + 驱动信号 + 置信度。track record 当过滤器(强项编进去、死穴扔掉)。

## 📊 当前 track record 快照(2026-08-09,asof;81结算/38%命中/14%本事)
| 标的 | 结算 | 命中 | 本事★ | 判读 |
|---|---|---|---|---|
| 美债2Y | 12 | 8 | 4 | ✅ 真edge(短端利率) |
| 2s10s | 3 | 3 | 2 | ✅ 曲线 |
| A股 | 4 | 3 | 1 | 偏正面(样本小) |
| 白银 | 8 | 3 | 0 | 一般 |
| 原油 | 7 | 3 | 1 | 一般 |
| 美元指数 | 5 | 1 | 0 | 弱 |
| 美债10Y | 5 | 1 | 0 | 弱 |
| 黄金 | 22 | 7 | 1 | ⚠ 看多但择时差(32%命中) |
| 标普500 | 14 | 2 | 2 | ❌ 看空美股基本全错 |
- **抽样审计**:`wm_audit.py`,错误率 ~20%(0捏造,多为改写/加料;严重=方向反转+资产错标)。**prompt调优撬不动此地板→靠 confirm 闸门人工清洗**。已 veto 3 条明显错(标普"回到5000"标涨×2、2025-07-18 有色错标)。模式扛得住 20% 噪声。
- 语料 96 集,已转写 ~74,已抽取 ~64(10 期 deepseek 持续返空,待补)。

## 🗺️ 代码地图
- `stockagent/data/store.py`:`western_macro_series`(source/symbol/date,close 统一承载价格或收益率)+ `wm_claims/settlements/rules` 表 + 方法(注意:重抽 upsert 不覆盖 state)。
- `stockagent/data/fetcher.py`:`fetch_us_treasury`(bond_zh_us_rate)/`fetch_us_index`(index_us_stock_sina .INX/.IXIC/.DJI)/`fetch_foreign_future`(futures_foreign_hist GC/SI/CL/OIL)/`fetch_forex_pair`(forex_hist_em,push2his 常被拦)+ `fetch_forex_pairs_ecb`(Frankfurter/ECB fallback,6腿EUR-cross换算USD-base)+ `reconstruct_dxy(_series)`(ICE 6腿公式,abs无须)。
- `stockagent/data/manager.py`:`update_western_macro()`(AkShare forex 失败→ECB fallback)。
- `stockagent/western_macro/`:`drivers.py`(canonical 15节点因果图+ASSET映射在extract.py)·`extract.py`(LLM抽DRAFT;`normalize_horizon`年份自纠;空响应重试;`process_episodes_parallel`默认workers=1)·`score.py`(评分;`series_for`支持fut/usidx/ust/dxy/index_daily/commodity)·`dashboard.py`(HTML+Plotly时序图:发布日→兑现日窗口+断言点+level目标线+状态列+**🥇黄金阶段块**+**📡实时确认块**)·`stage.py`(黄金阶段定位器 MVP: MA60+近12月偏离分位→5阶段决策树 + 词映射 + 一致性回测 + 置信度;复用 tracker.indicators/diagnose)·`live.py`(宏观call实时确认: 未到期断言→兑现中/背离/停滞 + 按标的聚合JZ净方向;复用 score 点在时helper)·`framework.py`(★北向目标 宏观框架看板: 纯数据沿因果链 利率→曲线→美元→金属→能源→权益,每资产 asset_analysis 给趋势/位置/结构标签(price=5阶段/yield=水平分位+趋势/curve=倒挂+陡峭) + 总览表+分节点图;复用 stage.classify_stage/dashboard 主题与黄金阶段块;无claim/台账)。
- `scripts/`:`backfill_western_macro.py`·`extract_western_claims.py`·`western_macro_report.py`(结算+渲染+开 ledger)·`wm_confirm.py`(审核CLI)·`wm_audit.py`(抽样审计)·`wm_gold_stage_eval.py`(黄金阶段回测闸门+混淆矩阵+--sweep标定)·`macro_framework_report.py`(渲染宏观框架看板 data/macro_framework.html·纯数据)。
- 测试:`tests/test_western_macro.py`(DXY公式+store)·`tests/test_western_score.py`(~15个:edge/baseline/路径感知/负值系列/点位方向一致)。

## ⚠️ 已踩平的 10 个坑(别再踩)
1. **DXY**:AkShare push2his 被网拦 → ECB/Frankfurter fallback(免费无key,6腿重算,实测对)。
2. **铜**:外盘 LHC/HG 数据失真(无效符号/零成交量)→ 铜/有色用**沪铜主连 CU0**(commodity_price,元/吨)。
3. **A股**:用**上证综指 000001**(非沪深300,点位对不上)。
4. **方向打分=路径感知**:窗口内≥3%显著波动即视为该方向发生(端点法漏"跌完又涨回")。
5. **负值系列**(2s10s倒挂):`_direction`/mfe 用 `abs(start)` 分母,守卫 `abs(start)<1e-9`(非`start<=0`)。
6. **点位(level)**:命中需方向也一致(防"摸到就涨回");level 缺 level_value→人工。
7. **情景(scenario)**:只 is_primary=1 计分,备选(is_primary=0)台账标"备选"。
8. **horizon**:LLM 系统性把近期未来标成过去年份 → `normalize_horizon`(年份自纠)+ render时`normalize_claim_horizons`安全网。
9. **deepseek 并发降质**(5worker→claim掉到1/4)+ 偶返空 → `process_episodes_parallel`默认 workers=1 + 空响应重试1次。
10. **LLM 抽取 timeout=180**(`llm_client.chat`加了timeout参数,默认40)。

## 📐 设计约束(勿违背)
- **围栏(ADR-0001)**:西方宏观数据/信号**永不喂 A股轮动引擎**,只读诊断给人看。要喂引擎需新 ADR。
- **edge = hit AND NOT baseline_hit**(赢过朴素基准才算本事);baseline=该资产自身漂移。
- **大模型只解析不预测**:LLM 抽 claim(PARSE,不发明数字/方向);阶段定位器的规则来自框架+数据,不是 LLM 现场判断。
- git:用户直接 commit master([[git-master-direct]]),不开分支/PR;**只在用户要求时 commit/push**。

## 🎯 下一步详规:黄金阶段定位器 MVP
**目标**:验证"JZ 的黄金阶段逻辑能否被数据+规则复现"。吻合率≥60%→路通,扩到美元/曲线/原油;<60%→降级为辅助参考。
**阶段表(草案,可调)**:筑底/底部震荡 → 反弹初期 → 趋势上行 → 头部区域 → 回调下跌。
**数据信号**(都在 `western_macro_series`,用 `score.series_for` 或直接 `store.get_western_series`):
- 黄金(fut/GC)自身结构:相对 MA60、近 N 月在区间位置(复用 tracker 的 price_timing 思路)。
- 美元(dxy/DXY)阶段:强势/头部/贬值。
- 曲线(ust/US2S10S):倒挂/陡峭化。
- (进阶)实际利率/期限溢价:目前只有名义 UST,**MVP 用名义UST趋势+DXY 代理**,真·实际利率需补 TIPs/breakeven 数据(P2)。
**回测标签**:从 wm_claims 的 statement 里抽 JZ 的阶段词("筑底""反弹""头部""回调")作 his-stated-stage;或直接读转写稿 grep。比对"该日规则分类阶段 vs 他说的阶段"。
**产出**:看板加一块"黄金当前阶段 + 驱动信号 + 置信度(信号强度 × 黄金非JZ强项→置信打折)"。
**注意**:黄金是 JZ 择时弱项(32%命中)→ 阶段定位可参考但置信打折,别照搬择时。

## 🔧 常用命令
```bash
python scripts/western_macro_report.py                 # 结算+渲染+开 data/western_macro.html
python scripts/extract_western_claims.py --since 2026-08-01   # 抽新集(幂等·顺序)
python scripts/backfill_western_macro.py               # 补数据(含ECB外汇+DXY)
python scripts/wm_confirm.py --veto <uid前8位>          # 审核清洗
python scripts/wm_audit.py --seed 42                   # 抽样审计
python scripts/wm_gold_stage_eval.py --show-dropped    # 黄金阶段回测闸门+混淆矩阵(--sweep 标定)
python scripts/macro_framework_report.py               # ★渲染宏观框架看板(纯数据·因果链) data/macro_framework.html
python -m pytest tests/test_western_macro.py tests/test_western_score.py tests/test_western_stage.py -q   # 测试
```
