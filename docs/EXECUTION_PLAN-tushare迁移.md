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
| 1.2 | Shibor（shibor_daily） | 金十偶发被拦 | `shibor`（120积分，2006 起） | 精确族 | 全量重灌（历史比金十深 9 年，无拼接） |
| 1.3 | LPR（lpr_monthly） | 金十 | `shibor_lpr` | 精确族 | **增量接续**唯一候选：起点≈2008 浅于金十 1991，重叠段对账后接续 |
| 1.4 | M2/M1（china_money_supply） | 金十被拦退避 | `cn_m`（600积分） | 精确族 | 全量重灌；M1 2024 口径断点为序列固有，换源不解决 |
| 1.5 | 社融增量（china_tsf） | 金十源滞后 2-3 月 | `sf_month` | 精确族 | 滞后是源侧固有，对账确认 tushare 滞后程度 |
| 1.6 | CPI/PPI（china_macro_monthly 两 metric） | 金十 | `cn_cpi`/`cn_ppi`（600积分） | 精确族 | PMI/社零/工业增加值无接口，**留在 china_macro_monthly 旧源混存**，图注诚实标注 |
| 1.7 | ETF 净值（etf_nav） | 天天基金缺码双调 fallback | `fund_nav`（unit/accum/adj_nav） | 高精度族 | 研究看板主数据：偏离度全历史分位全量重算；accum 分红日附近逐日 ≤0.001 元；含商品看板 5 只非池内标的 |
| 1.8 | 指数日线 7 宽基（index_daily） | sina（无痛） | `index_daily` | 精确族 | amount 千元单位换算；同官方数据期望零差 |
| 1.9 | 分红（stock_dividend） | sina（无痛） | `dividend`（2000-01 起） | 精确族 | pool 宇宙周更腿同迁 |
| 1.10 | 指数成分+权重（index_constituents） | csindex（无痛，月更） | `index_weight` | 精确族 | **实测门**：国证系（399006）是否覆盖；QDII/CES 缺口保留 csindex 兜底 |
| 1.11 | 商品国内 17 品种日线（commodity_price） | sina 连续合约（无痛） | `fut_daily`（带 oi 持仓量） | 口径差族 | **实测门**：连续合约代码映射（主力连续 9999 风格 / `fut_mapping`）；换月拼接口径差出报告 |
| 1.12 | 预告/快报/正式报（stock_forecast/express/report_actual） | 东财（无痛） | `forecast`/`express`（按 ann_date）+ `income` 逐股 | 口径差族 | **实测门**：forecast 按 ann_date 拉全市场（文档自相矛盾）；正式报走 income 逐股（pool 模式）；type 八类 vs 东财标签映射 |
| 1.13 | sina 17 项财报摘要（stock_financials） | 全量幂等无痛，冷启动慢 | `income`∪`fina_indicator` | 口径差族 | 17 项字段映射；与 1.12 共用逐股基础设施；sina 留精筛回退 |

## 批次 2 · 升级类（平移批收尾后做，各自独立成批、前后快照对照）

| # | 腿 | tushare | 升级内容 |
|---|---|---|---|
| 2.1 | 融资融券（market_margin） | `margin`（沪深北全史 2010-03 起） | ⑨恐贪杠杆成分沪市单边→两市；旧沪市口径指标存档并排一个过渡期 |
| 2.2 | 两市成交额（market_turnover） | `daily_info`（600积分，官方口径 SH_A 1991 起） | ⑧地量/⑨流动性换官方口径；量化旧 399001 口径差；白送全市场平均 PE |
| 2.3 | 交易所仓单（commodity_inventory） | `fut_wsr` | **实测门**：覆盖面；CZCE 三品种重叠精确对账后扩 SHFE/DCE/GFEX |
| 2.4 | 期限结构精确展期 | `fut_mapping` | 主力换月映射，修记录在案的「精确展期口径」待补项 |

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

## 实测门清单（批次内先测后迁）

1. `forecast`/`express` 按 ann_date 拉全市场（2000 档真实可用性）
2. `index_weight` 国证系覆盖（399006）
3. `fut_daily` 连续合约代码映射（主力连续风格 / 经 `fut_mapping`）
4. `fut_wsr` 交易所覆盖面
5. `shibor_lpr` 历史起点（不浅于金十 1991 则全量重灌，浅则增量接续）

## 已完成 / 无需迁移

- 个股诊断观察池日线：**事实上已迁移**——pool 的 `daily` 按交易日整表腿只写「DB 已跟踪代码」，观察池早已在库，随 data_check step 9 走 tushare。仅验证覆盖。
- pool 六腿（spot/申万/扣非/资产负债/曾用名/日线）：2026-09-13 已迁，不在本计划范围。
