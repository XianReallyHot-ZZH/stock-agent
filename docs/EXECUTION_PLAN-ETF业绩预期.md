# ETF 业绩预期层 执行计划 (EXECUTION PLAN E0-E4)

| 项 | 值 |
|---|---|
| 状态 | **v1.2 · E0 ✅ + E1 ✅ + E2 ✅ 已落地(2026-08-16)，E3-E4 待实施（E4 激活门≈2026-09-13）** |
| 日期 | 2026-08-16（v1.2 E2 落地） |
| 上游 | `docs/RESEARCH-ETF行业业绩预期.md`（调研报告：方法论/数据实测/36池矩阵/方案比选——本文的任务卡全部来自其 §5/§6/§7） |
| 目的 | 把调研报告的「做什么」翻译成「怎么做、按什么顺序、每步验收什么」，指导 AI 落地；**不重复调研内容，只引用** |
| 硬约束 | 三信号全 INFORMATIONAL 永不喂引擎 · 温度计非开关 · 每任务 pytest · 不往 `research/__init__.py` 加 re-export（DataManager 已 import `research.earnings`，动它会连锁崩三看板） |

> 所有端点结论以 `scripts/probe_earnings_sources.py` 的 VERDICT/DEAD ENDS 为准（可随时复跑）；任务卡中的接口均为 2026-08-16 实测活口。

---

## §0 依赖端点速览（详细列结构见调研报告 §4.1）

| 用途 | 接口 | 状态 |
|---|---|---|
| 一致预期整表（E0/E2/E4） | `stock_profit_forecast_em(symbol='')` ~2800行/1.4s | ✅（按股查询死，只能整表） |
| 指数成分+官方权重（E1） | `index_stock_cons_weight_csindex(symbol)` 月度快照 | ✅ |
| 业绩快报（E3） | `stock_yjkb_em(date)` | ✅ |
| 业绩报表正式值（E3） | `stock_yjbb_em(date)` | ✅ |
| 业绩预告（E3，已入库） | `stock_yjyg_em(date)` → `stock_forecast` 表 | ✅ |
| ~~重仓股 top-10~~ | `fund_portfolio_hold_em` | ❌ 死（E1 只留 fallback 位） |
| 港股逐券商预测（E5） | `stock_hk_profit_forecast_et` | ✅ |

---

## §1 总体 WBS + 依赖 + 运维节奏

```
E0 快照最小版 ✅(2026-08-16 首落 2329 只) ──时间敏感:修正动量冷启动4周,从E0起算
E1 成分底座+修断点 ──► E2 一致预期聚合+看板 ──► E4 修正动量+A5告警(激活门:快照数≥4)
                    └─► E3 三环时效链(独立,可后置)
E5 港股 mini-consensus(可选探索)
```

**运维节奏（全部并入现有看板流程，不新增常驻进程）**：

| 节奏 | 动作 | 入口 |
|---|---|---|
| 日 | 价格/份额/净值新鲜度（现状不变） | `scripts/dashboard_data_check.py --fix` |
| **周** | 一致预期整表快照 | `python scripts/research_report.py --backfill consensus`（E2 后并入 `--fix` 自动化） |
| 月 | 指数成分+权重刷新 | E1 落地后 `python scripts/backfill_constituents.py`（并入 data_check 黄点提示） |
| 季(披露季高频) | 预告/快报/正式报链刷新 | E3 落地后并入 `--backfill earnings` |

---

## §2 E0 · 一致预期快照最小版 ✅ 已落地（2026-08-16）

| 件 | 位置 |
|---|---|
| 纯解析（财年滚动对齐/列映射/缺列防护） | `fetcher.parse_consensus_table(df, now)` |
| 整表抓取（thin-guard <1000 行抛错，绝不静默写零） | `fetcher.fetch_consensus_snapshot(min_rows=1000)` |
| 表 `stock_consensus(code, fetch_date, n_reports, rating_×5, eps_fy1/2, fy1/2_year, source)` PK(code,fetch_date) | `store.SCHEMA` + `upsert_consensus / get_consensus_snapshot(asof) → (date, df) / consensus_snapshot_dates()` |
| 管线（fetch 失败→0 不写库不写 meta） | `manager.update_consensus()` |
| CLI 接线 | `python scripts/research_report.py --backfill consensus`（`all` 亦含） |
| 测试 12 例 | `tests/test_consensus.py` |
| **首次快照** | 20260816：2329 只（可用口径 1588）——**E4 激活日 ≈ 2026-09-13** |

验收命令：`python -m pytest tests/test_consensus.py -q`；`python scripts/research_report.py --backfill consensus`

---

## §3 E1 · 成分股底座 + 修断点 ✅ 已落地（2026-08-16）

> 结果：**30/30 指数成分落库**（含 159326→931994 电网设备，代码来自华夏官网申赎清单页、经 API 名称哨兵实证）；`etf_earnings` 重算 30 只、29 只 coverage>0（银行/300医药 cov=0 是真实状态——中报预告期这两行业极少发预告，score gate 正确显示「数据不足」）；看板业绩列从全「数据不足」变为 26 业绩高增/12 业绩恶化/2 业绩承压。E1.5 之外全按任务卡落地；**159326 已解，剩 512480(CES)/159915(国证) 两处待人工**。

| # | 任务 | 输入 | 输出 | 验收 | 依赖 |
|---|---|---|---|---|---|
| E1.1 | `config/etf_pool.yaml` 每成员加 `index_code:` + `index_expect:`（名称哨兵关键词） | 调研报告 §5 矩阵（29 只有码 + 3 只 null 待人工 + QDII null） | yaml 字段 | `cfg.rotation_symbols()` 不受影响；人工抽查 3 只 vs F10 | — |
| E1.2 | fetcher `fetch_index_constituents(index_code, timeout=40)` | P6 接口 | DataFrame[code(zfill6), name, weight, snapshot_date]，含 `index_name` 列；空→empty df | 探针复跑对照；`tests/test_constituents.py` 解析/填充用例 | — |
| E1.3 | store：表 `index_constituents(index_code, code, name, weight, snapshot_date, PK(index_code,code))` + `upsert_constituents(index_code, df)` + `get_constituents(index_code) → df[code,weight]` + `last_constituent_snapshot(index_code)` | — | 方法 | 幂等重跑；roundtrip 测试 | E1.2 |
| E1.4 | manager `update_constituents(symbols=None)`：遍历池、跳过 null index_code、0.3s 限速；**空结果/哨兵不符 → warn+跳过不写库** | E1.1-3 | 计数 | 日志可审；坏 index_code 不污染表 | E1.1-3 |
| E1.5 | **修断点**：`update_etf_earnings` 持仓改读 `get_constituents`（B 路线），A 路线 top-10 留 fallback；**空持仓→跳过该 ETF 不写零行**（现状 bug） | — | 改造 | 重跑后 `etf_earnings` coverage>0；`tests/test_earnings.py` 补空防护用例 | E1.3 |
| E1.6 | `scripts/backfill_constituents.py`（argparse `--today/--symbols`，仿 `backfill_scale.py`） | — | 脚本 | 重跑无重复；输出覆盖打印 | E1.4 |
| E1.7 | `setup_research_dashboard.py` 加幂等 stage；`dashboard_data_check.py` 加成分新鲜度列（snapshot_date 距今>45天→黄）+ `--fix` 步骤 | — | 接线 | 冷启动端到端跑通；data_check 列渲染 | E1.6 |

**阶段验收**：A 股行业 ETF 全部取得带权重成分；`python scripts/research_report.py --backfill earnings` 后看板业绩列脱离「数据不足」；全量 pytest 绿。
**待人工三项**（不阻塞 E1 其他任务）：512480（CES半导体无免费成分源→走东财行业成分人工导或保持 null）、159326（中证官网查「电网设备主题」代码）、159915（创业板指 399006 国证系→人工导成分或保持 null）。

---

## §4 E2 · 一致预期聚合 + 看板呈现 ✅ 已落地（2026-08-16）

> 结果：**29/36 只出数**（30 只有码 − 房地产 38.4% 被 40% 覆盖门诚实拦下）；标签分布 15 预期平稳 + 14 预期改善，预期 g +5%~+70%；排名表业绩列双 chip（上=预告广度、下=预期 g+覆盖+买入评级占比+财年·快照日）+ 表头新增「预期g」排序键（data-earn）+ 读图说明⑥（口径注：水平值非变化量·系统性乐观需横向比较）；明细 summary chips 加预期 g chip；控制台汇总加「📊 一致预期 N/36」。E2.6 的 data_check 自动化已在 E1 提前落地，本轮补 skill 维护注与 CLAUDE.md 维度描述。测试 `tests/test_research_earnings_consensus.py` 10 例。

| # | 任务 | 输入 | 输出 | 验收 | 依赖 |
|---|---|---|---|---|---|
| E2.1 | `research/earnings.py` 纯函数 `aggregate_consensus(constituents, snapshot, min_reports=3) -> dict`：g=Σwᵢ·(eps_fy2ᵢ/eps_fy1ᵢ−1) + median_g + coverage(达标权重/总权重) + n_names/n_all + buy_ratio | index_constituents + 最新快照 | signal dict | 复用 gates 哲学（coverage+中位数抗极值）；`tests/test_research_earnings_consensus.py`（权重构造→g/coverage/门） | E1 |
| E2.2 | 同文件 `consensus_score(signal, params) -> (score, label)`：clamp+label-band（套 `earnings_score` 模式） | E2.1 | 打分 | 边界用例 | E2.1 |
| E2.3 | `config/params.yaml` → `research.earnings.consensus.{min_reports:3, min_weight_cov:0.40}`（`.get()` 默认，旧文件不崩） | — | 配置 | 改错键不崩 | — |
| E2.4 | `scripts/research_report.py::build_snapshots`：`store.get_consensus_snapshot()` 一次取快照 + 逐 ETF `get_constituents` → snap[`consensus_g/cov/n/label`]；无快照/覆盖不足 → 键缺省（优雅降级） | — | 接线 | 冷库不崩；A 级 25 只出数 | E2.1-3 |
| E2.5 | `research/report.py`：`_earnings_cell` 扩双 chip「预告 label｜预期 g%」+ 表头排序键 + `读图说明` 口径注（东财研报摘录·研报数≥3·覆盖权重门·水平值非变化量） | — | 渲染 | 截图人工核；数据不足态省略 | E2.4 |
| E2.6 | `dashboard_data_check.py --fix`：consensus 快照距今>9 天→自动 `update_consensus`；`.claude/skills/research-dashboard/SKILL.md` 日常维护加周度快照一句 + CLAUDE.md 行业研究块补一行 | — | 运维 | 过期场景自动补 | E0 |

**阶段验收**：看板排名表 A 级 ETF 显示双 chip；§5 矩阵数字与看板抽查一致（如银行覆盖≈95%）；全量 pytest 绿。

---

## §5 E3 · 预告→快报→正式报三环时效链（独立于 E2，可并行/后置）

| # | 任务 | 要点 | 验收 |
|---|---|---|---|
| E3.1 | fetcher `fetch_stock_express(report_period)`（wrap P10，全市场按期）+ store 表 `stock_express(symbol, report_period, announce_date, np_yoy, rev_yoy, PK×2)` | 列映射公告日期（P10 16 列内核对） | 解析测试 |
| E3.2 | fetcher `fetch_stock_report_actual(report_period)`（wrap P11）+ 表 `stock_report_actual`（同构） | **注意**：P11 无显式公告日列——实现时核对全 16 列，缺则以「预约披露日」或报告期末+N 近似并在 docstring 声明粗糙度 | 同上 |
| E3.3 | manager `update_stock_express/report_actual(period)`：trailing 期回填（仿 `update_stock_forecasts` 8 期模式）+ 空防护 | — | 幂等重跑 |
| E3.4 | `research/earnings.py` 纯函数 `earnings_chain(forecast, express, actual, constituents, asof) -> dict`：每成分状态机 无披露→预告→快报→正式报 + 距今天数；ETF 层三环覆盖权重 + 预告 breadth（复用 BULL/BEAR）+ 快报落点 vs 预告区间（保守/中性/激进） | **窗口日历复用 `latest_report_period`** | 状态机边界用例（未出/更正/延迟） |
| E3.5 | `report.py` 逐 ETF 明细「业绩预期链」折叠块（四层阶梯状态条：每环覆盖权重%+披露日+时效灰度） | 复用懒渲染 | 截图人工核 |

---

## §6 E4 · 修正动量 + A5 告警（激活门：`len(consensus_snapshot_dates())≥4`，按 E0 推算 ≈2026-09-13）

| # | 任务 | 要点 | 验收 |
|---|---|---|---|
| E4.1 | `research/earnings.py` 纯函数 `revision_momentum(snap_now, snap_then, constituents) -> dict`：同财年标签对齐（**fy1_year 不匹配→None 防年末翻滚假信号**）加权 Δeps_fy1% + 上/下修家数广度 | 纯函数 | 翻滚边界用例 |
| E4.2 | 接线：`consensus_snapshot_dates()` 挑 ~4 周前快照；快照数<4 → 看板「累积中(N/4)」诚实降级 | — | 冷启动态渲染 |
| E4.3 | `tracker/alerts.py::evaluate` 新增 ETF 层 warn 级 **A5「一致预期下修」**（4周加权 forward EPS 下修>阈值 且覆盖达标）；params `research.earnings.revision.{lookback_weeks:4, min_snapshots:4, alert_drop_pct:3.0}` | **只提醒不交易**；与 A1/A2 并列 | 告警规则用例 |
| E4.4 | 可选：forward-PE 粗近似（P15 指数静态 PE ÷ (1+g)，粗糙度注明） | 末位可选 | — |

---

## §7 E5 · 港股 mini-consensus（可选探索）

`stock_hk_profit_forecast_et` 逐券商明细聚合 → 恒生医疗 513060/恒科 513180（成分股一次性人工列出，~30-60 只/只）→ mini-consensus。排在 E1-E4 后；纳指/中概互联维持放弃（调研 §3.6）。

---

## §8 铁律（每阶段复查）

1. **pytest 每个新函数**（CLAUDE.md 硬约束）——纯函数用例 + store roundtrip + manager 空防护三类齐全才算完。
2. **空结果绝不写库**：fetch 空/薄/哨兵不符 → warn + 跳过（etf_earnings 2026-08 全零教训的代码化）。
3. **阈值进 params.yaml**，`.get()` 默认值兜底旧配置。
4. **research/ 纯函数无 I/O**；份额系指标走 `timing.split_adjusted_shares`（本层不涉份额，仅提示惯例）。
5. **不往 `research/__init__.py` 加 re-export**；改删 research 子模块前先查 `data/manager.py` 的 import。
6. **永不喂引擎**：三信号不进 composite/rotation/alerts 只 warn；A5 文案含「温度计非开关」口径。
7. 端点 verdict 变化 → 先更新 `scripts/probe_earnings_sources.py` docstring，再改本计划与调研报告 §4。
