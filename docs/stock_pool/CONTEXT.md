# Candidate Stock Pool (候选个股池) — V8 高业绩池

The sixth read-only dashboard context. **V8 (2026-09-12 rewrite)**: the six strategy tables
(S1 偏离超卖 / S2 猛×深跌 / E4 修正动量 / PEAD / 变脸 / stage-2 戴维斯) were retired with
user approval; the dashboard now has a single organizing principle — the quarterly
high-earnings pool (陈氏季度池, distilled from 重远投资观 Q&A, source archive in
MANUAL_REVIEW.md). It surfaces candidates and falsifiable evidence — never buy/sell
points, never engine feed (ADR-0001 fence).

## Language

**候选池 (Candidate pool)**:
The screening universe of this dashboard — whole-market non-ST (spot-snapshot driven,
code segments 60/68/00/30). Derived, dynamic, data-driven. _Redefinition in V8_: no longer
consensus∩spot — 陈氏 method is pure financial screening and does not depend on analyst
coverage. _Avoid_: mixing with 观察池; "股票池" unqualified (say which pool).

**观察池 (Watchlist)**:
`DataManager.STOCK_WATCHLIST` — 40 hand-curated stocks, the *个股诊断* dashboard's subjects.
Static, deep per-stock diagnosis. The two pools coexist; a stock can be in both.

**高业绩池 (High-earnings pool)**:
The dashboard's primary output — a per-stock **state machine**: each stock is judged on
its own disclosure dates with data available at that moment (point-in-time); passing the
ring floor enters the pool, failing the next disclosure exits it. The dashboard shows the
current slice; each live render archives membership (pool_membership table) for diff and
replay. _Avoid_: calling it a quarterly snapshot batch (rebuild timing would be an
arbitrary parameter; the state machine has none).

**三环地板 (Ring floors)**:
The entry gate, tightening ring by ring — an honest adaptation to data availability, not
a compromise: 预告环 single-leg (forecast profit magnitude ≥ floor; 预告 discloses no
revenue), 快报环 dual-axis (np_yoy + rev_yoy), 正式环 dual-axis with 扣非 (deducted NP
from the sina survivor leg; falls back to 归母 with a 未精筛 flag until pulled).

**PEG 轨 / PB 分位轨 (Two valuation tracks)**:
Non-cyclicals rank by PEG (self-computed PE_ttm / 扣非 growth, gate PEG ≤ 1.0 — "业绩没出
股价已上天" gets screened out); cyclicals rank by own-history PB percentile (≤30%) — at a
cycle trough earnings collapse and PE explodes, so an PE gate would kill cyclicals at
their best buying point. Tracks rank separately, merge by within-track rank percentile,
take Top-N. _No industry quotas inside the pool_ — sector exposure emerges from screening;
position-sizing is the user's domain (the 50/50 reference display was removed by user
decision 2026-09-12).

**红旗 / 黄旗 (Red / yellow flags)**:
Risk-screen layering: red = the signal itself is risk (goodwill/equity > 30%; dual-high
cash+leverage proxy ≥15%/40%) — hard exclusion; yellow = needs human judgment of industry
context (receivables/revenue > 50%; goodwill jump > 30% as M&A proxy; 扭亏; 未精筛) —
enters the pool flagged, for manual review. The original fourth screen (营运资本/长期
负债) has no data source and lives in MANUAL_REVIEW.md as a manual check.

**涌现簇 (Emergent cluster)**:
A ≥50% share of commodity-linked cyclicals (type=cyclic with commodity mapping) inside
the pool — the signal by which 陈老师 discovered the commodity-cycle bet in July (sector
choice emerges from the pool, not from top-down prediction). Display-only.

**环比 diff (Pool diff)**:
New entrants / exits vs the previous archived snapshot — the visualization of "增速不行了
下季度自然被淘汰" (no persistence prediction; the pool stays composed of high earnings).

**未精筛 (Unrefined)**:
The sina survivor leg (扣非 / goodwill) hasn't been pulled for this floor-passer yet —
 归母 proxy in use, flag self-heals after the next leg refresh. Missing data is never
silently treated as safe.

**除权嫌疑 (Ex-div suspect)** — *retired in V8* (was an S1/stage-2 concept).
**分红前复权 (Dividend-adjusted close)**: still the price primitive (raw storage +
dividend-table runtime adjustment); the PB numerator uses the raw price (level-correct).

## Retired terms (V8)

S1 偏离超卖复合分 · 猛分 (fierce score) · 深跌 (deep drawdown) · 窗口A/窗口B · surprise
(PEAD) · stage-2. Their validators' conclusions remain archived in meta
(deviation_extreme_conclusion / pead_conclusion); the strategy code stays on disk,
dormant.
