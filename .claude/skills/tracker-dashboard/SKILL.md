---
name: tracker-dashboard
description: Refresh data and generate the 指数择时层看板 (index_timing.html, 6 sections). Use when the user wants to update/refresh the index-timing / broad-market dashboard, or asks to "生成/刷新指数看板/大盘择时看板/index timing dashboard". Backfills 5 broad-index daily + 沪深300 PE/PB + 全市场 PB, then renders the dashboard. Phase 1-B of the tracker module.
---

# 指数择时层看板 — 维护与生成

只读诊断模块(基于课程 S12-13,**不碰交易引擎**)。输出 `data/index_timing.html`(离线自包含,深浅色可切):估值开关 · 市场温度 · 蓝筹vs成长 · 趋势状态 · 突破跌破信号 · 偏离极值曲线。

## 触发场景
- "刷新指数看板 / 大盘择时看板 / tracker dashboard / 生成指数看板 / 指数择时"
- 用户要看当前大盘择时诊断(估值位置 / 趋势 / 偏离极值 / 大小盘温差)

## 标准流程

### 1. 更新数据(幂等,全量覆盖,可反复跑)
```bash
PYTHONIOENCODING=utf-8 python scripts/backfill_index.py
```
抓 5 宽基日线(sina) + 沪深300/上证50/中证500 PE+PB(legulegu) + 全市场 PB。每次全量覆盖。

或先看新鲜度(指数段):
```bash
PYTHONIOENCODING=utf-8 python scripts/dashboard_data_check.py
```
读「指数层(择时)」段:5 宽基日线 / PE / PB / 全市场 PB 是否到最新交易日。

### 2. 生成看板
```bash
PYTHONIOENCODING=utf-8 python scripts/index_timing_report.py
# → data/index_timing.html(6.6MB,离线自包含)
```
打开:双击 `data/index_timing.html`,或终端 `start data/index_timing.html`。

### 3. 汇报(给用户)
- **估值开关 zone**(低位·可激进 / 高位·宜保守 / 结构分化·宜观望 / 中位·中性)+ 沪深300 PE/PB 分位
- **市场温度**:大小盘温差(同步 / 小盘偏贵 / 小盘偏便宜)
- **蓝筹 vs 成长**仓位倾向
- **5 宽基趋势**:60 日线上下 / 突破跌破档位 / 震荡市 flag / 偏离度
- **偏离极值**:接近历史正/负极值的指数(S13 套利区)

## 已知坑(数据源限制,看板已处理)
- **创业板指/科创50 无 PE/PB**:`stock_index_pe/pb_lg` 只支持沪深300/上证50/中证500。这两只仍能算日线趋势/偏离(无估值分位)。
- **沪深300 PB 同口径**:估值开关 zone 用沪深300 PE+PB(同口径)判断结构分化(PE=PB/ROE → PE高/PB低 = ROE偏弱);全市场 PB 单独作「大小盘温差」。
- **legulegu 限流**:PE/PB 抓取带 sleep,偶尔慢,重跑即可。
- **历史起点**:科创50 从 2020、创业板指从 2010(各自偏离极值的历史范围较短)。

## 端点真相(akshare 1.18.64)
| 数据 | 端点 | 备注 |
|---|---|---|
| 宽基日线 | `stock_zh_index_daily(symbol="sh000300")` | sina,sh/sz 前缀(000xxx→sh, 399xxx→sz) |
| 沪深300 PE | `stock_index_pe_lg(symbol="沪深300")` | legulegu,中文名,取「滚动市盈率」 |
| 沪深300 PB | `stock_index_pb_lg(symbol="沪深300")` | 同上,「市净率」 |
| 全市场 PB | `stock_a_all_pb()` | legulegu,全A PB 中位数 + 历史分位 |
| 不支持 | 创业板指/科创50 的 PE/PB(`stock_index_pe/pb_lg` 支持集不含) | 用日线趋势/偏离代替 |
