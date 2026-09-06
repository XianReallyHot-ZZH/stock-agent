---
name: commodity-dashboard
description: Refresh data and generate the 大宗商品看板 (commodity.html, 6 sections). Use when the user wants to update/refresh the commodity dashboard, or asks to "生成/刷新大宗商品看板/商品看板/商品面板/比价/商品雷达/商品ETF错配". Backfills 国际基准 (LME铜CAD/LME铝AHD/LME锌ZSD/COMEX金银/WTI/CBOT豆粕玉米, sina 外盘) + 中证商品指数 (ccidx 官方总览; 南华 akshare 端点已死) + 投资标的 NAV (非池内:有色期货159980/能化159981/豆粕159985/白银LOF161226/南方原油501018 → etf_nav), then renders. 第八看板, 2026-09 从个股诊断拆出; 国内 17 品种日线/夜盘快照仍归 backfill_stock_data --comm. 永不喂引擎.
---

# 大宗商品看板 — 维护与生成

商品周期观测温度计 + 周期股择时深化底座(第八看板,2026-09 从个股诊断看板的周期 tab 拆出——职责分离:
商品研究做大在本看板,个股看板聚焦股票本身,只留 🧭 商品环境速览行)。输出 `data/commodity.html`
(离线自包含,深浅色可切默认浅色),六 section:

1. **🚦 异动雷达** — 双段:⚠快腿(20日动量≥±10% + 60日新高/新低=研究排队,实证:追买跑输)/
   ⛔慢腿(偏离分位≥95%/≤5%=追高风险)。**国内序列口径**(与 validate_commodity_speed 实证、
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

## 触发场景
- "刷新大宗商品看板 / 商品看板 / 商品面板 / 比价 / 商品雷达 / 商品ETF错配"
- 用户要看商品环境、某品种国际/国内对照、金银比等比价、商品 ETF 错配时

## 标准流程

### 1. 更新数据(幂等,可反复跑)
```bash
PYTHONIOENCODING=utf-8 python scripts/backfill_commodity.py        # 国际基准 + 指数 + 标的NAV(~1-2min)
PYTHONIOENCODING=utf-8 python scripts/backfill_commodity.py --targets  # 只投资标的 NAV
```
国内 17 品种日线/夜盘快照(面板的隔夜列、雷达口径)归 `backfill_stock_data.py --comm`
(dashboard_data_check --fix 6.11/6.12/6.13 三腿都已自动带;标的 NAV 只拉非池内 5 只,
池内标的随研究看板 NAV 腿走不重复拉;QDII 501018 的 NAV 源滞后 1-2 天属正常)。

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

## 隔离纪律
只读旁路·**永不喂引擎**;commodity→tracker 单向 import 原语(judge_commodity/commodity_dev_stats/
commodity_radar/fig_json_readable),与 pool/china_macro 同向,不 re-export。
**传导链不动**:commodity_map→leading.commodity_signal/alignment→positioning_score 仍吃国内价
(commodity_price 表);面板主语(国际)与个股卡判定(国内)偶发分歧是两个用途,读图说明已注明。
温度计非开关:全部 section 观测非信号,无微信推送(M1/M2 股票告警仍在个股看板双通道)。

## 二期剩余(挂号)
- 库存/仓单、期限结构(近月-远月展期收益)、基差——**须先过 event-study**(支撑位式礼遇)
  才配进面板/雷达;候选腿:交易所仓单/社会库存(akshare 有,口径与限流待验)
- 豆粕/能化/有色期货 ETF 以 research_only 进 etf_pool.yaml 的评估:**结论不进池**
  (期货ETF NAV 含展期结构,偏离度/筹码语义弱;黄金 518880 进池是现货单品种对标)——
  映射表直读 etf_nav 已够,进池收益低于维护成本
