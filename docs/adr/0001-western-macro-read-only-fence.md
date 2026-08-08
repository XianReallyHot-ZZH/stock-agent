# Western-macro module is a hard-fenced read-only 旁路 — never feeds the rotation engine

We are adding a western-macro **prediction ledger + causal framework** module (USD / U.S. Treasury yields / gold / oil / U.S. equities) built on the 96-episode JZ "西方经济" transcript corpus. The core project is A-share ETF rotation under a hard rule — *决策归规则引擎，模型/外部观点不发明数字*. The corpus is the opposite: one human's discretionary macro calls with price targets and scenarios. **Decision:** build it in-tree, mirroring the `tracker/` / `research/` read-only-旁路 pattern (shared data + dashboard + alerts infra), but impose a hard fence — it emits diagnostics for the human only; its outputs never import into or influence `engine/` rotation, with no exception for assets where macro seems relevant (e.g. a gold ETF).

## Considered options
- **Sidecar repo** — rejected: reinvents the data/dashboard/alerts stack for purity. `commodity_price` is already consumed read-only by `tracker/leading.py`, proving the fenced-in-tree pattern works.
- **Unfenced in-tree** — rejected: inevitable discretionary / external-view contamination of the rule engine, plus look-ahead risk.

## Consequences
The human manually synthesizes the western-macro and A-share dashboards; there is no automated macro→A-share crossfeed. Lifting the fence (e.g. letting a gold-macro signal tilt the gold ETF) requires a new ADR that does it deliberately.

## Amendment — forex source (2026-08-08)
The "AkShare-only primary, no external source" preference is **relaxed for forex only**:
`push2his.eastmoney.com` (AkShare's forex host) is blocked on this network, and DXY is the
forecaster's most-cited causal lever, so data availability wins. `fetch_forex_pairs_ecb()`
fetches ECB reference rates (Frankfurter API, free, no key) and falls back whenever AkShare
forex yields fewer than 6 legs. The **fence is unchanged** — it is still read-only diagnostic
data that never feeds `engine/`; only the forex source mix changed.
