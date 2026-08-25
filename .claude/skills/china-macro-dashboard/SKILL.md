---
name: china-macro-dashboard
description: Refresh data and generate the 国内宏观看板 (china_macro.html, 7 sections). Use when the user wants to update/refresh the domestic-macro / China macro dashboard, or asks to "生成/刷新国内宏观看板/货币条件看板/M2看板/利率看板/政策日历/社融nowcast/通胀/PMI". Backfills Shibor + FDR007/FR 回购定盘 + LPR + 中债国债期限结构 + 央行资产负债表 (金十) + 国债/地方债发行明细 (cninfo 逐券) + 通胀/实体五腿 (CPI/PPI/PMI/社零/工业增加值), then renders. 第七看板, 与宏观框架(海外宏观)对称; 永不喂引擎.
---

# 国内宏观看板 — 维护与生成

中国本土宏观观测层(第七看板,与宏观框架=海外宏观对称;规划与端点真相见 `docs/EXECUTION_PLAN-国内宏观.md`)。
输出 `data/china_macro.html`(离线自包含 ~6MB,深浅色可切),七节:
①货币信用(M2/M1 同比+剪刀差+社融脉冲+episode 状态机+实证结论 meta 活注入——原指数择时⑪已并入,此处唯一入口)
②利率与流动性(FDR007=央行政策目标利率区/Shibor/LPR/中债 10Y−2Y + OMO/MLF 月度净投放近似)
③政策日历(硬编码典型时点 → 下次时点+倒计时;观点结算归 docs/CLAIMS_LEDGER.md)
④社融可观测成分(政府债=国债+地方债逐券月度堆叠+月内累计 vs 近12月均值 + 社融分项[贷款/企业债/股票]历史分布——**观测非预测**,信贷黑箱诚实留白)
⑤会议→M2 转向历史回放(锚点月后 3 月 M2 上行概率;实证:四锚点全 42-44%——「会后放水」叙事无统计支持)
⑥通胀(CPI/PPI 同比 + PPI−CPI 上下游剪刀差)
⑦实体(官方制造业 PMI 荣枯线 + 社零/工业增加值同比;工业增加值源端滞后~1年,图注说明)。

## 触发场景
- "刷新国内宏观 / 货币条件 / M2 看板 / 政策日历 / china macro dashboard"
- 用户要看当前 M2/利率/政策事件时点

## 标准流程

### 1. 更新数据(幂等,全量覆盖,可反复跑)
```bash
PYTHONIOENCODING=utf-8 python scripts/backfill_china_macro.py          # 全刷(发行明细腿较慢)
PYTHONIOENCODING=utf-8 python scripts/backfill_china_macro.py --rates # 只利率四腿
PYTHONIOENCODING=utf-8 python scripts/backfill_china_macro.py --cb    # 只央行资产负债表
PYTHONIOENCODING=utf-8 python scripts/backfill_china_macro.py --lgb   # 只地方债发行明细(月窗分段)
PYTHONIOENCODING=utf-8 python scripts/backfill_china_macro.py --tsy   # 只国债发行明细
PYTHONIOENCODING=utf-8 python scripts/backfill_china_macro.py --real  # 只通胀/实体五腿(月度)
```
货币条件(M2/M1/社融)的数据腿在 `backfill_index.py --money`(原⑪数据腿,国内宏观看板①为唯一消费方;社融分项扩列也随它刷)。

### 2. 生成看板
```bash
PYTHONIOENCODING=utf-8 python scripts/china_macro_report.py --no-open
# → data/china_macro.html(七 section,深浅色可切)
```
打开:双击 `data/china_macro.html`,或经壳页 `data/index.html` 左侧导航「🏛️ 国内宏观」。

### 3. 汇报(给用户)
- **M2 同比 + episode 状态**(下行确认/触底回升/见顶回落;实证:公布滞后吃掉几乎全部触底回升 edge,温度计非开关)
- **FDR007** 最新(持续低于政策利率=水充裕/偏高=紧)+ Shibor O/N
- **LPR 1Y/5Y** + 中债 10Y 与 10Y−2Y 期限利差
- **OMO/MLF 月度净投放**(近似,滞后~1月)
- **社融可观测成分**:本月政府债(国债+地方债)累计 vs 近12月均值(观测非预测;信贷黑箱)
- **会议锚点回放**:各锚点会后 3 月 M2 上行概率(实证 42-44%,会后放水叙事无统计支持)
- **通胀**:CPI/PPI + PPI−CPI 剪刀差(正=涨价停在上游,下游利润承压)
- **实体**:官方制造业 PMI vs 50 荣枯线 + 社零/工业增加值同比
- **下次政策事件倒计时**(≤14 天标红:金融数据公布/LPR/政治局/中央经济工作会议…)

## 已知坑(端点真相 · 2026-08-24 探针)
- 利率/实体腿金十源、债券明细 cninfo 源,偶发被拦 → 脚本内指数退避重试,重跑即可。
- `repo_rate_hist` **需日期窗口参数** → fetcher 按年分段拉(同 `fetch_market_margin` 先例),全史 2020-09 起。
- **地方债/国债明细**(`bond_local_government_issue_cninfo`/`bond_treasure_issue_cninfo`)2021-09 起,cninfo 限流 → 按月窗分段+逐窗重试,失败月跳过重跑自愈;**逐券口径(含再融资/跨市场),绝对量级未与官方月报交叉校验——④只作月度节奏/月内累计的相对观察**。
- **官方 PMI 用 `macro_china_pmi`(月份表,2008 起正常更新)**;`macro_china_pmi_yearly`(报告族,2005 起)源停更至 2025-08 仅作并接补早段。**工业增加值(报告族)源端滞后~1 年**——图注说明,读趋势用。**财新 PMI 弃用**(源日期语义混杂,新旧行发布日/参考月口径不一,防错位)。
- **永久待补(2026-08-25 终审)**:日度 OMO 净投放、票据转贴利率——免费源确认无;OMO 用央行资产负债表月度差分近似(②),够用。国债发行明细已于远期批补齐、政府债已入 ④。
- LPR 表 1991 起含旧贷款基准利率(RATE_1/2 列);中债表只存中国列(美国列归 western_macro 域)。
| 端点 | 用途 | 起点 |
|---|---|---|
| `macro_china_shibor_all` | Shibor 8 期限 | 2015-05 |
| `repo_rate_hist(start,end)` | FR/FDR 定盘(FDR007=政策目标利率) | 2020-09 |
| `macro_china_lpr` | LPR+旧基准 | 1991 |
| `bond_zh_us_rate(start_date)` | 中债 2/5/10/30Y+10Y−2Y | 1990 |
| `macro_china_central_bank_balance` | 央行表(claim_odc=OMO/MLF 余额) | 1993 |
| `bond_local_government_issue_cninfo` | 地方债发行明细(v2 社融可观测成分) | 2021-09 |
| `bond_treasure_issue_cninfo` | 国债发行明细(远期批补齐政府债另一半) | 2021-09 |
| `macro_china_cpi` / `macro_china_ppi` / `macro_china_consumer_goods_retail` | CPI/PPI/社零 同比('YYYY年MM月份'式) | 2008/2006/2008 |
| `macro_china_pmi` / `macro_china_industrial_production_yoy` | 官方制造业 PMI / 工业增加值(报告族,参考月规则见 fetcher) | 2008/1990 |

## 纪律
只读旁路·**永不喂交易引擎**;先行代理未过 event-study 礼遇前一律观察项,不出现「预测/信号」措辞;
政策观点打分归 `docs/CLAIMS_LEDGER.md`(本看板只放日历事实)。
