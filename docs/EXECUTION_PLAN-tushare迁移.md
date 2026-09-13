# EXECUTION PLAN · 存量数据腿回溯迁移 tushare

2026-09-13 定稿（grilling 会话产出，Q1-Q13 全部落定）。纪律见 `docs/adr/0002-tushare-migration-scope-and-gates.md`，术语见 `docs/data/CONTEXT.md`。

**范围**：8 个只读看板的数据腿，能换 tushare 的尽量换（用户裁定含无痛腿=全迁）。
**冻结**：引擎腿（`daily_prices` ETF 部分、`etf_scale`）不动；`update_data.py` 不碰。
**顺序原则**：痛点 × 价值 ÷ 风险；平移批在前、升级批在后、退役先行。

## 批次 0 · 退役（无风险，先做）

| 腿 | 理由 | 动作 |
|---|---|---|
| 行业 PE（cninfo `stock_industry_pe_ratio_cninfo`） | PE 已 unused，仅 data_check 14 天门控续命 | 从门控+回填摘除，表留盘 |
| ETF 重仓 top10 兜底（`fund_portfolio_hold_em`） | 端点 2026-08 实锤死（6/6 JSONDecodeError） | 删兜底路径，E1 缺口诚实报缺 |

## 批次 1 · 平移类（逐腿：口径对照→全量重灌→对账→pytest→独立 commit）

| # | 腿（表） | 现源→痛点 | tushare 接口 | 对账族 | 备注 |
|---|---|---|---|---|---|
| 1.1 | 个股估值（stock_valuation） | baidu 半月稀疏/NoneType/无股息率 | `daily_basic`（pe/pe_ttm/pb/ps/总市值/流通市值/dv_ratio/换手） | 口径差族（静/TTM 分列报告） | PCF 无消费方（已验证）可丢；白送股息率；按 ts_code 逐股全史 |
| 1.2 | Shibor（shibor_daily） | 金十偶发被拦 | `shibor`（120积分，2006 起） | 精确族 | **已判(2026-09-13 对账)**：金十主源+tushare 应急——tushare 2022-04/05 overnight↔m3 两列交换 42 格，判金十优 |
| 1.3 | LPR（lpr_monthly） | 金十 | `shibor_lpr` | 精确族 | **接线已落**：tushare 主源(keep_null 保旧基准列；限频 1次/时 失败即金十)；首次成功拉取时补对账+起点检查 |
| 1.4 | M2/M1（china_money_supply） | 金十被拦退避 | `cn_m`（600积分） | 精确族 | **判死不迁(2026-09-13)**：tushare cn_m 的 M1=旧口径，2024 起新口径门 FAIL(差 43 万亿)；fallback 翻口径比缺数据更糟，金十留任 |
| 1.5 | 社融增量（china_tsf） | 金十源滞后 2-3 月 | `sf_month` | 精确族 | **已判**：金十主源(分项列全)+tushare sf_month 应急(修订差≤0.2% 观测用途无碍,keep_null 保分项)；stk_endval 社融存量→已落批次 2.5 |
| 1.6 | CPI/PPI（china_macro_monthly 两 metric） | 金十 | `cn_cpi`/`cn_ppi`（600积分） | 精确族 | **已判：tushare 主源**——金十=自算假精度(1.80138…)，tushare=官方发布口径(1.8)，严格更优；PMI/社零/工业增加值留金十同表混存，图注标注 |
| 1.7 | ETF 净值（etf_nav） | 天天基金缺码双调 fallback | `fund_nav`（unit/accum/adj_nav） | 高精度族 | **已判(2026-09-13 对账)：tushare 主源**——39 标的 38 PASS(|Δ|=0.0000 为主)；**511990 货币ETF 除外**(两源面值/摊余口径结构性不同 max\|Δ\|=3.92，`NAV_TS_EXCLUDE` 永走 em)；含商品看板 5 只非池内标的 |
| 1.8 | 指数日线 7 宽基（index_daily） | sina（无痛） | `index_daily` | 精确族 | **判死保留 sina(2026-09-13 对账)**：close 差=sina 3位小数 vs tushare 4位(升级无害)+1994/2001 古老修正(tushare 优)，但 **volume 单位沼泽**(上证系 t/s 恰0.01=股vs手、创业板指 0.0029=sina 深市另一套)，×100 对 399006 会错；无痛腿不赌单位 |
| 1.9 | 分红（stock_dividend） | sina（无痛） | `dividend`（2000-01 起） | 精确族 | **已判：tushare 主源**——cash_div_tax=每股税前(对账实证,茅台 28.02423 两源相等)；送转合一(stk_div 每股,×10)、消费方按和用；sina 降级兜底 |
| 1.10 | 指数成分+权重（index_constituents） | csindex（无痛，月更） | `index_weight` | 精确族 | **已落(2026-09-13)**：混合设计——csindex 保主源(成分名称+名称哨兵)，tushare 兜**国证系缺口**(399006 创业板指首次可得，etf_pool.yaml 补 index_code)；index_weight 无名称列→store name 保留守卫 + 权重和 90-110% 哨兵替代名称哨兵；实测 csindex 对 399006 超时 40s 时兜底实战接住 |
| 1.11 | 商品国内 17 品种日线（commodity_price） | sina 连续合约（无痛） | `fut_daily`（带 oi 持仓量） | 口径差族 | **已判(2026-09-13)：15/17 品种迁主源**——对账中位 |Δ|/价 0.0000%（两源即同一主力收盘），>1% 日全部=换月判定差(铁矿16/焦煤20/纯碱24/生猪25天,原油47天月月换月属预期)；**碳酸锂(LC.GFX)/LPG(PG.ZCE) tushare 无主力连续→TS_COMMODITY_EXCLUDE 永走 sina 补位**(品种级来源标记) |
| 1.12 | 预告/快报/正式报（stock_forecast/express/report_actual） | 东财（无痛） | `forecast`/`express`（按 ann_date）+ `income` 逐股 | 口径差族 | **已落(2026-09-13 二轮·降级件)**：东财主源不动；预告/快报 tushare ann_date 窗扫应急重建(fetch_earnings_forecast_tushare/fetch_stock_express_tushare,yoy=幅度中值/同比自算;express 的 yoy_* 实测=上年同期绝对值);正式报腿无应急(income 逐股全市场=5000 积分 vip) |
| 1.13 | sina 17 项财报摘要（stock_financials） | 全量幂等无痛，冷启动慢 | `income`∪`fina_indicator` | 口径差族 | **已落(2026-09-13 二轮·降级件)**：fetch_stock_financials_tushare 逐股重建 14/17 项(equity_total/goodwill/ocf 需 balancesheet/cashflow 应急不扩,缺项留空 sina 恢复自愈);expense_ratio 自算 |

## 批次 2 · 升级类（平移批收尾后做，各自独立成批、前后快照对照）

| # | 腿 | tushare | 升级内容 |
|---|---|---|---|
| 2.1 | 融资融券（market_margin） | `margin`（沪深北全史 2010-03 起） | **已落(2026-09-13)**：对账沪市侧 3998 日**零格不一致**；全量重灌 2010-03-31 起带 `financing_cs/total_margin_cs` 沪深合计（北交所排除——量级微小且 2022 起步）；旧 `*_sse` 列留档并排（COALESCE 互不抹）；⑨杠杆成分 `fillna` 过渡、口径标签三处更新；最新两融合计 13495 亿 |
| 2.2 | 两市成交额（market_turnover） | `daily_info`（600积分，官方口径 SH_A 1991 起） | **判死不切(2026-09-13 对账)**：daily_info=纯 A 股口径，baostock=**含债券/基金全证券口径**（中位差 86%、p95 205% 且系数不稳定）——⑧地量阈值与 event-study 实证校准在旧口径上，切换=平移已校准实证层；fallback 也不做（防瞬时污染量纲）。**发现留档**：现有「两市成交额」实为全证券口径、比市场惯称的 A 股口径高 ~86%；若要 A 股语义须另立重校准项目（fetch_market_turnover_tushare 留盘存档） |
| 2.3 | 交易所仓单（commodity_inventory） | `fut_wsr` | **已落(2026-09-13 二轮)**：CZCE 3× 高估 bug 修(旧全列求和吃小计/总计行,实证 FG 3804=3×1268;改「总计」行口径全量重灌,比例不变故历次 event-study 结论可比)+WSR 八品种扩腿(铜/铝/锌/螺纹/金/银 SHFE·原油 INE·碳酸锂 GFEX 2019 周起,仓库和;单次 1200 行截断必须 query_paged;SHFE 同名双行=完税+保税 sum 全行;**CZCE 品种不吃 fut_wsr**——混升贴水/有效预报错位行,FG/UR 对账实证;DCE 品种无源;单位吨/千克/桶/手随品种自身分位);validate_commodity_inventory 边界定界 3→11 品种重跑(事件数 8 倍,仍温度计非开关);对账 data/recon/tushare_wsr.md |
| 2.4 | 期限结构精确展期 | `fut_mapping` | **已落(2026-09-13 二轮)**：fut_mapping+逐合约收盘→fut_contract_daily(15 品种 2.4 万+15.4 万行);fundamentals.roll_adjusted_series 纯函数(换月日收益按旧合约 close 计)+panel loader;两个 event-study 验证器前向收益切换(回退主连+注记);对账实证换月污染中位 0.2-7.4pp(生猪 max 32pp,黑色/农产品原研究确被污染),重跑判定不变;data_check 6.15 腿>35天门控;data/recon/tushare_roll.md |
| 2.5 | 社融存量落地（china_tsf 扩列） | `sf_month.stk_endval` | **已落(2026-09-13 二轮)**：ts_stock 扩列(万亿单位·keep_null 与分项互不抹)+sf_month 常规月调;money_conditions.tsf_stock_yoy_series 纯函数(PeriodIndex 月度对齐防稀疏错位+**口径断点规则**:单月|MoM|>10% 判断点、其后 12 个月同比丢弃——2017-01 +18% 统计范围切换未回溯实证,混口径假读 30%);①节脉冲代理→真口径同比(缺列回退代理,标签随口径),④节存量 tile;对账 data/recon/tushare_tsf_stock.md(DB 重拉零格差/存量自洽中位 6.3%/形态对照近年相关 0.991) |

## 1.12/1.13 降级说明（2026-09-13 实施中重排；二轮已落地）

实测发现**按报告期拉全市场是 5000 积分 vip 接口**（forecast_vip/express_vip/income_vip）→ 1.12「迁主源」在 2000 档做不到同形状替换（东财按期整面板 vs tushare 按公告日增量）。降级为「东财主源 + tushare ann_date 应急重建」设计（实测门已过：forecast 按 ann_date 高峰日 1002 行可用）。1.13（sina 17项→income∪fina_indicator）同理降为应急补位优先级。二者重排到批次 2 之后——给无痛腿买保险的价值低于真升级。
**二轮落地(2026-09-13)**：预告/快报应急=ann_date 披露窗(~4 月)逐 bdate 扫+滤 end_date 拼面板；express 的 yoy_* 字段实测为上年同期**绝对值**(非百分数)→同比自算；1.13 覆盖 14/17 项(equity_total/goodwill/ocf 需 balancesheet/cashflow 应急不扩)；正式报腿无应急(income 逐股全市场=vip)。

## 判死 / 保留清单（勿再评估）

- **判死**：`index_dailybasic` PE（只覆盖 6 指数、无沪深300）；`report_rc` 一致预期（2000 档每天仅 10 次）；`yc_cb` 中债曲线（单独权限接口）；`fund_share` 实测取消（份额表归引擎冻结）；`et_share_size`(8000)/`fund_daily`(5000)/`cn_pmi`(5000)/国际指数(6000)。
- **保留原源**：FDR007（定盘口径，`repo_daily` DR007 加权价=口径降级）；社零/工业增加值/央行资产负债表/政府债逐券（无接口）；夜盘快照（rt 族单独权限）；外盘国际期货/LME/COMEX；南华（`fut_index_daily` 仅留备份通道）；ccidx；100ppi；海外腿全部（UST/DXY/美股/FRED/NYFed/COMEX/CFTC/经济日历）；美债 `us_tycr`/TIPS `us_trycr` 备份通道不切换（现源无痛）。

## 对账容差表（验收标准）

| 族 | 腿 | 标准 |
|---|---|---|
| 精确族 | 指数日线、Shibor/LPR/CPI/PPI/M2/社融、margin 沪市侧、成分权重、分红 | 容差 0，重叠期 ≥250 期逐期 diff |
| 高精度族 | etf_nav | 重叠期 ≥1 年逐日 \|Δ\|≤0.001 元 且 ≥99.9% 落内 |
| 口径差族 | daily_basic 估值、fut_daily 商品、forecast/express、income 财报映射 | 不设硬门：出量化报告（日均偏差/最大偏差/分布），看过放行 |
| 升级类 | margin 两市化、daily_info、fut_wsr | 不叫对账：旧口径存档+新口径并排快照对照 |

## 每腿交付物模板

1. fetcher 新函数：tushare 主源 + 旧源兜底回退链（TushareError→降级，不炸腿）
2. manager 编排接入 `dashboard_data_check --fix` 门控（更新节奏不变）
3. 对账：`scripts/recon_tushare.py <leg>` 重叠期 diff 报告
4. pytest：新 fetcher 单测 + 消费方 parity 测试
5. 独立 commit（平移/升级不混批，引用本文件）

## 实测门清单（2026-09-13 已测 · scripts/probe_tushare_gates.py）

1. ✅ `forecast`/`express` 按 ann_date 拉全市场 — 高峰日 1002 行（20260715 中报预告）/ 633 行（20260131），路径可用
2. ✅ `index_weight` 国证系 — 399006.SZ 有数据（对照组 000300.SH 同返 7000 行），成分腿可全量迁
3. ✅ `fut_daily` 主力连续 — `RB.SHF`（螺纹钢主力）直接可拉、带 oi 持仓；17 品种经 `fut_basic` 建 `<品种>.<所>` 映射
4. ✅ `fut_wsr` — 列为**仓库粒度**（trade_date/symbol/fut_name/warehouse/vol/unit），SHFE/INE/CZCE/GFEX 覆盖、**无 DCE**；单次调用 ~1200 行**截断且含重复行**，必须 query_paged（二轮实证）；CZCE 品种列错位不可用（见批次 2.3 判决）
5. ⚠ `shibor_lpr` — 实测**限频 1次/小时**（文档 120 积分档未标）；腿按「单次全区间 ≤4000 行、retries=1、失败留旧源」设计，历史起点检查并入首次实拉

## 已完成 / 无需迁移

- 个股诊断观察池日线：**事实上已迁移**——pool 的 `daily` 按交易日整表腿只写「DB 已跟踪代码」，观察池早已在库，随 data_check step 9 走 tushare。仅验证覆盖。
- pool 六腿（spot/申万/扣非/资产负债/曾用名/日线）：2026-09-13 已迁，不在本计划范围。

## 收官（2026-09-13 二轮）

**全部批次已落地**（首轮 12 commits：批次0+批次1 平移13腿+2.1/2.2；二轮 4 commits：2.5 社融存量+2.3 仓单扩腿+2.4 展期口径+1.12/1.13 应急）。计划内再无「剩余」件；判死/保留清单继续有效（勿再评估）。遗留观察项（非本计划范围）：
- CZCE fut_wsr 列错位若他日修复，可评估 CZCE 三品种并源（当前 akshare 总计口径为准）；
- DCE 库存无免费源（铁矿/焦煤/豆粕/玉米/生猪/LPG）；
- 正式报（stock_report_actual）东财唯一源——income 逐股全市场为 vip 接口；
- 基差/库存两 event-study 已在扩容+展期调整口径下重跑（判定均为温度计非开关），结论活注入看板。
