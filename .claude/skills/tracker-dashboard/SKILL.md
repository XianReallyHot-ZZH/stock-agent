---
name: tracker-dashboard
description: Refresh data and generate the 指数择时层看板 (index_timing.html, 9 sections). Use when the user wants to update/refresh the index-timing / broad-market dashboard, or asks to "生成/刷新指数看板/大盘择时看板/index timing dashboard". Backfills 6 broad-index daily(含上证综指000001) + 沪深300/上证50/中证500 PE/PB + 全市场 PB + 两市成交额(baostock) + 上交所融资融券(⑨恐惧贪婪·杠杆成分), then renders the dashboard. Phase 1-B of the tracker module.
---

# 指数择时层看板 — 维护与生成

只读诊断模块(基于课程 S12-13 + 周期律/量价实证,**不碰交易引擎**)。输出 `data/index_timing.html`(离线自包含 ~10MB,深浅色可切),九节:③估值开关(PE/PB时序图) · ⑥市场温度·大小盘温差 · ④蓝筹vs成长 · ②趋势状态 · ⑤突破跌破信号 · ①偏离极值曲线 · ⑦相对周期律(创业板vs上证点差) · ⑧成交量地量监测 · ⑨恐惧贪婪指数(5成分0-100复合,温度计非开关)。

## 触发场景
- "刷新指数看板 / 大盘择时看板 / tracker dashboard / 生成指数看板 / 指数择时"
- 用户要看当前大盘择时诊断(估值位置 / 趋势 / 偏离极值 / 大小盘温差)

## 标准流程

### 1. 更新数据(幂等,全量覆盖,可反复跑)
```bash
PYTHONIOENCODING=utf-8 python scripts/backfill_index.py
```
抓 6 宽基日线(sina,含上证综指000001=⑦基准) + 沪深300/上证50/中证500 PE+PB(legulegu) + 全市场 PB + 两市成交额(baostock sh.000001+sz.399001=⑧地量数据源,首次拉历史到 1991 起、稍慢) + 上交所融资融券(`stock_margin_sse`按年分段,⑨恐惧贪婪·杠杆成分,历史自 2010-03)。可选 `--turnover`/`--margin` 只刷单项。每次全量覆盖。

或先看新鲜度(指数段):
```bash
PYTHONIOENCODING=utf-8 python scripts/dashboard_data_check.py
```
读「指数层(择时)」段:6 宽基日线 / PE / PB / 全市场 PB 是否到最新交易日。

### 2. 生成看板
```bash
PYTHONIOENCODING=utf-8 python scripts/index_timing_report.py
# → data/index_timing.html(~10MB,离线自包含,9 section)
```
打开:双击 `data/index_timing.html`,或终端 `start data/index_timing.html`。

### 2b. (可选) 地量深度验证报告
```bash
PYTHONIOENCODING=utf-8 python scripts/validate_volume_bottom.py
# → data/volume_bottom_study.html(⑧ event-study:各 horizon 胜率/量底→价底天数/极端度分桶)
```

### 3. 汇报(给用户)
- **估值开关 zone**(低位·可激进 / 高位·宜保守 / 结构分化·宜观望 / 中位·中性)+ 沪深300 PE/PB 分位(③图看历史位置)
- **市场温度**:大小盘温差(同步 / 小盘偏贵 / 小盘偏便宜)
- **蓝筹 vs 成长**仓位倾向
- **6 宽基趋势**:60 日线上下 / 突破跌破档位 / 震荡市 flag / 偏离度
- **偏离极值**:接近历史正/负极值的指数(S13 套利区)
- **⑦ 相对周期律**:创业板 vs 上证 点差在 5 年包络的位置(极点才有方向 / 中枢无 edge)
- **⑧ 成交量地量**:成交额/MA250 + 量底→价底时效(~1 月);**地量≠高胜率买点**(实证:各 horizon 胜率~50%,仅时效成立)
- **⑨ 恐惧贪婪指数**:5 成分(动量/流动性/波动率/估值/杠杆)0-100 复合 + 五档标签;**温度计非开关**(同 ⑧ 实证无 edge,只读不喂引擎)

## 已知坑(数据源限制,看板已处理)
- **创业板指/科创50 无 PE/PB**:`stock_index_pe/pb_lg` 只支持沪深300/上证50/中证500。这两只仍能算日线趋势/偏离(无估值分位)。
- **沪深300 PB 同口径**:估值开关 zone 用沪深300 PE+PB(同口径)判断结构分化(PE=PB/ROE → PE高/PB低 = ROE偏弱);全市场 PB 单独作「大小盘温差」。
- **legulegu 限流**:PE/PB 抓取带 sleep,偶尔慢,重跑即可。
- **两市成交额(baostock)**:⑧地量数据源走 baostock,偶发 login 失败 → 重跑即可(幂等 upsert);首次拉历史(到 1991)稍慢。上证综指 000001 无 PE/PB(同创业板/科创50),⑦只用其日线算点差。
- **历史起点**:科创50 从 2020、创业板指从 2010(各自偏离极值的历史范围较短)。

## 端点真相(akshare 1.18.64)
| 数据 | 端点 | 备注 |
|---|---|---|
| 宽基日线 | `stock_zh_index_daily(symbol="sh000300")` | sina,sh/sz 前缀(000xxx→sh, 399xxx→sz);6 宽基含上证综指 sh000001(⑦基准) |
| 两市成交额 | baostock `sh.000001`+`sz.399001` 的 `amount` | ⑧地量数据源;两所 amount 求和=两市(交易所总数,非成分和),历史到 1991 |
| 沪深300 PE | `stock_index_pe_lg(symbol="沪深300")` | legulegu,中文名,取「滚动市盈率」 |
| 沪深300 PB | `stock_index_pb_lg(symbol="沪深300")` | 同上,「市净率」 |
| 全市场 PB | `stock_a_all_pb()` | legulegu,全A PB 中位数 + 历史分位 |
| 上交所融资融券 | `stock_margin_sse(start_date,end_date)` | ⑨恐惧贪婪·杠杆成分;沪市信用交易日级总量,**单次封顶 ~2000 行→按年分段**;深市总量历史不可得(`stock_margin_szse` 仅当日快照),v1 仅沪市 |
| 不支持 | 创业板指/科创50 的 PE/PB(`stock_index_pe/pb_lg` 支持集不含) | 用日线趋势/偏离代替 |
