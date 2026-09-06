---
name: commodity-dashboard
description: Refresh data and generate the 大宗商品看板 (commodity.html, 7 sections). Use when the user wants to update/refresh the commodity dashboard, or asks to "生成/刷新大宗商品看板/商品看板/商品面板/比价/商品雷达/商品ETF错配/基差库存". Backfills 国际基准 (LME铜CAD/LME铝AHD/LME锌ZSD/COMEX金银/WTI/CBOT豆粕玉米, sina 外盘) + 中证商品指数 (ccidx 官方总览; 南华 akshare 端点已死) + 投资标的 NAV (非池内 5 只 → etf_nav) + 基差/期限结构 (100ppi 2019起) + 郑商所仓单 (CZCE 三品种周采样 2021起), then renders. 第八看板, 2026-09 从个股诊断拆出; 国内 17 品种日线/夜盘快照仍归 backfill_stock_data --comm. 永不喂引擎.
---

# 大宗商品看板 — 维护与生成

商品周期观测温度计 + 周期股择时深化底座(第八看板,2026-09 从个股诊断看板的周期 tab 拆出——职责分离:
商品研究做大在本看板,个股看板聚焦股票本身,只留 🧭 商品环境速览行)。输出 `data/commodity.html`
(离线自包含,深浅色可切默认浅色),七 section:

1. **🚦 异动雷达** — 双段:⚠快腿(20日动量≥±10% + 60日新高/新低=研究排队,实证:追买跑输)/
   ⛔慢腿超买(偏离分位≥95%=追高风险)/🟢慢腿超卖(≤5%=飞刀与错杀观察,双向分节)。**国内序列口径**(与 validate_commodity_speed 实证、
   夜盘快照同源),meta 键 `commodity_speed_conclusion` 与个股看板时代同键复用
2. **📊 环境总览** — 官方=中证商品指数(ccidx.com,两线;**南华 akshare 端点已死** qhkch.com 挂了,
   官方总览用中证)+ 自算等权合成/广度(17 品种,非官方指数,图注标明)
3. **🧲 品种面板** — 17 品种;**有国际基准的品种国际价为主语**(国际价格波动一般传导至国内,
   研究看传导方向),国内价对照(=大A投资指导;内外 60 日差);无基准 9 品种标「国内定价」
4. **📈 品种时序** — 主语序列图 + 点击放大(偏离度 Top-10 标注/rangeslider/快捷窗)
5. **⚖ 比价矩阵** — 金银比/油金比/铜铝比/豆粕玉米比/螺矿比/玻璃纯碱比(params.yaml
   `commodity.ratio_pairs`);**黄金只做分母**(黄金叙事归宏观框架看板)
6. **🎫 投资标的映射(二期)** — 品种→大A可投标的 13 只(期货ETF·T+0:有色期货159980/能化159981/
   豆粕159985;股票ETF:有色/煤炭/钢铁/建材/化工/养殖/农业;现货ETF·T+0:黄金518880;LOF:白银161226/
   南方原油501018·QDII)+ **错配度**=ETF(NAV)涨幅−品种涨幅(蓝≥+10pp=ETF领先/溢价、橙≤−10pp=ETF
   落后·错杀观察)。期货ETF NAV 含展期、QDII 含汇率;**T+0 品种永不进引擎宇宙**;偏离度/筹码在
   行业研究看板不复刻(池内标的带 📈 跳转)
7. **🔬 基差·期限结构·库存(二期剩余,2026-09 过 event-study 礼遇后入板)** — 主力基差率+分位 |
   近月-主力斜率年化+分位(正=contango/负=backwardation) | 郑商所仓单库存分位+4周变化(仅玻璃FG/
   纯碱SA/尿素UR——PG(LPG)仓单接口无该键、金属/碳酸锂无多史免费源,待补);实证结论活注入
   (meta `commodity_basis_conclusion`/`commodity_inventory_conclusion`)。验证器:
   `validate_commodity_basis.py`(深贴水/深升水/期限结构四臂 vs 全日抽样基线,expanding 分位防前视,
   冷却60日)与 `validate_commodity_inventory.py`(低/高库存+去化/累库四臂,周采样,冷却8周)

## 触发场景
- "刷新大宗商品看板 / 商品看板 / 商品面板 / 比价 / 商品雷达 / 商品ETF错配"
- 用户要看商品环境、某品种国际/国内对照、金银比等比价、商品 ETF 错配时

## 标准流程

### 1. 更新数据(幂等,可反复跑)
```bash
PYTHONIOENCODING=utf-8 python scripts/backfill_commodity.py        # 全部五腿(基准+指数+标的NAV+基差+仓单)
PYTHONIOENCODING=utf-8 python scripts/backfill_commodity.py --targets  # 只投资标的 NAV
PYTHONIOENCODING=utf-8 python scripts/backfill_commodity.py --basis     # 只基差+期限结构(首次全史2019起 ~11min,之后增量秒级)
PYTHONIOENCODING=utf-8 python scripts/backfill_commodity.py --inv       # 只郑商所仓单(首次2021起 ~5min,之后次周三 1 次调用)
```
国内 17 品种日线/夜盘快照(面板的隔夜列、雷达口径)归 `backfill_stock_data.py --comm`
(dashboard_data_check --fix 6.11~6.14 四腿都已自动带;标的 NAV 只拉非池内 5 只,
池内标的随研究看板 NAV 腿走不重复拉;QDII 501018 的 NAV 源滞后 1-2 天属正常)。

### 1.5 实证(🔬 结论的来源;重跑=重结算,结论写 meta 活注入看板)
```bash
PYTHONIOENCODING=utf-8 python scripts/validate_commodity_basis.py        # 基差/期限结构四臂 → data/commodity_basis_study.html
PYTHONIOENCODING=utf-8 python scripts/validate_commodity_inventory.py    # 库存四臂(CZCE三品种) → data/commodity_inventory_study.html
```

### 2. 生成看板
```bash
PYTHONIOENCODING=utf-8 python scripts/commodity_report.py --no-open
# → data/commodity.html(默认自动打开浏览器;壳页 data/index.html 左侧导航「🛢 大宗商品」)
```

### 3. 汇报(给用户)
- **雷达**:⚠异动品种(动量/新高新低)+ ⛔极端品种(超买/超卖),温度计非开关
- **总览**:中证商品指数同比/近60日 + 自算广度(20日/60日上涨占比)
- **面板**:向上/背离/向下品种清单(主语口径);国际主语与国内价的内外 60 日差
- **比价**:金银比/铜铝比等的当前值与全史分位
- **投资标的**:|错配60日| 最大的几对(ETF vs 品种 谁涨谁没涨;负=错杀观察)
- **🔬 基差/库存**:基差率/期限斜率/库存分位的极端品种 + 两条实证结论(温度计非开关口径)

## 已知坑(端点真相 · 2026-09-06 实测)
- **南华指数 akshare 端点已死**(get_qhkc_index 源 qhkch.com 停更,KeyError)——官方总览用
  `futures_index_ccidx`(中证商品指数,ccidx.com,~4 年 969 交易日,当日新鲜)
- **外盘铜**:HG(COMEX)/LHC 数据失真不用;**LME铜=CAD**(sina futures_foreign_hist 符号),
  LME铝=AHD、LME锌=ZSD、CBOT豆粕=SM(非美豆 S,与国内豆粕口径一致)、CBOT玉米=C
- 国际基准写 `western_macro_series source='fut'` 与 western 腿同表幂等;GC/SI/CL 两腿都会刷,
  本腿独立 meta(`last_commodity_benchmark_update`)保证新鲜度不依赖宏观看板节奏
- 豆粕/玉米的 CBOX 单位是 美分/蒲式耳、美元/短吨——与国内 元/吨 不同,面板按主语单位标注,
  同比/动量为各自序列内比值不受单位影响
| 端点 | 用途 | 起点 |
|---|---|---|
| `futures_foreign_hist`(sina) | 国际基准 8 符号(CAD/AHD/ZSD/GC/SI/CL/SM/C) | 2016-09 |
| `futures_index_ccidx`(ccidx) | 中证商品期货指数/价格指数(官方总览) | ~2022 |
| `fund_etf_fund_info_em`(复用) | 投资标的 NAV(非池内 5 只,增量为王;QDII 滞后 1-2 天) | 2019+ |
| `futures_spot_price_daily`(100ppi) | 基差+期限结构(现货/近月/主力+基差率;逐日请求≈1.4min/年,按半年窗分段) | 2018 |
| `futures_warehouse_receipt_czce`(逐日) | 郑商所仓单→聚合(玻璃FG/纯碱SA/尿素UR 周采样) | 2021 |

## 隔离纪律
只读旁路·**永不喂引擎**;commodity→tracker 单向 import 原语(judge_commodity/commodity_dev_stats/
commodity_radar/fig_json_readable),与 pool/china_macro 同向,不 re-export。
**传导链不动**:commodity_map→leading.commodity_signal/alignment→positioning_score 仍吃国内价
(commodity_price 表);面板主语(国际)与个股卡判定(国内)偶发分歧是两个用途,读图说明已注明。
温度计非开关:全部 section 观测非信号,无微信推送(M1/M2 股票告警仍在个股看板双通道)。

## 库存端点真相(2026-09-06 审计 · 🔬 覆盖边界)
- 99qh(`futures_inventory_99`)死、SHFE/DCE 仓单端点死(JSONDecodeError)、GFEX 解析坏
  (akshare 未跟上改版,碳酸锂库存待补)、东财 `futures_inventory_em` 活但仅 72 天
  ——**多史免费源只剩 CZCE 仓单**(且 PG/LPG 接口无该键)→ 库存实证覆盖=玻璃/纯碱/尿素三品种,
  结论外推需谨慎;金属/能源/农产品库存无多史免费源,永久待补
- 期限结构没有独立逐月合约源——用 100ppi 的 近月/主力 结算价对(年化斜率)作代理,展期收益
  精确口径(逐主力换月)待三期
- 豆粕/能化/有色期货 ETF 进 etf_pool 的评估:**结论不进池**(NAV 含展期结构,偏离度/筹码语义弱)
