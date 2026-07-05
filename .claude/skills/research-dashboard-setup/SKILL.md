---
name: research-dashboard-setup
description: Cold-start setup for the ETF 行业研究·性价比看板 on a fresh clone / new machine. Use when the user just cloned the repo (no data yet — the SQLite DB is gitignored), asks "how to run the dashboard / 首次运行 / 新机器 / 准备工作", or the dashboard render fails with empty/missing data. Installs deps, ensures .env, and runs the full historical backfill (~1hr) then renders.
---

# ETF 行业研究看板 — 新机器冷启动

## 何时用这个（而不是 research-dashboard skill）
- **本 skill**：fresh clone / DB 为空 / 首次跑起来（需要从零回填全部历史数据，~1hr）
- **research-dashboard skill**：已经跑起来过，日常刷新/生成看板（增量补缺口）

判断依据：看 `data/stockagent.sqlite` 是否存在且基准(510300)有价格。空 → 本 skill。

## 为什么要回填
看板的数据源 SQLite 被 `.gitignore` 排除（DB 不进 git），所以新克隆的仓库**没有任何数据**。直接 `python scripts/research_report.py` 会得到全 NaN 的空看板。必须先回填：价格/份额/净值/行业PE。

## 一键冷启动（推荐）
```bash
pip install -r requirements.txt                 # 含 plotly（看板依赖，已加入 requirements）
python scripts/setup_research_dashboard.py       # 全量回填 + 生成看板（~1hr）
python scripts/setup_research_dashboard.py --skip-pe   # 想快速预览：跳过~30min的PE（估值NaN，双因子排名照跑）
```
脚本依次做：依赖检查 → 确保 .env → 价格+日历 → 份额(SSE+SZSE) → NAV → 行业PE → 渲染。**幂等**，中途断了重跑会续填。

## 手动分步（脚本失败或想分阶段时）
```bash
# 0. 环境
pip install -r requirements.txt
cp .env.example .env          # LLM key 可选；不填走规则模板

# 1. 价格 + 交易日历（必须最先——份额/PE 回填依赖基准价格日期做时间线）
python scripts/update_data.py

# 2. 份额（SSE 按日 + SZSE 按月增量）
python scripts/research_report.py --backfill scale --source sse --start 2021-01-01
python scripts/research_report.py --backfill scale --source szse --start 2021-01-01

# 3. 净值（真NAV，全ETF，快）
python scripts/research_report.py --backfill nav --start 2021-01-01

# 4. 行业PE（cninfo，限流，最慢；历史只到~2023）
python scripts/research_report.py --backfill pe --start 2023-01-01 --step 7 --sleep 8

# 5. 生成
python scripts/research_report.py
```

## 各 stage 耗时 & 注意
| stage | 数据 | 耗时 | 备注 |
|---|---|---|---|
| 价格+日历 | update_data.py | ~5min | 29 symbols，必须最先 |
| 份额 SSE | fund_etf_scale_sse 按日 | ~9min | 515880 通信不在源头869只里（已知缺，看板画参考虚线）|
| 份额 SZSE | fund_scale_daily_szse 按月 | ~9min | 深市历史（创业板/纳指等）|
| NAV | fund_etf_fund_info_em | ~2min | 真 NAV，主接口失败自动回退备用接口 |
| 行业PE | cninfo 按日全行业 | ~30min | 限流需 sleep 8 + step 7；历史~2023起约3年；可 --skip-pe |
| 渲染 | research_report.py | ~10s | 默认含全池LLM综合(1次调用) |

## 验证
- `python scripts/dashboard_data_check.py` 应显示：price/shares/nav 新鲜 27/27（515880 份额全缺是已知），PE 有 100+ 日期
- 打开 `data/research_report.html`，应见 27 只 ETF 排名 + 全池格局综合 + 各 ETF 的份额/净值/PE 图

## 常见坑
- **plotly 未装**：报 `ModuleNotFoundError: plotly` → `pip install plotly`（已在 requirements）
- **PE 全 NaN / 看板估值列空**：PE 回填没跑或被 cninfo 限流刷掉 → 重跑 stage 4，或检查 cninfo 是否被 IP 限流（换网/等待）
- **看板全 NaN**：价格没回填（stage 1 没跑）→ 先 `python scripts/update_data.py`
- **AkShare 报错/超时**：AkShare 抓 eastmoney/sina 不稳定，重跑即可（幂等）；持续失败换网络
- **深市 ETF 份额还是只有1天**：stage 2 的 `--source szse` 没跑 → 单独跑它
- **没 LLM key**：不报错，看板顶部「全池格局」走规则模板（grounded，无预测）
