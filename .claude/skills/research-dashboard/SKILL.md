---
name: research-dashboard
description: Refresh, backfill, and generate the ETF 行业研究·性价比看板 (research_report.html). Use when the user wants to update/refresh the research dashboard, suspects its data is stale or incomplete, wants a current report (e.g. before a Friday review), or asks to "生成/刷新看板". Checks data freshness vs the latest trading day and auto-backfills gaps.
---

# ETF 行业研究·性价比看板 — 维护与生成

纯研究报告模块（只读，不碰交易引擎）。输出 `data/research_report.html`：每只 ETF 的份额 vs 累计净值、行业PE分位、三因子(估值/筹码/趋势)性价比排名。

## 触发场景
- "刷新看板 / 数据旧了 / 生成看板 / 这周五要看报表 / research dashboard"
- 用户要当前数据，但可能上次更新后已过数天（需补到最新交易日）

## 标准流程（按顺序）

### 1. 检查数据新鲜度
```bash
PYTHONIOENCODING=utf-8 python scripts/dashboard_data_check.py
```
读输出：
- 基准(510300)最新交易日 = 系统知道的最新交易日（今天若是周末/节假日，会是上一个交易日）
- 每只 ETF 的 price/shares/nav 最新日期 vs 基准日；`[份额旧]/[净值旧]`=落后，`[无份额]/[无净值]`=完全缺失
- PE 最新日期；`PE旧` = 落后 >14 天

### 2. 若有落后/缺失 → 自动补齐到最新交易日
```bash
PYTHONIOENCODING=utf-8 python scripts/dashboard_data_check.py --fix
```
这会按缺口补：价格(update_all)→份额(backfill 缺口 SSE+SZSE)→净值(增量 per-symbol)→PE(仅 >14天旧才补，cninfo限流)。补完自动复查。
> 用户明确要"当前报表"时，**默认就跑 --fix**，不必逐项问。

### 3. 生成看板
```bash
PYTHONIOENCODING=utf-8 python scripts/research_report.py            # 全27只 + 全池格局LLM综合(1次调用，默认)
PYTHONIOENCODING=utf-8 python scripts/research_report.py --no-llm   # 全规则模板，最快
PYTHONIOENCODING=utf-8 python scripts/research_report.py --llm-per-etf  # 再加逐只LLM(27次，慢，极少需要)
```
LLM 用法：**默认只做 1 次全池格局综合**（顶部蓝框，跨标的归纳——这才是 LLM 相对规则的增量价值）。逐只解读默认走规则模板（表里已有相位标签够了）。零预测硬护栏：含禁词(预计/有望/看好/后市/将会/看涨/看跌)自动回退规则模板。

### 4. 汇报（给用户）
- 参与排名 N/27（数据不足的被排除，单列）
- 性价比 top 3 / bottom 3 + 各自相位标签
- 任何本轮新发现的数据问题（如某 ETF 新增缺失、PE 掉到很稀疏等）

## 已知坑（不必"修"，是数据源限制，看板已优雅处理）
- **515880 通信 无份额历史**：`fund_etf_scale_sse` 的 869 只里不含它 → 看板自动画当日份额虚线参考。无法补历史（免费源没有）。
- **cninfo 行业PE**：限流，需 `--sleep 8 --step≥7`；历史仅 ~2023 起（约3年）。要加密：`python scripts/research_report.py --backfill pe --step 7 --sleep 8`（后台~30min）。
- **NAV 拆分断崖**：画的是 acc_nav(累计净值，拆分连续)，不是 unit_nav(原始，有断崖)。`fetch_etf_nav` 主接口失败会自动回退 `fund_open_fund_info_em`。
- **份额历史深市**：`fund_etf_scale_szse()` 是 spot 无历史，真正历史在 `fund_scale_daily_szse`（按月增量回填，已在 backfill_etf_scale source=szse 里）。

## 排名规则提醒
`data_sufficient` 标志：缺"应有"的因子（如份额历史不足→chip算不出）→ 排除出排名；"不适用"的因子（宽基/QDII/商品无PE）→ 保留(双因子)。所以排名是同类相比。

## 数据回填（一次性历史，非日常）
```bash
# 一次性补全部历史（新环境/重置后）
python scripts/research_report.py --backfill nav --start 2021-01-01
python scripts/research_report.py --backfill pe --start 2023-01-01 --step 7 --sleep 8   # cninfo从2023起
python scripts/research_report.py --backfill scale --source szse --start 2021-01-01      # 深市份额
python scripts/research_report.py --backfill scale --source sse --start 2021-01-01       # 沪市份额
```

## 端点真相（探针确认，akshare 1.18.64）
| 数据 | 可用端点 | 备注 |
|---|---|---|
| ETF NAV | `fund_etf_fund_info_em(fund,start,end)` → 失败回退 `fund_open_fund_info_em` | 单位+累计净值 |
| SSE 份额 | `fund_etf_scale_sse(date)` | 按日，不含515880 |
| SZSE 份额 | `fund_scale_daily_szse(start,end,symbol="ETF")` | 按区间，按月分块 |
| 行业PE | `stock_industry_pe_ratio_cninfo(symbol="证监会行业分类",date)` | 按日全行业快照，限流 |
| 死路 | ~~stock_index_pe_lg~~(只宽基)、~~stock_zh_index_value_csindex~~(仅25天)、~~fund_etf_scale_szse()~~(spot无历史) | |
