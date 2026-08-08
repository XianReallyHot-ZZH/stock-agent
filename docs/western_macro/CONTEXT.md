# Western-Macro Prediction Ledger

The read-only diagnostic context that turns the JZ "西方经济" transcript corpus (96 episodes) into a **trackable forecasting record**. It extracts falsifiable claims, scores them against naive baselines, and grows as new episodes arrive — to *measure* one discretionary macro forecaster, never to auto-execute.

## Language

**Episode**:
One transcribed video in the corpus, keyed by publish date. The unit a batch of Claims is extracted from.
_Avoid_: video, 期 (use Episode in the model; "期" only in user-facing copy).

**Claim**:
One falsifiable assertion extracted from one Episode — with a stated asset, type, horizon, confidence, and a basis in the causal framework. Vague stances ("黄金长期看涨") and non-falsifiable commentary are not Claims.

**Settlement**:
The recorded outcome of a Claim at its horizon: actual direction, hit, baseline hit, edge.

**edge**:
A Claim's ability score = `hit AND NOT baseline_hit`. The only measure of forecasting skill; a raw `hit` rate flatters forecasters in trending markets. Every scored Claim must have a baseline.

**baseline**:
The naive benchmark a Claim is judged against — by default the asset's own drift over the horizon ("always-long gold"). The thing edge is measured *against*.

**horizon**:
The date or trigger by which a Claim must resolve; required to score. Inferred from the Episode when not explicit; if un-inferrable, the Claim is unscoreable.

**primary scenario**:
For a multi-branch (A/B) Claim, the branch the forecaster named most likely. Only the primary scenario's outcome counts toward edge; an alternate branch hitting does not — hedging is not forecasting.

**Rule**:
An operational / risk directive ("禁定投", "禁做空", "趁回调加仓") — not a prediction. Lives in a separate `rules` ledger; never scored as a Claim, but tracked for "did adherence avoid loss."

**Range claim**:
A Claim that the asset stays inside a band ("原油 70–90 震荡"). Scored only against a defined tolerance band; otherwise demoted to direction-neutral (unscored).

**Framework (causal map)**:
The forecaster's recurring driver-chain — `债务压力 → 化债工具(FIMA/eSLR/稳定币/YCC/QE) → 实际利率·期限溢价 → 利率曲线(2s10s) → 美元 → 黄金/有色`. A small canonical map; each Claim's `basis` references one or more of its nodes.
_Avoid_: 思维导图, 逻辑图.
