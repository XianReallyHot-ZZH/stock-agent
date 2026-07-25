# 指数择时层 Handoff — 清理上下文后恢复用

## ⚡ 快速恢复（先读这段，30 秒定位）

**状态（2026-07-25）**：指数择时看板 `data/index_timing.html` 已扩到 **8 节**（新增 ③PE/PB时序图 / ⑦相对周期律 / ⑧成交量地量监测）。全部 commit & push 到 `origin/master`：⑦`5ff9087` / ⑧`78d4f14` / ③图`e01b3de` / docs`14ece78`。全量测试绿。

**4 条命令验证一切在跑**：
```bash
python -m pytest tests/ -q                     # 全绿(数量随新增测试增长)
python scripts/backfill_index.py               # 6宽基(含000001)+沪深300PE/PB+全市场PB+两市成交额(幂等)
python scripts/index_timing_report.py          # → data/index_timing.html(8节,~10MB)
python scripts/validate_volume_bottom.py       # → data/volume_bottom_study.html(⑧地量 event-study 深度报告)
```

**文件地图**：
| 角色 | 文件 |
|---|---|
| 指标(纯函数) | `stockagent/tracker/indicators.py`：⑦`relative_spread_series/cycle_extremes/classify_cycle/relative_momentum/consecutive_run/linear_fit_line`；⑧`turnover_percentile/turnover_dry_events/volume_bottom_stats/turnover_new_low_years` |
| 诊断组装 | `stockagent/tracker/diagnose.py`：`diagnose_relative_cycle` / `diagnose_turnover` / 挂 `diagnose_layer`；`_binom_p_one_sided` |
| 看板渲染 | `stockagent/tracker/dashboard.py`：`_valuation_figure`(③) / `_relative_cycle_figure`(⑦) / `_turnover_figure`(⑧) + 各 `_section_html` + `render_index_timing` 接线 |
| 提醒 | `stockagent/tracker/alerts.py`：E5(⑦周期极点) / V1(⑧地量) —— **仅经 research/stock 推送通路触发**(index_timing_report.py 无 `--push-alerts`) |
| 数据 | `data/store.py`(`market_turnover` 表 + `index_daily` 加 000001) / `fetcher.py::fetch_market_turnover`(baostock) / `manager.py::update_market_turnover` |
| 脚本 | `scripts/backfill_index.py`(`--turnover` flag) / `scripts/validate_volume_bottom.py`(新) |
| 测试 | `tests/test_tracker_indicators.py` / `tests/test_alerts.py` |
| skill | `.claude/skills/tracker-dashboard` / `dashboards`（已同步到 8 节） |

**恢复后第一步**：继续开发 → 挑下面「可选增强」；验证 → 跑上面 4 条命令；懂某块 → 读对应 docstring（中文注释详尽）+ 本文件「关键实证/决策弯路」段。

---

## 🔑 关键实证发现 + 决策弯路（清上下文会丢、必须靠这里重建的部分）

### ⑦ 相对周期律（创业板 vs 上证 点差）
- **spread = 上证综指 − 创业板指（点）**；headline = 当前点差在 **5 年包络 [min, max] 内的线性位置** `(cur−min)/(max−max)`（作者口径，**复现他全部三次调用**：上沿1350/下沿-250/现在中枢）；副指标 = 同窗口**秩分位**（历史稀有度，house 口径）；漂移 OLS ≈ **−40 点/年**（创业板结构性跑赢）。
- **弯路**：原计划 OLS 去趋势 + 秩分位 → 实测给「下沿极点」，与作者「中枢」相左（点差分布偏态，高位停留久把秩分位拉低）→ **改用包络位置**作 headline，秩分位降为副。
- 图通道线 = 上下沿的**线性趋势拟合**（看漂移方向 + 振幅收窄/展宽；非精确边缘，精度让位于趋势）。

### ⑧ 成交量地量监测（两市成交额）
- **地量 = 成交额 / MA250 ≤ 0.6**（regime 自适应，除以 1 年均值消除名义额长期上行）。
- **弯路 1**：原选「3 年新低」实测**只 2 个事件**（名义额长期上行 → 严格新低极罕见）→ 改 MA250 比值（134 事件）。
- **弯路 2**：又发现 **90s 幼年期（50/134 事件，成交额百万级 + 指数从 100 暴升）虚高 60 日胜率到 55%** → 加 `TURNOVER_MATURE_START=2000-01-01` 过滤。
- **诚实结论（成熟市场 sample~84）**：✅ **时效扎实**（量底→价底中位 ~31 交易日、最长 60）；❌ **胜率无 edge**（各 horizon ~50%，**非**评论员说的「高胜率」）。
- **极端子集**（近 >0.5 年最低）：60 日 ~67-75%，但样本 ~15、p≈0.09-0.15 **未显著** → callout 标「暗示非定律、不据此加仓」。lookback 分桶有梯度（42%→75%）但极端桶 N=4 噪声；ratio（干涸比）无梯度。
- 图双色：普通地量(琥珀小) / 极端地量(红大)；悬停显「近X年最低」。

### 数据源死胡同（探针确认，别再踩）
- ❌ `stock_market_activity_legu` = **当日快照**（12 行），非历史序列。
- ❌ sina `stock_zh_index_daily` 只 `volume`（成分股数）**无 amount**；即现有 `index_daily.volume` 不是金额、不是两市。
- ❌ eastmoney `push2his` 在本环境被**代理墙挡**（ProxyError）。
- ✅ **baostock `sh.000001`+`sz.399001` 的 `amount` = 交易所总成交额**（已验证 sz.399001 与 399106 完全相同 → 交易所总数非成分和；2024-10-08 两市=3.45 万亿吻合真实天量），两所求和 = 两市，历史到 1991。

---

## ⚠️ 数据约定 & 已知 wart
- **⑦ 点差符号**：spread=上证−创业板，某日创业板点位 > 上证时为**负**（tile 显负数）——框架（去漂移残差分位）符号无关、照常工作，但与评论员「上证跑赢 350 点」的措辞可能对不上，`.hint` 已说明。
- **⑧ 样本稀疏**（~84 次/20 年）→ 胜率统计 CI 宽，UI 一律标「经验参考非定律」+ 露样本数 + p 值，**绝不写成确定信号**。
- **000001 无 PE/PB**（同创业板/科创50）→ ⑦ 只用其日线算点差。
- **baostock 偶发 login 失败 → 重跑**（幂等 upsert）；首次拉历史（到 1991）稍慢。
- **plotly.js 首加载由 ③ 承载**（HTML 最前的图）→ ⑦/⑧/① 都 `include_plotlyjs=False` 避免重复 ~3MB。
- **图分位口径**：③/⑦/⑧ 图用**全历史**口径；tile 的分位仍用**近 10 年**（diagnose 默认）——两者互补，caption 已写明。

## 📋 可选增强（讨论过、未做，按价值排序）
1. **⑧ 情绪反向指标**：融资余额 / 搜索指数等 → 缺数据源，暂缓。
2. **⑧ 基本面锚**：经济数据突变监测（地量 thesis 的证伪过滤）→ 偏定性，暂缓。
3. **E5/V1 接入 index_timing_report.py `--push-alerts`**：目前只走 research/stock 通路；要指数看板独立推送才加。
4. **⑧ 极端子集样本积累后重测显著性**：现 p≈0.09-0.15 未显著，几年后再看是否→显著。
5. **⑦ 短期风格轮动持续性展开**：现仅 mom(20日) + consecutive_run，可加「主导方向持续多久」(容忍 1 日反向)。
6. **⑧ 复查 2017-2018 地量**：是否被 2015 泡余的 rolling-min 压掉没触发（MA250 比值口径下应已捕获，可验证）。

> 验证范式：`python scripts/validate_volume_bottom.py` 的「按极端度分桶」表 + dashboard ⑧ callout 双向对照，最快确认 ⑧ 逻辑没回归。
