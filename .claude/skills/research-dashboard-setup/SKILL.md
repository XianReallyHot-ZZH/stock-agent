---
name: research-dashboard-setup
description: Cold-start setup for the ETF 行业研究·择时跟踪看板 on a fresh clone / new machine. Use when the user just cloned the repo (no data yet — the SQLite DB is gitignored), asks "how to run the dashboard / 首次运行 / 新机器 / 准备工作", or the dashboard render fails with empty/missing data. Installs deps, ensures .env, runs the historical backfill (NAV+份额 enough for 偏离度/剪刀差/筹码/资金流向; 业绩预期底座——指数成分+一致预期快照+三环链——自动带; PE optional/unused now) then renders.
---

# ETF 行业研究·择时跟踪看板 — 新机器冷启动

## 何时用这个（而不是 research-dashboard skill）
- **本 skill**：fresh clone / DB 为空 / 首次跑起来（需要从零回填全部历史数据，~30min；PE 已可跳过）
- **research-dashboard skill**：已经跑起来过，日常刷新/生成看板（增量补缺口）

判断依据：看 `data/stockagent.sqlite` 是否存在且基准(510300)有价格。空 → 本 skill。

## 为什么要回填
看板的数据源 SQLite 被 `.gitignore` 排除（DB 不进 git），所以新克隆的仓库**没有任何数据**。直接 `python scripts/research_report.py` 会得到全 NaN 的空看板。必须先回填：价格/份额/净值。（PE 本看板已不用，可跳过。）

## 一键冷启动（推荐）
```bash
pip install -r requirements.txt                 # 含 plotly（看板依赖）
python scripts/setup_research_dashboard.py --skip-pe   # 回填(价格+份额+净值) + 生成看板（~30min，PE 已不用故跳过）
```
> `--skip-pe` 现在是**合理默认**：本看板跟踪偏离度/剪刀差/筹码/资金流向只需 NAV+份额，业绩预期底座（指数成分+一致预期快照+三环链）由脚本 stage 4.5 自动带，PE 不再渲染。去掉 `--skip-pe` 仍可（脚本会补 PE，~额外30min），但对本看板是浪费。
脚本依次做：依赖检查 → 确保 .env → 价格+日历 → 份额(SSE+SZSE) → NAV → (可选)行业PE → **业绩预期底座（指数成分月度快照+一致预期整表快照+三环链+业绩列重算，~2min）** → 渲染。**幂等**，中途断了重跑会续填。

## 手动分步（脚本失败或想分阶段时）
```bash
# 0. 环境
pip install -r requirements.txt
cp .env.example .env          # LLM key 已无需（解读/告警退役）；.env 仍需存在

# 1. 价格 + 交易日历（必须最先——份额回填依赖基准价格日期做时间线）
python scripts/update_data.py

# 2. 份额（SSE 按日 + SZSE 按月增量）
python scripts/research_report.py --backfill scale --source sse --start 2021-01-01
python scripts/research_report.py --backfill scale --source szse --start 2021-01-01

# 3. 净值（真NAV，全ETF，快）—— 偏离度算在 acc_nav 上
python scripts/research_report.py --backfill nav --start 2021-01-01

# 4. 行业PE（本看板已不用，可跳过；仅当别的用途要 PE 才补）
# python scripts/research_report.py --backfill pe --start 2023-01-01 --step 7 --sleep 8

# 5. 生成
python scripts/research_report.py
```

## 各 stage 耗时 & 注意
| stage | 数据 | 耗时 | 备注 |
|---|---|---|---|
| 价格+日历 | update_data.py | ~5min | 全 symbols，必须最先 |
| 份额 SSE | fund_etf_scale_sse 按日 | ~9min | 515880 通信不在源头 869 只里（已知缺，看板画参考虚线）|
| 份额 SZSE | fund_scale_daily_szse 按月 | ~9min | 深市历史（创业板/纳指等）|
| NAV | fund_etf_fund_info_em | ~2min | 真 NAV，主接口失败自动回退备用接口；偏离度用 acc_nav |
| 业绩预期底座 | stage 4.5 | ~2min | 指数成分(csindex官方权重·30指数) + 一致预期整表快照(~2800只) + 快报/正式报三环链 + etf_earnings 重算 |
| 行业PE | cninfo 按日全行业 | ~30min | **本看板已不用，可跳过**（`--skip-pe`）|
| 渲染 | research_report.py | ~10s | 纯可视化，无 LLM |

## 验证
- `python scripts/dashboard_data_check.py` 应显示：price/shares/nav 新鲜 N/N（515880 份额全缺是已知）+ 指数成分 30 个 · 一致预期快照当日
- 打开 `data/research_report.html`，应见：顶部「偏离度极端区」横幅 + 价值/成长/周期 三类 tab 分页排名表（偏离度/剪刀差/筹码/业绩预期列，表头可排序）+ 逐标的明细折叠面板（默认收起，点开见「⛓业绩预期链」三环状态条 + 份额净值图（剪刀差窗口）与净值-MA60 偏离度图（第几极值 ▲▼ 标记））。业绩预期列上=预告 label、下=一致预期 g（修正动量冷启动期显示「累积中 1/4」属正常——快照需 4 周攒满）。置顶 创业板/科创50 默认展开在最前（⭐）。右上 🌙/☀️ 可切深浅色。

## 常见坑
- **plotly 未装**：报 `ModuleNotFoundError: plotly` → `pip install plotly`（已在 requirements）
- **看板全 NaN / 偏离度空**：价格或 NAV 没回填 → 先 `python scripts/update_data.py` 再 backfill nav
- **偏离度历史不足被排除排名**：某 ETF NAV 上市短（<80 日）→ 正常（明细图仍画，只是不进排名）
- **AkShare 报错/超时**：抓 eastmoney/sina 不稳定，重跑即可（幂等）；持续失败换网络
- **深市 ETF 份额还是只有1天**：stage 2 的 `--source szse` 没跑 → 单独跑它
