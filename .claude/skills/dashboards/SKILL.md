---
name: dashboards
description: One-click refresh + generate + open all seven read-only diagnostic dashboards (ETF 行业研究 research_report.html / 指数择时 index_timing.html / 个股诊断 stock_diagnose.html / 宏观框架 macro_framework.html / 国内宏观 china_macro.html / 仓位管理 position.html / 候选个股池 stock_pool.html), then serve them from one combined shell page data/index.html (left-nav iframe switcher from scripts/dashboard_home.py). Auto-detects COLD-START (empty/thin DB → full backfill ~1hr, works on a fresh clone) vs WARM daily incremental refresh. Use when the user wants to update/refresh ALL dashboards at once, do a 盘前/周五全套看板 review, just cloned the repo and wants the dashboards running ("一键起看板 / fresh start"), or asks to "一键看板 / 刷新所有看板 / 把看板都更新一下并打开". Refreshes data, regenerates the 7 HTMLs, opens the combined shell page in the default browser. 候选个股池 stock_pool.html 的数据更新与生成已暂停 (2026-08-29, 等用户指令重启; 数据冻结在 08-27, 旧 HTML 仍可在壳页查看)——本 skill 现刷六个看板, 恢复时先 `dashboard_data_check.py --fix --pool` 补数据再 `stock_pool_report.py` 重渲染.
---

# 七看板一键刷新 + 打开（统一入口 · 冷启动感知）

七个**只读诊断**看板的统一入口（都不碰交易引擎），**fresh clone 也能一键起**：

| 看板 | 产物 | 单看板 skill |
|---|---|---|
| ETF 行业研究（择时跟踪·偏离度/剪刀差/筹码/资金流向/业绩预期） | `data/research_report.html` | `research-dashboard` |
| 指数择时（估值开关·趋势·相对周期·地量·偏离·恐贪） | `data/index_timing.html` | `tracker-dashboard` |
| 个股诊断（三类·S07 归因·S10 戴维斯·S08 避坑·时序图·🤖AI评估） | `data/stock_diagnose.html` | （无，本 skill 覆盖） |
| 宏观框架（因果链 利率→曲线→美元→金属→能源→权益 · 🥇黄金阶段定位器 · 微观紧缺） | `data/macro_framework.html` | （无，本 skill 覆盖） |
| 国内宏观（货币信用 M2/M1/社融 · 利率与流动性 Shibor/FDR007/LPR/中债期限结构/OMO · 政策日历） | `data/china_macro.html` | `china-macro-dashboard` |
| 仓位管理（估值档×预案对照·档位统计·切换事件·温度计非开关） | `data/position.html` | （无，本 skill 覆盖；数据腿复用 backfill_index.py，零新增回填） |
| 候选个股池（覆盖池~2300 只筛选漏斗·六策略表：超卖/猛×深跌/修正动量/PEAD/变脸/双击）⏸**数据更新已暂停(2026-08-29,等用户指令重启)** | `data/stock_pool.html`（冻结在 08-27，仍可看） | （无；恢复时 `--fix --pool` 补数据 + `stock_pool_report.py` 重渲染） |

七看板还有一个**总入口壳页** `data/index.html`（`scripts/dashboard_home.py` 生成，左侧导航 + iframe 装载七看板，切换不重载/记住上次选择/as_of 新鲜度标注；无数据依赖，重跑秒级）。

## 触发场景
- 日常：「刷新所有看板 / 盘前看板 / 周五全套报表 / dashboards」
- 冷启动：「刚 clone，把看板跑起来 / fresh start dashboards / 一键起看板」

## 前置
- `pip install -r requirements.txt` + `cp .env.example .env`（`setup_research_dashboard.py` 也会查依赖/.env，缺了会提示）

## 标准流程

### 0. 检测冷启动 vs 日常（读输出决定走 1a 还是 1b）
```bash
PYTHONIOENCODING=utf-8 python -c "
import sqlite3, os
p = 'data/stockagent.sqlite'
if not os.path.exists(p):
    print('COLD: 数据库不存在(fresh clone)')
else:
    try:
        n = sqlite3.connect(p).execute('SELECT COUNT(*) FROM daily_prices WHERE symbol=\"510300\"').fetchone()[0]
        print('WARM: 已有数据(510300 共', n, '根)') if n > 200 else print('COLD: 数据稀疏(510300 仅', n, '根)')
    except Exception as e:
        print('COLD:', e)
"
```
判定：输出 `COLD` → 走 **1a**（全套冷启动，首次 ~1hr）；输出 `WARM` → 走 **1b**（增量，几分钟）。
> 只有 **research 那条腿**需要分叉：冷启动要 `setup_research_dashboard.py`（全量历史，含 ~30min PE）；日常用 `dashboard_data_check.py --fix`（增量补缺口）。**index/stock 的 backfill 本就是全量幂等，冷热都一样**——两条分支都跑它们。

### 1a. 冷启动（DB 空/稀疏 · 首次 ~1hr）
```bash
# ETF 行业研究：全套历史(价格+份额+净值；PE 已不用)+ 渲染 research_report.html（自含依赖/.env 检查）
PYTHONIOENCODING=utf-8 python scripts/setup_research_dashboard.py --skip-pe   # PE 已不用，跳过省 ~30min
# 指数择时（全量幂等：7 宽基日线含000001+000852 + 沪深300 PE/PB + 全市场 PB + 两市成交额 + 融资融券 + 货币条件[国内宏观①数据腿]）
PYTHONIOENCODING=utf-8 python scripts/backfill_index.py
# 个股诊断（全量幂等：日线 / baidu PE·PB / sina 财报 / 分红 / eastmoney 预告）
PYTHONIOENCODING=utf-8 python scripts/backfill_stock_data.py
# 商品价（A 类领先信号：碳酸锂/铜/螺纹钢/黄金/原油，futures_zh_daily_sina 日频）
PYTHONIOENCODING=utf-8 python scripts/backfill_stock_data.py --comm
# 国内宏观（全量幂等：Shibor/FDR007定盘/LPR/中债期限结构 + 央行资产负债表；金十源）
PYTHONIOENCODING=utf-8 python scripts/backfill_china_macro.py
# 宏观框架（全量幂等：UST/美股/外盘期货/外汇+DXY 6腿重算）
PYTHONIOENCODING=utf-8 python scripts/backfill_western_macro.py
# 宏观框架·黄金微观紧缺（COMEX库存/CFTC投机+商业持仓/央行购金/FRED实际利率+通胀预期/NYFed期限溢价）
PYTHONIOENCODING=utf-8 python scripts/backfill_gold_micro.py
# 宏观框架·经济日历（近7天已公布+未来45天排期·美国重要性≥2·数据真伪surprise+FOMC/CPI/非农催化剂）
PYTHONIOENCODING=utf-8 python scripts/backfill_economic_calendar.py
# ⏸ 候选个股池：数据更新已暂停(2026-08-29,等用户指令重启)——冷启动腿 backfill_stock_pool.py --all 跳过不跑
```

### 1b. 日常增量（数据已存在 · 几分钟）
```bash
# ETF 行业研究：查新鲜度 → 自动补缺口到最新交易日（价格/份额/净值 + 业绩预期底座：成分月度/一致预期快照周度/三环链周度）
PYTHONIOENCODING=utf-8 python scripts/dashboard_data_check.py --fix
# 指数择时（全量幂等，同冷启动）
PYTHONIOENCODING=utf-8 python scripts/backfill_index.py
# 个股诊断（全量幂等，同冷启动）
PYTHONIOENCODING=utf-8 python scripts/backfill_stock_data.py
# 商品价（同冷启动；周期 tab 商品面板/时序图/alignment/M1-M2 依赖此数据）
PYTHONIOENCODING=utf-8 python scripts/backfill_stock_data.py --comm
# 国内宏观（同冷启动：Shibor/FDR007定盘/LPR/中债期限结构+央行资产负债表，全量幂等）
PYTHONIOENCODING=utf-8 python scripts/backfill_china_macro.py
# 宏观框架（全量幂等，同冷启动：UST/美股/外盘期货/外汇+DXY）
PYTHONIOENCODING=utf-8 python scripts/backfill_western_macro.py
# 宏观框架·黄金微观紧缺（同冷启动：COMEX/CFTC/央行/FRED实际利率/NYFed期限溢价）
PYTHONIOENCODING=utf-8 python scripts/backfill_gold_micro.py
# 宏观框架·经济日历（同冷启动：近7天已公布+未来45天排期·美国重要性≥2）
PYTHONIOENCODING=utf-8 python scripts/backfill_economic_calendar.py
# ⏸ 候选个股池：数据更新已暂停(2026-08-29,等用户指令重启)——--fix 默认跳过第7-9步(spot/行业·分红/日线
#   增量)并打印暂停提示, 顺带省掉 ~45-75min 的日线大头；恢复时跑 `--fix --pool` 一次性补齐落后增量
```
任一步失败不影响其余（各自独立）；所有 backfill 幂等，中断重跑即可。

### 2. 生成 HTML（当前六个 · 候选个股池暂停中不生成）
```bash
PYTHONIOENCODING=utf-8 python scripts/research_report.py        # 冷启动时已由 setup 渲染过,这里重跑无妨(秒级);纯可视化(无 LLM/无告警)
PYTHONIOENCODING=utf-8 python scripts/index_timing_report.py    # 指数择时（10 section，深浅色可切）
PYTHONIOENCODING=utf-8 python scripts/stock_report.py           # 个股诊断（卡片+弹窗时序图+🤖按钮，深浅色可切；生成不调LLM，🤖点击时实时生成）
PYTHONIOENCODING=utf-8 python scripts/macro_framework_report.py --no-open # 宏观框架（总览表+🥇黄金阶段定位器+微观紧缺+因果链分节点图，纯数据无LLM）；--no-open 必须：脚本默认会自动开浏览器，不加会与第3步重复弹两次
PYTHONIOENCODING=utf-8 python scripts/china_macro_report.py --no-open   # 国内宏观（七 section：①货币信用 ②利率与流动性 ③政策日历 ④社融nowcast ⑤会议→M2回放 ⑥通胀 ⑦实体；纯数据无LLM；--no-open 同上）
PYTHONIOENCODING=utf-8 python scripts/position_report.py        # 仓位管理（估值档×预案对照；数据腿=backfill_index.py 已回填的 index_pe/pb，无新回填；预案表在 config/params.yaml position_plan）
# ⏸ 候选个股池 data/stock_pool.html 暂停生成(数据冻结在 08-27,旧 HTML 仍可看)；重启时先 --fix --pool 补数据再跑:
# PYTHONIOENCODING=utf-8 python scripts/stock_pool_report.py    # （六表；首次自动带 stage-2 候选腿周度刷新~5-8min，--no-stage2 跳过）
```
要推送信号提醒：指数/个股看板加 `--push-alerts`（触发时推微信/飞书）；研究看板的 `--push-alerts` 已退役（仅可视化），传了也是 no-op；候选池 v1 无推送（看板 only）。

### 3. 生成并打开总入口壳页（七看板合一个页面 · 左侧导航 iframe · `data/index.html`）
```bash
PYTHONIOENCODING=utf-8 python scripts/dashboard_home.py   # 生成壳页 + 打开（无数据依赖·秒级；只读七个 HTML 的 mtime 标 as_of/新鲜度）
```
壳页内点左侧导航切七看板（iframe 切回不重载，保留滚动/交互状态；记住上次选择；绿点=今日已生成/黄点=过期/灰点=未生成并给生成命令）。仍可单独开某看板：双击 `data/xxx.html`。

### 4. 汇报（给用户）
- 走的是 **COLD 还是 WARM**（让用户知道这次是不是首次大回填）
- 总入口壳页路径（`data/index.html`）+ 七个看板各自一行关键结论：
  - 研究：参与排名 N/总数、顶部「偏离度极端区」（超买/超卖各哪些）+ 剪刀差分化 ETF + 板块资金流向标签 + 📡申赎异动（各窗口事件数、🌊连环潮汐领衔条目、今日/当时口径差异）+ 业绩预期（预告 label 分布、一致预期 g N/36 出数、修正动量冷启动进度 X/4）
  - 指数：估值 zone + 大小盘温差 + ⑦相对周期位置 + ⑧地量状态 + ⑨恐惧贪婪读数(0-100/五档) + 是否有有效突破/跌破信号
  - 个股：观察池触发提醒数 + 周期 tab 商品面板（哪些商品向上/背离/向下）+ 埋伏候选（门槛线≥30 上下）+ 任何异常（避坑/戴维斯/预告拐点/M1-M2 商品背离）
  - 宏观框架：🥇黄金阶段定位器（阶段 + bull_intact 底层 + 置信度 + 操作建议）+ 利率节点（实际利率/期限溢价方向）+ 微观（COMEX库存/CFTC投机是否泡沫·商业是否逼空/央行购金节奏）+ 📰经济日历（近7天美国高重要性数据公布vs预期surprise + 未来FOMC/CPI/非农催化剂时点）
  - 仓位：当前估值档（+进入日/在档天数）+ 该档预案权益%区间 + 最近一次档位切换 + 环境注记（⑨恐贪/⑧地量/⑩关键位）
  - 候选池：⏸ 已暂停(2026-08-29,数据冻结在 08-27)——不汇报、不刷新；用户说重启后恢复（--fix --pool 补数据 + 重渲染 + 恢复本行汇报）
- 本轮数据问题（某 ETF 缺失、某股 PE 稀疏、legulegu 限流需重跑等）

## 何时用统一 skill vs 单看板 skill
- **全刷 + 全开 / 冷启动一键起** → 本 skill
- 只刷/细控**单个**看板 → `research-dashboard` / `tracker-dashboard`
- 只想冷启动 **ETF 研究**一个 → `research-dashboard-setup`（本 skill 冷启动分支已内含它）

## 注意
- 冷启动首次 ~30min（多源历史；本看板 PE 已不用，`--skip-pe` 跳过 cninfo PE 那 ~30min）。
- `setup_research_dashboard.py` 已含依赖/.env 检查，但最好先按 README 跑过 `pip install` + `cp .env.example .env`。
- 所有 backfill 幂等，中断重跑即可；纯只读诊断侧，不改交易引擎数据。
- 宏观框架的黄金微观数据含 **FRED（实际利率/通胀预期）+ NY Fed（期限溢价）** 两个非-akshare 免费源（同 ECB 外汇先例·无 key）；本网若被拦会跳过该组、其余照跑（`backfill_gold_micro.py` 逐组容错）。GOFO 租赁利率/全球 ETF 是真实数据缺口（无免费源），看板相应位置标注待补。
- 个股看板 🤖 AI 评估是**点击时实时生成**（非预计算，生成看板不调 LLM）：要用 🤖 需另起 `python scripts/ai_eval_server.py`（首个长驻服务·127.0.0.1:8765，起一次可反复点；不点 🤖 则不需要它）。
