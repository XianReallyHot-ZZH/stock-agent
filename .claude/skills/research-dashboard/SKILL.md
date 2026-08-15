---
name: research-dashboard
description: Refresh, backfill, and generate the ETF 行业研究·择时跟踪看板 (research_report.html). Use when the user wants to update/refresh the research dashboard, suspects its data is stale or incomplete, wants a current report (e.g. before a Friday review), or asks to "生成/刷新看板". Tracks 净值-MA60 偏离度 + 份额-净值剪刀差 + 板块资金流向(行业级分组份额净申赎); checks data freshness vs the latest trading day and auto-backfills gaps.
---

# ETF 行业研究·择时跟踪看板 — 维护与生成

纯研究模块（只读，不碰交易引擎）。**择时跟踪**定位（已从「性价比评估」转定位）：每只 ETF 跟踪 ① 净值-MA60 偏离度（当前偏离 + 历史百分位分位 + 第几极值）② 份额-净值剪刀差分化 ③ 筹码方向（份额申赎 5/10/20/30/60 日近端等差加权投票·±1% 死区，机构行为代理；表格筹码列含多窗口序列）——①×③ 交叉出「偏离度×筹码四象限提醒」横幅（机会/关注/严重警告/风险提示，持平不入格）④ 板块资金流向（份额视角，`research/flow.py`：**行业级分组 25 组**=etf_pool.yaml `group` 字段——每行业独立、只合并真正相近（跨境与大A分开：恒生医疗/纳指各自独立、港股科技=恒科+中概；通信/传媒分开；有色/煤炭分开；中游材料=钢铁+建材+化工）；增量vs存量分解 tile + 组级净流入时序图（窗口5/20/60日×亿/占当日组规模% 6态按钮·组chips筛选）；组构成三处可见（chips 悬停/明细块/排名表类型副行）；插在四象限横幅与排名 tab 之间，图走 CHARTS['__flow'] 懒渲染；组×月热力图已移除——ROC 方差与组规模成反比，长历史看线图%态+全部范围）。②③④ 的份额均已做**拆分/折算前复权**（`timing.split_adjusted_shares`：份额×unit_nav 反向断崖检测；原始份额跨拆分日会读出 +100% 假"申赎"）。输出 `data/research_report.html`（深浅色可切）：顶部「偏离度极端区」横幅（超卖绿/超买红，可点跳转）+ 💰 板块资金流向 section + 价值/成长/周期 真 tab 分页排名表（记住上次选择·表头点击排序 名称/类型/偏离/成交）+ 逐标的明细折叠面板（默认收起·置顶展开·summary 摘要 chips·顶部下拉快速跳转·右下回顶部；份额净值图 + 偏离度图 + 日度净申赎图（柱:亿元·线:日增减%），懒渲染=滚动停稳后分帧画）。**纯跟踪、不标买卖点、人决策**；LLM 解读与告警推送均已退役（仅可视化）。

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
- PE 行可忽略：本看板已不用 PE（偏离度/剪刀差只需 NAV+份额）

### 2. 若份额/净值落后或缺失 → 自动补齐到最新交易日
```bash
PYTHONIOENCODING=utf-8 python scripts/dashboard_data_check.py --fix
```
补：价格(update_all) → 份额(缺口 SSE+SZSE) → 净值(增量 per-symbol)。补完自动复查。**PE 不必补**（看板不用）。
> 用户明确要"当前报表"时，**默认就跑 --fix**，不必逐项问。

### 3. 生成看板
```bash
PYTHONIOENCODING=utf-8 python scripts/research_report.py   # 生成择时跟踪看板（无 LLM、无告警推送，纯可视化）
```
历史 flag `--push-alerts` / `--no-llm` / `--llm-per-etf` 仍可传（向后兼容）但已是 no-op（告警推送与 LLM 解读已退役）。置顶 ETF 改 `config/params.yaml` 的 `research.pinned_etfs`（默认 创业板159915 / 科创50 588000）。

### 4. 汇报（给用户）
- 参与排名 N/总数（NAV 历史不足算偏离度的被排除，单列）
- 顶部「偏离度极端区」：超卖区(绿)/超买区(红) 各哪些 ETF + 偏离% + 分位 + 第几低/高
- 有剪刀差分化的 ETF（份↑净↓ / 份↓净↑）
- 四象限提醒命中（超卖+筹码增=机会 / 超买+筹码减=风险 / 超卖+筹码减=严重警告 / 超买+筹码增=关注；极端区命中稀少是设计使然，全空省略横幅属正常）
- 板块资金流向标签：[增量普涨/增量聚焦/存量轮动/净赎回/缩量观望] + 近20日全池净流入(亿) + 轮动强度 + 各组 chips（谁在流入/流出——存量轮动时看组间跷跷板）
- 任何本轮新发现的数据问题（如某 ETF 新增缺失）

## 已知坑（数据源限制，看板已优雅处理）
- **份额拆分/折算**（2026-08 修复）：原始份额跨拆分日有 +100% 级跳变（2021-2026 全池 12 起，如 515880×2、159941×4、512200 反向折算）——份额系指标（剪刀差/筹码/资金流向）统一走 `timing.split_adjusted_shares` 前复权（份额×unit_nav 反向断崖检测·阈值 20%/25% 反号；分红无份额跳变不误触发、巨额真实申赎保留）。历史上 fix_splits 曾对份额写过前复权但只覆盖 25 个散点日期造成错位行，`scripts/fix_share_scale.py`（run-once，已跑）重抓修复；日更写原始值，计算时调整才是长效机制。
- **515880 通信**：曾有"无份额历史"问题，现已有完整历史（2021 起），正常参与所有份额指标；若某标的真无历史，资金流向会把它列入 excluded 并在看板注明。
- **NAV 拆分断崖**：偏离度算在 **acc_nav（累计净值，拆分/分红连续）** 上，不是 unit_nav（原始，有断崖会伪造偏离极值）。`fetch_etf_nav` 主接口失败自动回退 `fund_open_fund_info_em`。
- **份额历史深市**：`fund_etf_scale_szse()` 是 spot 无历史，真正历史在 `fund_scale_daily_szse`（按月增量回填，在 backfill_etf_scale source=szse 里）。按月拼接缝可能把数日流量记到单日——滚动求和/月度 ROC 层面望远镜抵消，仅时点涂抹。
- **偏离度需 NAV 历史 ≥ ma_period+20（≈80 日）**：不够的 ETF 不进排名（明细图仍画）。
- **板块资金流向的方法论边界**（读图说明④同款）：ETF份额=净申赎（配置盘脚印）但流入≠看好（A股有越跌越买的逆势申购）；「板块间流向」是推断非观测（资金来源无标签）；36 只是精选池，流出可能去了池外主题 ETF。

## 排名规则提醒
`data_sufficient` = 偏离度可算（NAV 历史 ≥ ma_period+20 根 acc_nav）。不足者保留明细图、不进排名。三类（价值/成长/周期）仅作分页分组；排序统一按偏离极值 `|分位−0.5|`。置顶 ETF（`research.pinned_etfs`）排在逐标的明细最前并标 ⭐。

## 数据回填（一次性历史，非日常）
```bash
# 新环境/重置后补全部历史（本看板只需 nav + scale；pe 已不用但脚本仍支持，可跳过省 ~30min）
python scripts/research_report.py --backfill nav --start 2021-01-01
python scripts/research_report.py --backfill scale --source szse --start 2021-01-01   # 深市份额
python scripts/research_report.py --backfill scale --source sse --start 2021-01-01    # 沪市份额
```

## 端点真相（探针确认，akshare 1.18.64）
| 数据 | 可用端点 | 备注 |
|---|---|---|
| ETF NAV | `fund_etf_fund_info_em(fund,start,end)` → 失败回退 `fund_open_fund_info_em` | 单位+累计净值（偏离度用 acc_nav） |
| SSE 份额 | `fund_etf_scale_sse(date)` | 按日快照（当前 DB 已含全池含 515880） |
| SZSE 份额 | `fund_scale_daily_szse(start,end,symbol="ETF")` | 按区间，按月分块 |
| （已不用）行业PE | `stock_industry_pe_ratio_cninfo` | 本看板已不渲染 PE，回填可跳过 |
