"""Pure functions for the ⑨ 恐惧贪婪指数 (Fear & Greed) composite — 指数择时层·只读诊断旁路。

温度计不是开关:和 ⑧地量一样实证无择时 edge,只给市场情绪的快速读数,供人综合判断;
**永不喂交易引擎**(ADR:与 research/tracker 旁路同围栏)。

5 成分(类别均衡,每类一票,等权)→ 每个做 5 年滚动百分位 → 0-100(恐惧方向反转)→ 等权平均 → 五档标签。

成分(数据均来自已有表,除杠杆用新表 market_margin;口径与 ⑧/③ 复用):
  动量   momentum    — 上证综指 close/MA60−1 的 5y 滚动分位(偏离越正=越贪婪)
  流动性 turnover    — 两市成交额/MA250 的 5y 滚动分位(量越大=越贪婪;口径同 ⑧地量)
  波动率 volatility  — 上证综指 20 日实现波动率的 5y 滚动分位,**反转**(vol↑=恐惧→低分)
  估值   valuation   — 全市场 PB(中位)的 5y 滚动分位(PB 越高=越贪婪)
  杠杆   leverage    — 沪深两市融资余额合计 20 日变化率的 5y 滚动分位(2026-09 批次2.1 从沪市单边升级;ROC 去名义漂移)

归一化选百分位(对极端值鲁棒、不假设正态;CNN/baifenwei 先例),窗口 5y(A 股一轮牛熊)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOW = 252 * 5       # 5 年滚动百分位窗口(A 股一轮牛熊;CNN 隐含惯例)
MIN_BARS = 60          # 窗口内最少有效样本(<此 → NaN,不进分母;约 3 个月)
MA_PERIOD = 60         # 动量成分:close/MA60 偏离(复用 indicators.MA_PERIOD 口径)
TURNOVER_MA = 250      # 流动性成分:成交额/MA250(口径同 ⑧)
VOL_WINDOW = 20        # 波动率成分:20 日实现波动率(年化系数 √20)
LEVERAGE_ROC = 20      # 杠杆成分:融资余额 20 日变化率(去名义漂移)

_COMPONENTS = ("momentum", "turnover", "volatility", "valuation", "leverage")


def rolling_percentile(series: pd.Series, window: int = WINDOW,
                       min_bars: int = MIN_BARS) -> pd.Series:
    """每个 bar:当前值在 [i-window+1, i] 窗口内的分位(0..1,0=最低,1=最高)。

    防前视(只用当时及之前的数据);窗口内有效样本 <min_bars → NaN。百分位法对极端值鲁棒、
    不假设正态(优于 z-score,免 winsorize)。返回与输入 dropna 后等长的 Series。"""
    s = pd.Series(series, dtype=float).dropna()
    if len(s) == 0:
        return pd.Series([], dtype=float)
    vals = s.to_numpy(dtype=float)
    idx = s.index
    out = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        lo = max(0, i - window + 1)
        seen = vals[lo:i + 1]
        seen = seen[~np.isnan(seen)]
        if len(seen) < min_bars:
            continue
        out[i] = float((seen < vals[i]).sum()) / len(seen)
    return pd.Series(out, index=idx)


def _ma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


# ---- 单成分:原始序列 → 0-100 分数(高分=贪婪;恐惧方向者先取分位再 100−)----
def momentum_component(close: pd.Series) -> pd.Series:
    """动量:close/MA60−1 → 5y 分位 ×100。偏离越正(线上方越远)= 越贪婪。"""
    close = pd.Series(close, dtype=float).dropna()
    if len(close) <= MA_PERIOD:
        return pd.Series([], dtype=float)
    dev = close / _ma(close, MA_PERIOD) - 1.0
    return rolling_percentile(dev) * 100.0


def turnover_component(turnover: pd.Series) -> pd.Series:
    """流动性:成交额/MA250 → 5y 分位 ×100。量越大 = 越贪婪(天量=贪婪,地量=恐惧)。"""
    t = pd.Series(turnover, dtype=float)
    t = t[t > 0]
    if len(t) <= TURNOVER_MA:
        return pd.Series([], dtype=float)
    ratio = t / t.rolling(TURNOVER_MA).mean()
    return rolling_percentile(ratio) * 100.0


def volatility_component(close: pd.Series) -> pd.Series:
    """波动率:20 日实现波动率(日收益 std×√20)→ 5y 分位 → **反转**×100(vol↑=恐惧→低分)。"""
    close = pd.Series(close, dtype=float).dropna()
    if len(close) <= VOL_WINDOW:
        return pd.Series([], dtype=float)
    rv = close.pct_change().rolling(VOL_WINDOW).std() * np.sqrt(VOL_WINDOW)
    return 100.0 - rolling_percentile(rv) * 100.0


def valuation_component(pb: pd.Series) -> pd.Series:
    """估值:全市场 PB(中位)→ 5y 分位 ×100。PB 越高(越贵)= 越贪婪。"""
    pb = pd.Series(pb, dtype=float).dropna()
    if len(pb) == 0:
        return pd.Series([], dtype=float)
    return rolling_percentile(pb) * 100.0


def leverage_component(financing: pd.Series) -> pd.Series:
    """杠杆:融资余额 20 日变化率 → 5y 分位 ×100。杠杆扩张越快 = 越贪婪(pct_change 去名义漂移)。"""
    f = pd.Series(financing, dtype=float).dropna()
    if len(f) <= LEVERAGE_ROC:
        return pd.Series([], dtype=float)
    roc = f.pct_change(LEVERAGE_ROC)
    return rolling_percentile(roc) * 100.0


def fear_greed_series(components: dict) -> pd.Series:
    """逐日合成恐惧贪婪分(0-100)。

    components = {name: 0-100 分 Series(方向已统一为高分=贪婪)}。
    对齐到 union index;缺失成分当日用其**前值**填充(不放大其他权重;只在有过该成分时才填);
    每日取当日**可用成分均值**(等权;v1 每类一票 → 即类别均衡)。无任何成分 / 某日全缺 → NaN。"""
    parts = []
    for name in _COMPONENTS:
        s = components.get(name)
        if s is None or len(s) == 0:
            continue
        parts.append(pd.Series(s, dtype=float).rename(name))
    if not parts:
        return pd.Series([], dtype=float)
    panel = pd.concat(parts, axis=1)      # union index;缺 → NaN
    panel = panel.sort_index()            # 成分来自 store 的字符串日期(ISO)→ 字典序=时间序;
                                          # 必须先排序再 ffill,否则错乱顺序会污染前填与 iloc[-1]
    panel = panel.ffill()                  # 缺失成分用前值(不进分母规则;前导 NaN 仍 NaN → 排除)
    score = panel.mean(axis=1, skipna=True)  # 每日可用成分均值;全 NaN 日 → NaN
    return score.clip(0.0, 100.0)


def classify(score: float) -> str:
    """0-100 → 五档标签(市场惯例)。NaN/None → '—'。

    0-25 极度恐惧 / 25-45 恐惧 / 45-55 中性 / 55-75 贪婪 / 75-100 极度贪婪。"""
    if score is None or (isinstance(score, float) and score != score):
        return "—"
    s = float(score)
    if s < 25:
        return "极度恐惧"
    if s < 45:
        return "恐惧"
    if s <= 55:
        return "中性"
    if s <= 75:
        return "贪婪"
    return "极度贪婪"
