# 西方宏观预测台账 — Handoff (Phase 3 · 只读旁路 · ADR-0001)

## ⚡ 快速恢复
- 看效果：`python scripts/western_macro_report.py`（结算+渲染 `data/western_macro.html` 并打开）
- 抽新集：`python scripts/extract_western_claims.py`（幂等，跳过已抽）
- 补数据：`python scripts/backfill_western_macro.py`（UST/美股/外盘期货/外汇 + 6腿重算 DXY）
- 设计依据：`docs/adr/0001-western-macro-read-only-fence.md`（围栏）+ `docs/western_macro/CONTEXT.md`（领域语言）

## 这是什么
把 JZ《西方经济》96 集 B 站转写稿（`docs/Billibili-JZ/western_economy/`）变成**可追踪的预测业绩记录**：
LLM 抽**可证伪 claim**（方向/区间/点位/时点/情景）→ 到期后对**朴素基准**打分 → `edge = hit AND NOT baseline_hit`
（只有赢过"该资产自身漂移"的预测才算本事）。**只读诊断，永不喂 A 股轮动引擎。**

## 代码地图
- `stockagent/data/store.py`：`western_macro_series` + `wm_claims`/`wm_settlements`/`wm_rules` 表 + 方法
- `stockagent/data/fetcher.py`：`fetch_us_treasury`/`fetch_us_index`/`fetch_foreign_future`/`fetch_forex_pair` + `reconstruct_dxy(_series)`（ICE 公式 6 腿重算）
- `stockagent/data/manager.py`：`update_western_macro()`
- `stockagent/western_macro/`：`drivers.py`(canonical 驱动图) · `extract.py`(LLM 抽 DRAFT) · `score.py`(评分/自动结算) · `dashboard.py`(HTML)
- `scripts/`：`backfill_western_macro.py` · `extract_western_claims.py` · `western_macro_report.py`
- 测试：`tests/test_western_macro.py`（DXY 公式+store）· `tests/test_western_score.py`（edge/baseline 逻辑）

## Slice 1 现状（2026-08-08）
- 数据层：~70K 行已入库（UST 26年/美股22年/外盘期货10-30年）。`US 2s10s=+0.46`（陡峭化，JZ 核心论点数据在手）。
- 已抽 7 期（3 个 Aug + 2026-02-02 + 2025-10-30 + 2025-03-13 + 重试 08-05），共 20 claim + 若干 rule。
- 评分跑通：4 条结算 = **2 EDGE★**（美债2Y 两次逆势命中）+ 2 hit-no-edge（A股/标普 2025 顺势）→ 完美演示 edge 语义。

## 已知坑 / 待办
1. **DXY/外汇 ✅ 已解(2026-08-08)**:`push2his`(AkShare 外汇)被网拦 → `fetch_forex_pairs_ecb()`(ECB/Frankfurter,免费·无 key·1999 起)自动 fallback,一次取 EUR→6 币换算成 USD-base 对 → 重算 DXY。实测 latest 99.86、2024-01-02≈102.1(与真实 ICE 吻合)。美元 claim 现在数据齐,horizon 到了即结算。ADR-0001 已加 Amendment(只放宽外汇源,围栏不变)。
2. **LLM 系统性把"近期未来"误标成过去年份**（2026-08 期说"9月"→2025-09-30）。已加①prompt 锚定发布日②`_build_claim` 年份自纠③`settle_claim` inverted-horizon 守卫。仍以**人工确认 horizon** 为准。
3. **confirm 闸门 UI 未建**：`store.set_wm_claim_state(uid,'confirmed'/'vetoed')` 已有，但无 CLI/看板按钮。当前 `settle_claims` 连 DRAFT 一起结算（=Q9 的"历史批量自动结算+抽样审计"模式）；可执行层（近期/open）需先 confirm。
4. **语料回填中**：96 集未全转写，track record 为部分样本（看板已标"非定论"）。
5. **深回填（到 2024）**：设计已定（分期、历史批量自动结算+抽样审计），待语料补全后跑全量。

## 下一步（按优先级）
- 用户本机补 DXY → 美元/黄金 claim 可自动结算
- 建 confirm 闸门（看板按钮 / CLI）→ 可执行层先 confirm 再计分
- 全量抽 96 集（语料补全后）
- （可选）看板加 Plotly 时序图、微信结算提醒（Q10 暂缓）
