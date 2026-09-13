# tushare 迁移对账 · 1.2-1.6 金十宏观族 (精确族: 容差 0)

- 双侧现拉(金十 vs tushare),重叠期逐格比对;金十被拦的腿标 SKIP 不阻塞其余

- shibor: 重叠 2346 期 · FAIL — 42 格不一致overnight: 21 格不一致(max |Δ|=1.05); m3: 21 格不一致(max |Δ|=1.05)
- lpr: SKIP — 源拉取失败 shibor_lpr failed after 1 retries: 抱歉，您访问接口(shibor_lpr)频率超限(1次/小时)，具体频次详情：https:
- money(M2/M1/M0): 重叠 223 期 · FAIL — 258 格不一致m2_amt: 81 格不一致(max |Δ|=41.98); m2_yoy: 10 格不一致(max |Δ|=0.0048); m1_amt: 87 格不一致(max |Δ|=4.316e+05); m1_yoy: 1 格不一致(max |Δ|=2.6); m0_amt: 79 格不一致(max |Δ|=44.8)
- cpi_yoy: 重叠 223 期 · FAIL — 57 格不一致value: 57 格不一致(max |Δ|=0.05)
- ppi_yoy: 重叠 247 期 · FAIL — 57 格不一致value: 57 格不一致(max |Δ|=0.005)
- tsf_inc: 重叠 136 期 · FAIL — 59 格不一致tsf_inc: 59 格不一致(max |Δ|=9232)