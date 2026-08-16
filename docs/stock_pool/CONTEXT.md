# Candidate Stock Pool (候选个股池)

The sixth read-only dashboard context: a **screening funnel** over the analyst-covered universe
(~2300 stocks), turning whole-market data legs (consensus snapshots, 业绩预告/快报/正式报 panels,
daily prices, industry boards) into candidate lists per strategy. It surfaces candidates and
falsifiable evidence — never buy/sell points, never engine feed (ADR-0001 fence).

## Language

**候选池 (Candidate pool)**:
The screening universe AND output of this dashboard — derived, dynamic (~2300), data-driven
(consensus coverage ∩ non-ST spot). Enters and leaves as data changes; nobody curates it by hand.
_Avoid_: mixing with 观察池; "股票池" unqualified (say which pool).

**观察池 (Watchlist)**:
`DataManager.STOCK_WATCHLIST` — 40 hand-curated stocks, the *个股诊断* dashboard's subjects.
Static, deep per-stock diagnosis. The two pools coexist; a stock can be in both.

**偏离超卖 (S1)**:
Strategy-1 trigger: a stock's own MA60-deviation percentile ≤5% **and** price actually below
MA60 — per-stock history as the ruler, never cross-sectional. Composite score = 0.6×depth +
0.4×stabilization; guardrailed. _Avoid_: calling the raw percentile "排名" (cross-section is
display-only).

**护栏 (Guardrails)**:
S1's three gates: no 预亏族 forecast type (首亏/续亏/增亏), consensus 4-week revision not in
big downgrade, face-change direction ≠ down. Missing data passes with a 缺 flag — unknown ≠
known risk.

**猛分 (Fierce score)**:
Strategy-2's earnings-expectation strength, per type. Cyclic = commodity health × price lag
(alignment) with consensus-g confirmation; growth = mean of up to three legs (forward-g rank /
revision / reported acceleration), ≥2 legs valid. Value stocks have no fierce score in v1 —
earnings elasticity is not their axis.

**深跌 (Deep drawdown)**:
250-day drawdown ≥40% (same primitive as P1). S2's price leg; only meaningful crossed with
fierce — 下跌≠便宜.

**窗口A (Window A)**:
预告/快报 landed → 正式报 deadline. Information revealed; drift-capture window. All types.

**窗口B (Window B)**:
上期正式报截止+grace → 下期预告开窗. Mid-quarter pre-estimation ambush; **cyclic only**
(commodity prices are observable — "能分析出反转才提前下注"). Q1 has no Window B by
construction; that is calendar honesty, not a bug.

**变脸 (Face change)**:
Reported-earnings regime break, same-tail comparison: 跳档 (gear-down, 小米型), 趋势破位
(trend break, 腾讯型), 连亏 (consecutive negatives, 美团型), 拐头向上 (turn-up). Feeds S1
guardrails (down) and is a standalone monitor table (both directions).

**surprise (PEAD)**:
Forecast yoy minus the point-in-time implied expectation — leg C (consensus-implied, annual
periods) or leg A (prior-year actual). Whether post-announcement drift exists is *decided by
the validator*, not assumed.

**除权嫌疑 (Ex-div suspect)**:
A 0.8–8% single-day drop with no dividend event nearby. Flagged ⚠️, excluded from stage-2;
the dividend table self-heals it weekly. Not a verdict.

**分红前复权 (Dividend-adjusted close)**:
Runtime adjustment from the dividend table (exchange formula), applied before every S1/S2
computation. Raw storage + deterministic events — never statistical cliff-guessing.
