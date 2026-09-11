---
name: macro-dashboard
description: Refresh data and generate the 宏观框架看板 (macro_framework.html). Use when the user wants to update/refresh the macro framework / gold stage locator dashboard, or asks to "生成/刷新宏观框架看板/黄金定位器/macro dashboard". Backfills western-macro series (UST/DXY/美股/期货) + gold-micro (COMEX库存/CFTC持仓/央行购金/FRED实际利率/NYFed期限溢价) + economic calendar (美国高重要性事件), then renders. Phase 3 north-star dashboard.
---

# 宏观框架看板 — 维护与生成

只读诊断模块(ADR-0001 围栏·**不碰交易引擎**)。输出 `data/macro_framework.html`(离线自包含,深浅色可切)。沿 JZ 因果链 利率→曲线→美元→金属→能源→权益 铺开,旗舰 = 🥇**黄金阶段定位器**(价格结构+微观紧缺+利率美元驱动→阶段+驱动三栏+置信度+操作建议)。

## 触发场景
- "刷新宏观框架看板 / 黄金定位器 / macro dashboard / 宏观看板"
- 用户要看当前黄金阶段定位 + 微观紧缺证据 + 经济日历催化剂
- 盘中快速看黄金(不想等七看板全量刷新)

## 标准流程

### 1. 更新数据(3 个 backfill,全量幂等,可反复跑)
```bash
# ① 宏观序列(UST 2Y/10Y/30Y + 美股 + 外盘期货 GC/SI/CL + 外汇 + DXY 6腿重算)
PYTHONIOENCODING=utf-8 python scripts/backfill_western_macro.py
# ② 黄金微观紧缺(COMEX库存 + CFTC投机+商业持仓 + 央行购金(实物) + FRED实际利率/通胀预期 + NYFed期限溢价)
PYTHONIOENCODING=utf-8 python scripts/backfill_gold_micro.py
# ③ 经济日历(近7天已公布 + 未来45天排期 · 美国重要性≥2 · ~52个请求 ~40s)
PYTHONIOENCODING=utf-8 python scripts/backfill_economic_calendar.py
```
任一步失败不影响其余(各自独立);全部幂等,中断重跑即可。~2-3 分钟完成。

### 1b. (可选) 先看数据新鲜度
```bash
PYTHONIOENCODING=utf-8 python -c "
from stockagent.config import get_config
from stockagent.data.store import Store
st = Store(get_config().db_path)
checks = [
    ('UST/DXY/期货', st.last_western_date('fut','GC')),
    ('FRED实际利率', st.last_western_date('fred','DFII10')),
    ('ACM期限溢价', st.last_western_date('nyfed_acm','ACMTP10')),
    ('COMEX库存', st.get_comex_inventory('GC').index[-1] if len(st.get_comex_inventory('GC')) else '无'),
    ('CFTC投机', st.get_cftc_position('GC').index[-1] if len(st.get_cftc_position('GC')) else '无'),
    ('央行购金', st.get_cb_gold('CN').index[-1] if len(st.get_cb_gold('CN')) else '无'),
]
for name, dt in checks: print(f'  {name}: {dt}')
print('  经济日历覆盖:', st.get_economic_calendar(min_importance=0)['date'].max() if len(st.get_economic_calendar(min_importance=0)) else '无')
"
```
任一项日期非最新交易日 → 重跑对应 backfill。

### 2. 生成看板
```bash
PYTHONIOENCODING=utf-8 python scripts/macro_framework_report.py
# → data/macro_framework.html(总览表+🥇黄金定位器+微观紧缺+经济日历+因果链分节点图,纯数据无LLM)
```
打开:双击 `data/macro_framework.html`,或终端 `start data/macro_framework.html`。

### 3. 汇报(给用户)
- **🥇 黄金阶段定位器**:阶段(筑底/反弹/趋势/头部/回调) + bull_intact(底层牛市在否) + 置信度 + 操作建议(双向防守)
- **驱动三栏**:底层(央行购金/期限溢价/实际利率方向) · 中期(2s10s/实际利率/DXY) · 短期(投机是否泡沫/商业是否逼空/库存是否紧缺)
- **利率节点**:美债2Y/10Y/30Y + 实际利率 + 通胀预期 + 期限溢价 的当前位置和趋势
- **📰 经济日历**:近7天美国高重要性数据(公布vs预期=数据真伪) + 未来45天催化剂(FOMC/CPI/非农)
- 任何数据异常(FRED/NYFed 被拦 / DXY 外汇缺腿 / CFTC 停更)

## 已知坑(数据源限制)
- **DXY 外汇**:AkShare push2his 常被网拦 → 自动 fallback ECB/Frankfurter(免费无 key,6腿重算)。若 ECB 也失败,DXY 缺 → 美元/黄金相关分析受影响(本机/换网络重跑)。
- **FRED / NY Fed**:`backfill_gold_micro.py` 含 2 个非-akshare 免费源(FRED CSV + NY Fed XLS)。本网若拦 → 该组跳过(逐组容错),其余照跑。
- **GOFO / 全球 ETF**:无免费源(LBMA 2015 停发 / GLD·IAU 无公开 API),看板相应位置标注缺口。
- **经济日历远期**:百度财经日历(`news_economic_baidu`)对 >3 周的远期日期可能未排上 FOMC(直查 9/16 返回空);临近会自动补全。前看 45 天覆盖到能抓到的最远事件。
- **经济日历次指标缺公布值**:续请失业金/国债竞拍等次指标源永不回填 actual→「已公布/即将公布」**按 asof 时刻判定**(2026-09-11 修复,旧行只看 actual 有无、已过时点滞留未来排期):已过时点进「已公布」表公布列显示「—」(脚注注明源未收录),属正常非缺陷。
- **CFTC 周频**:CFTC 持仓报告每周二发布(滞后),非日频。

## 与 dashboards skill 的关系
- **本 skill**(`macro-dashboard`):只刷宏观框架看板一个(快,~3 分钟)。
- **`dashboards`**:全刷八看板壳页(研究/指数/个股/大宗商品/宏观框架/国内宏观/仓位 + ⏸候选池暂停中,实际刷七个;冷启动 ~1hr,日常 ~15-20 分钟)。
- 日常盘中看黄金 → 本 skill;盘前/周五全套 → `dashboards`。

## 端点真相
| 数据 | 端点 | 备注 |
|---|---|---|
| UST/美股/期货 | AkShare bond_zh_us_rate / index_us_stock_sina / futures_foreign_hist | 全量幂等 |
| DXY | ECB/Frankfurter forex → 6腿重算 | AkShare push2his 备选(常被拦) |
| COMEX库存 | futures_comex_inventory(symbol=黄金/白银) | 日频,2021+ |
| CFTC投机 | macro_usa_cftc_c_holding | 周频,1986+(非商业/large speculator) |
| CFTC商业 | macro_usa_cftc_merchant_goods_holding | 周频(merchant/commercial 套保) |
| 央行购金 | macro_china_foreign_exchange_gold | 月频,实物万盎司存量 |
| 实际利率 | FRED fredgraph.csv?id=DFII10 | 免费 CSV,无 key |
| 通胀预期 | FRED fredgraph.csv?id=T10YIE | 同上 |
| 期限溢价 | NY Fed ACMTermPremium.xls | ACM 模型,10yr=ACMTP10 |
| 经济日历 | news_economic_baidu(date=YYYYMMDD) | 百度财经日历,逐日 |
