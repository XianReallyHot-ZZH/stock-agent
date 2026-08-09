---
name: dashboards
description: One-click refresh + generate + open all four read-only diagnostic dashboards (ETF 行业研究 research_report.html / 指数择时 index_timing.html / 个股诊断 stock_diagnose.html / 宏观框架 macro_framework.html). Auto-detects COLD-START (empty/thin DB → full backfill ~1hr, works on a fresh clone) vs WARM daily incremental refresh. Use when the user wants to update/refresh ALL dashboards at once, do a 盘前/周五全套看板 review, just cloned the repo and wants the dashboards running ("一键起看板 / fresh start"), or asks to "一键看板 / 刷新所有看板 / 把看板都更新一下并打开". Refreshes data, regenerates the 4 HTMLs, opens them in the default browser.
---

# 四看板一键刷新 + 打开（统一入口 · 冷启动感知）

四个**只读诊断**看板的统一入口（都不碰交易引擎），**fresh clone 也能一键起**：

| 看板 | 产物 | 单看板 skill |
|---|---|---|
| ETF 行业研究（三类分类·性价比·9 信号） | `data/research_report.html` | `research-dashboard` |
| 指数择时（估值开关·趋势·相对周期·地量·偏离） | `data/index_timing.html` | `tracker-dashboard` |
| 个股诊断（三类·S07 归因·S10 戴维斯·S08 避坑·时序图·🤖AI评估） | `data/stock_diagnose.html` | （无，本 skill 覆盖） |
| 宏观框架（因果链 利率→曲线→美元→金属→能源→权益 · 🥇黄金阶段定位器 · 微观紧缺） | `data/macro_framework.html` | （无，本 skill 覆盖） |

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
# ETF 行业研究：全套历史(价格+份额+净值+PE)+ 渲染 research_report.html（自含依赖/.env 检查）
PYTHONIOENCODING=utf-8 python scripts/setup_research_dashboard.py
# 快速预览（跳过 ~30min 的 PE 回填，双因子排名照跑）：上行加 --skip-pe
# 指数择时（全量幂等：6 宽基日线含000001 + 沪深300 PE/PB + 全市场 PB + 两市成交额）
PYTHONIOENCODING=utf-8 python scripts/backfill_index.py
# 个股诊断（全量幂等：日线 / baidu PE·PB / sina 财报 / 分红 / eastmoney 预告）
PYTHONIOENCODING=utf-8 python scripts/backfill_stock_data.py
# 商品价（A 类领先信号：碳酸锂/铜/螺纹钢/黄金/原油，futures_zh_daily_sina 日频）
PYTHONIOENCODING=utf-8 python scripts/backfill_stock_data.py --comm
# 宏观框架（全量幂等：UST/美股/外盘期货/外汇+DXY 6腿重算）
PYTHONIOENCODING=utf-8 python scripts/backfill_western_macro.py
# 宏观框架·黄金微观紧缺（COMEX库存/CFTC投机+商业持仓/央行购金/FRED实际利率+通胀预期/NYFed期限溢价）
PYTHONIOENCODING=utf-8 python scripts/backfill_gold_micro.py
```

### 1b. 日常增量（数据已存在 · 几分钟）
```bash
# ETF 行业研究：查新鲜度 → 自动补缺口到最新交易日（价格/份额/净值/PE）
PYTHONIOENCODING=utf-8 python scripts/dashboard_data_check.py --fix
# 指数择时（全量幂等，同冷启动）
PYTHONIOENCODING=utf-8 python scripts/backfill_index.py
# 个股诊断（全量幂等，同冷启动）
PYTHONIOENCODING=utf-8 python scripts/backfill_stock_data.py
# 商品价（同冷启动；周期 tab 商品面板/时序图/alignment/M1-M2 依赖此数据）
PYTHONIOENCODING=utf-8 python scripts/backfill_stock_data.py --comm
# 宏观框架（全量幂等，同冷启动：UST/美股/外盘期货/外汇+DXY）
PYTHONIOENCODING=utf-8 python scripts/backfill_western_macro.py
# 宏观框架·黄金微观紧缺（同冷启动：COMEX/CFTC/央行/FRED实际利率/NYFed期限溢价）
PYTHONIOENCODING=utf-8 python scripts/backfill_gold_micro.py
```
任一步失败不影响其余（各自独立）；所有 backfill 幂等，中断重跑即可。

### 2. 生成四个 HTML
```bash
PYTHONIOENCODING=utf-8 python scripts/research_report.py        # 冷启动时已由 setup 渲染过,这里重跑无妨(秒级);默认 1 次 LLM 全池综合(无 key 走规则模板),--no-llm 最快
PYTHONIOENCODING=utf-8 python scripts/index_timing_report.py    # 指数择时（8 section，深浅色可切）
PYTHONIOENCODING=utf-8 python scripts/stock_report.py           # 个股诊断（卡片+弹窗时序图+🤖按钮，深浅色可切；生成不调LLM，🤖点击时实时生成）
PYTHONIOENCODING=utf-8 python scripts/macro_framework_report.py # 宏观框架（总览表+🥇黄金阶段定位器+微观紧缺+因果链分节点图，纯数据无LLM）
```
要推送信号提醒：各自加 `--push-alerts`（触发时推微信/飞书）。

### 3. 打开四个看板（跨平台，file:// 绝对路径，默认浏览器新标签）
```bash
python -c "import webbrowser,pathlib; [webbrowser.open(pathlib.Path(f'data/{n}').resolve().as_uri()) for n in ['research_report.html','index_timing.html','stock_diagnose.html','macro_framework.html']]"
```
Win/mac/linux 通用。失败就手动双击 `data/*.html`，或 Win 用 `start data/xxx.html`、mac 用 `open data/xxx.html`。

### 4. 汇报（给用户）
- 走的是 **COLD 还是 WARM**（让用户知道这次是不是首次大回填）
- 三个 HTML 路径 + 各自一行关键结论：
  - 研究：参与排名 N/27、性价比 top 3 + 相位
  - 指数：估值 zone + 大小盘温差 + ⑦相对周期位置 + ⑧地量状态 + 是否有有效突破/跌破信号
  - 个股：观察池触发提醒数 + 周期 tab 商品面板（哪些商品向上/背离/向下）+ 埋伏候选（门槛线≥30 上下）+ 任何异常（避坑/戴维斯/预告拐点/M1-M2 商品背离）
  - 宏观框架：🥇黄金阶段定位器（阶段 + bull_intact 底层 + 置信度 + 操作建议）+ 利率节点（实际利率/期限溢价方向）+ 微观（COMEX库存/CFTC投机是否泡沫·商业是否逼空/央行购金节奏）
- 本轮数据问题（某 ETF 缺失、某股 PE 稀疏、legulegu 限流需重跑等）

## 何时用统一 skill vs 单看板 skill
- **全刷 + 全开 / 冷启动一键起** → 本 skill
- 只刷/细控**单个**看板 → `research-dashboard` / `tracker-dashboard`
- 只想冷启动 **ETF 研究**一个 → `research-dashboard-setup`（本 skill 冷启动分支已内含它）

## 注意
- 冷启动首次 ~1hr 不可避免（PE 回填 cninfo 限流 + 多源历史）；`--skip-pe` 可快速预览。
- `setup_research_dashboard.py` 已含依赖/.env 检查，但最好先按 README 跑过 `pip install` + `cp .env.example .env`。
- 所有 backfill 幂等，中断重跑即可；纯只读诊断侧，不改交易引擎数据。
- 宏观框架的黄金微观数据含 **FRED（实际利率/通胀预期）+ NY Fed（期限溢价）** 两个非-akshare 免费源（同 ECB 外汇先例·无 key）；本网若被拦会跳过该组、其余照跑（`backfill_gold_micro.py` 逐组容错）。GOFO 租赁利率/全球 ETF 是真实数据缺口（无免费源），看板相应位置标注待补。
- 个股看板 🤖 AI 评估是**点击时实时生成**（非预计算，生成看板不调 LLM）：要用 🤖 需另起 `python scripts/ai_eval_server.py`（首个长驻服务·127.0.0.1:8765，起一次可反复点；不点 🤖 则不需要它）。
