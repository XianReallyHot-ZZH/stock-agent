# tracker 执行计划 (EXECUTION PLAN)

| 项 | 值 |
|---|---|
| 状态 | **v0.1,待认可后进入 Phase 1-B 实施** |
| 日期 | 2026-07-19 |
| 上游 | `docs/PRD-A股综合跟踪工具.md`(产品定义) |
| 目的 | 把 PRD 的「做什么」翻译成「怎么做、按什么顺序、每步验收什么」,指导 AI 落地 |

> 所有数据层结论来自一次真实接口尽调(akshare 1.18.64,2026-07-19),非猜测。详见 §0。

---

## §0 技术尽调 Ground Truth(数据层事实依据)

| # | 数据 | akshare 接口 | 结论 | 影响阶段 |
|---|---|---|---|---|
| 1 | 宽基日线 | `stock_zh_index_daily(symbol="sh000300")` | ✅ 5 宽基全得(sina,sh/sz 前缀;科创50→2020、创业板→2010 起,其余到 2002-2005)。OHLCV 无 amount | **1-B 骨架** |
| 2 | 沪深300 PE 历史 | `stock_index_pe_lg(symbol="沪深300")` | ✅ 沪深300/上证50/中证500,回溯 2005,`滚动市盈率`;❌ 创业板指/科创50 不支持 | **1-B ④** |
| 3 | 宽基 PB | `stock_index_pb_lg(symbol="沪深300")` | ✅ 宽基;❌ **行业指数(中证煤炭等)不支持** → 周期型降级 PE 反向 | **1-A 周期** |
| 4 | ETF 分红 | `fund_etf_dividend_sina(symbol="sh510300")` | ⚠️ 机械可用但覆盖差(3/1),累计非单次 → 容忍稀疏 | **1-A 价值** |
| 5 | 个股估值 | `stock_zh_valuation_baidu(symbol="600519", indicator="市盈率(TTM)", period="全部")` | ✅ PE(TTM)/PE(静)/PB/市现率/总市值,全市场 IPO 起;❌ 无股息率 | **2 金矿** |
| 6 | 个股日线 | `stock_zh_a_daily(symbol="sh600519", adjust="hfq")` | ✅ 四市场;需修 `_szsh_prefix`(`6xx/68x/9xx→sh`) | **2 骨架** |
| bonus | 全市场 PB | `stock_a_all_pb()` | ✅ 2005 起,带历史分位 → 大盘估值 regime 信号 | **1-B ④** |

**前缀规则(关键)**:指数/个股 sina 调用,前缀 = `000xxx/5xxx/6xx/68x/9xx → sh`;`0xxx(非000)/3xx/399xxx → sz`。

**网络注意**:尽调环境 Clash 代理(127.0.0.1:7890)down,eastmoney 端点不可达;sina/baostock/baidu/cninfo/lg 直连可用。项目多源容错(eastmoney→sina→baostock)sina 能扛。

---

## §1 总体 WBS + 依赖

```
Phase 1-B(指数择时层)── 数据就绪,零回归,先做
  B0 数据层 ──► B1 指标 ──► B2 可视化 ──► B3 tracker骨架 ──► B4 测试 ──► B5 看板渲染
Phase 1-A(ETF 三类分类重构)── 待 B 立骨架后,数据有坑需降级
  A0 数据层(降级) ──► A1 分类器 ──► A2 三类指标 ──► A3 重构 scoring ──► A4 提醒九条 ──► A5 看板
Phase 2(个股层)── 触发:1-B/1-A 跑通 + 个股数据栈就绪
  C0 个股数据栈 ──► C1 个股诊断(三类自动判定+利润归因+避坑) ──► C2 个股提醒
```

**铁律**:每个新功能必须有 pytest(CLAUDE.md 硬约束);回测/实盘共用同一引擎函数;纯函数优先、配置驱动。

---

## §2 Phase 1-B · 指数择时层(详细,即将执行)

> 目标:交付一个交互式 HTML 看板,含 §6 五件套(偏离极值曲线 + 趋势状态 + 蓝筹vs成长 + 估值开关 + 突破跌破)。零回归(不动 `research/`)。

### B0 · 数据层扩展(`stockagent/data/`)

| 任务 | 输入 | 输出 | 验收 | 依赖 |
|---|---|---|---|---|
| **B0.1** `fetch_index_daily` | 宽基代码(000016/000300/000905/399006/000688) | `fetcher.py` 新增函数,调 `stock_zh_index_daily(sh/sz 前缀)`,返回 OHLCV DataFrame | 5 宽基全得;科创50≥2020、创业板≥2010 起点标注;`_run_with_timeout` 包装 | — |
| **B0.2** `fetch_index_pe` | 中文名(沪深300/上证50/中证500) | 调 `stock_index_pe_lg`,取 `滚动市盈率` 系列 | 3 指数 PE 到最新;创业板/科创50 显式跳过(标注不支持) | — |
| **B0.3** `fetch_market_pb` | — | 调 `stock_a_all_pb()`,取全市场 PB + 历史分位列 | 全市场 PB 到最新 | — |
| **B0.4** store 扩展 | — | `store.py` 新表/函数:`upsert_index_daily`(或复用 `daily_prices` TEXT symbol)、`index_pe` 表、`market_pb` 表;配套 `last_*_date` / `get_*_series`(模板抄 `etf_nav`) | 幂等 upsert(ON CONFLICT);增量游标 MAX(date) | B0.1-3 |
| **B0.5** 回填脚本 | — | `scripts/backfill_index.py`(幂等,仿 `backfill_scale.py`);`dashboard_data_check.py` 扩展指数新鲜度 | 重跑无重复;新鲜度检查覆盖指数数据 | B0.4 |

### B1 · 指标计算(纯函数,`stockagent/tracker/` 或扩展 `engine/indicators.py`)

| 任务 | 算法 | 验收 | 依赖 |
|---|---|---|---|
| **B1.1** ma60 + 趋势状态 | `ind.sma(close,60)`;均线趋势=今vs昨;价格 vs 均线(上/下) | 纯函数+pytest | B0 |
| **B1.2** 偏离度 | `close/ma60 − 1` 历史序列 | pytest | B0 |
| **B1.3** 历史极值 | 偏离度序列 max/min(分正负);当前偏离相对极值位置 | pytest:复现 S13 案例(2020-07-13 创业板顶偏离极值) | B1.2 |
| **B1.4** 有效突破/跌破 | S13 六档梯度:收盘突破/跌破、±2%/3%、均线趋势、连续 N 日 | pytest:各档位正确触发 | B1.1 |
| **B1.5** 震荡市识别 | 均线斜率 + 价格横穿 60 线次数 → 趋势/震荡 | pytest:震荡市关闭趋势信号 | B1.1 |
| **B1.6** 蓝筹 vs 成长 | 上证50 vs 创业板/中证500 趋势对比 → 都上=偏成长/都下=偏蓝筹/相反=偏向上 | pytest | B1.1 |

### B2 · 五件套可视化(plotly,`tracker/dashboard.py`)

| 组件 | 内容 | 验收 |
|---|---|---|
| **B2.1** 偏离极值曲线 | 主图(价格+ma60)+ 副图(偏离度,±极值水平线,当前点高亮) | 双击 HTML 可见;plotly 缩放/悬停 |
| **B2.2** 趋势状态面板 | 5 宽基:均线趋势/上下/趋势vs震荡 一览 | 5 指数齐全 |
| **B2.3** 蓝筹 vs 成长对比 | 上证50 vs 创业板/中证500 + 仓位倾向标注 | 对比清晰 |
| **B2.4** 估值开关 | 沪深300 PE 分位 + 全市场 PB 分位,标注高/低位 + 敏感度建议 | 双指标显示 |
| **B2.5** 突破跌破信号 | 当前触发有效突破/跌破的指数 + 档位 | 与 B1.4 一致 |

### B3 · `tracker/` 骨架

建 `stockagent/tracker/{__init__,classifier,diagnose,alerts,dashboard}.py`。Phase 1-B 填 `diagnose`(指数诊断)+ `dashboard`(渲染);`classifier`/`alerts` 留桩(Phase 1-A 填)。
验收:包可 import;`diagnose` 返回结构化指数诊断 dict。

### B4 · 测试

- 每个 B0 数据函数 + B1 指标纯函数有 pytest;
- 回归测试:复现 S13 历史案例(创业板 2020-07-13 顶、2015 大牛市全程不破 60 线);
- 全量 `pytest tests/ -q` 不破现有 129 个测试。

### B5 · 看板渲染接入

`scripts/research_report.py` 加「指数择时」页签,或新 `scripts/index_timing_report.py` 生成独立 HTML。
验收:生成的 HTML 含五件套;`dashboard_data_check` 覆盖指数新鲜度。

**Phase 1-B DoD**:指数择时层 HTML 看板上线,五件套齐全,数据到最新交易日,pytest 全绿。

---

## §3 Phase 1-A · ETF 三类分类重构(骨架,待 B 完)

> 前置:Phase 1-B 立好 `tracker/` 骨架。数据有坑,按降级策略执行。

| 任务 | 内容 | 降级处理 |
|---|---|---|
| **A0** 数据层 | `fetch_index_pb`(宽基)、`fetch_etf_dividend`(容忍稀疏);`etf_pool.yaml` 加 `style`(主+次)+ 宽基 ETF 跟踪指数映射 | 行业 ETF PB 不抓(无源) |
| **A1** 分类器 | `tracker/classifier.py`:三类标签(主+次),人工标注 + `csrc_industry` 映射校验 | — |
| **A2** 三类指标 | 价值(股息率稀疏+PE 分位+分红稳定)/ 成长(业绩聚合+PE 分位)/ 周期(**行业 PE 反向降级**+筹码+趋势) | 周期 PE 反向明确标「降级」 |
| **A3** 重构 `scoring.py` | 一刀切三因子 → 按类型走差异化主指标 + 通用底盘;`earnings.py` 归成长 | 动到现有 `research_report.html`,需回归测试 |
| **A4** 提醒九条 | `tracker/alerts.py`:D/E1/E2/C1/C2/B1/A1/A2/F1;看板告警区 + 微信双通道 | C1/C2 周期 PB 触顶/底用降级 PE |
| **A5** 看板 | 三类分页 + 告警区 | — |

**Phase 1-A DoD**:ETF 看板三类分类上线,九条提醒双通道,周期型标注 PB 降级。

---

## §4 Phase 2 · 个股层(路线,触发条件满足后细化)

> 触发:Phase 1-B/1-A 跑通 + 个股数据栈就绪。

| 任务 | 内容 |
|---|---|
| **C0** 个股数据栈 | `stock_zh_valuation_baidu`(PE/PB/总市值,免财报硬算)+ `stock_history_dividend`(股息率 derive)+ 修 `fetch_stock_daily`(prefix)+ 必要财务表(营收/扣非/ROE,baidu 不够时) |
| **C1** 个股诊断 | 三类自动判定 + 利润来源归因(S07 茅台拆解)+ 避坑四类(S08)+ 戴维斯双击(S10) |
| **C2** 个股提醒 | A3/G1/G2/E3/E4 |

---

## §5 测试策略

- **纯函数优先**:B1 指标、A2 三类指标、C1 诊断全是纯函数(无 I/O),易测;
- **历史案例回归**:S13 创业板顶、S10 阿里业绩变脸、S07 茅台拆解,各做一个回归测试(既是测试也是文档方法论的活校验);
- **数据幂等**:每个 `fetch_*` 重跑无副作用(ON CONFLICT);
- **不破现有**:全量 `pytest tests/ -q` 保持 129+ 全绿;
- **配置驱动**:阈值(突破 2%/3%、PB 分位 10%/90%、极值百分位)进 `params.yaml`,不在代码硬编码。

---

## §6 里程碑 + Definition of Done

| 里程碑 | 内容 | DoD |
|---|---|---|
| **M1** Phase 1-B | 指数择时层看板 | 五件套齐全;数据到最新交易日;pytest 全绿;HTML 双击可看 |
| **M2** Phase 1-A | ETF 三类分类 + 提醒 | 三类分页;九条提醒双通道(看板+微信);周期 PB 降级已标注 |
| **M3** Phase 2 | 个股诊断 | 三类自动判定;利润归因;避坑四类;个股级提醒 |

---

## §7 下一步(待你认可)

1. 认可本计划 → 我建 Phase 1-B 的 TaskList(B0-B5),从 **B0.1 `fetch_index_daily`** 开始实施;
2. 实施中如遇接口字段与尽调不符(尤其 eastmoney 路径),回退 sina 已验证路径;
3. 每个 B 子任务完成即跑 pytest + 汇报,小步推进。

> 若你希望先调整 Phase 切分、或想先看某个 B 任务的具体代码设计(如 `fetch_index_daily` 的签名/store schema),现在说。
