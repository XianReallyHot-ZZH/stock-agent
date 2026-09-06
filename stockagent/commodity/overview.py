"""📊 商品环境总览(第八看板 · 纯函数)。

官方腿=中证商品指数(ccidx.com,futures_index_ccidx;南华 akshare 端点已死);
自算腿=等权广度/等权合成指数(原料=17 国内品种日频,口径透明、非官方指数,图注标明;
官方指数缺失时合成指数顶上并显式标注)。
"""
from __future__ import annotations

import pandas as pd


def breadth_series(series_map: dict, window: int = 20) -> pd.DataFrame:
    """自算广度(纯):各品种 window 日涨幅的 等权均值(eq_ret) + 上涨品种占比(up_frac)。

    输入 {variety: close Series}(日历不齐用并集,逐日取当日有数据的品种);
    输出 DataFrame(index=日期, columns=[eq_ret, up_frac]),品种全缺 → 空。"""
    rets = {}
    for v, s in series_map.items():
        if s is None or len(s) < window + 1:
            continue
        r = s.astype(float).pct_change(window).dropna()
        if len(r):
            rets[v] = r
    if not rets:
        return pd.DataFrame()
    df = pd.DataFrame(rets)
    out = pd.DataFrame({
        "eq_ret": df.mean(axis=1),
        "up_frac": (df > 0).sum(axis=1) / df.notna().sum(axis=1),
    })
    return out.replace([float("inf"), float("-inf")], pd.NA).dropna()


def synthetic_index(series_map: dict) -> pd.Series:
    """自算等权累计指数(纯,基数 100):每日等权平均收益累计。**非官方指数**——无权重/无展期
    调整,只作环境温度与官方缺失时的 fallback,图注必须标明口径。品种全缺 → 空 Series。"""
    rets = {}
    for v, s in series_map.items():
        if s is None or len(s) < 3:
            continue
        r = s.astype(float).pct_change().dropna()
        if len(r):
            rets[v] = r
    if not rets:
        return pd.Series(dtype=float)
    daily = pd.DataFrame(rets).mean(axis=1)     # 逐日等权(日历不齐自动跳缺)
    return (1.0 + daily.fillna(0.0)).cumprod() * 100.0


def index_snapshot(s: pd.Series) -> dict:
    """指数/合成序列快照(纯):last/date/yoy/m20/m60。不足 → {}。"""
    if s is None or len(s) < 2:
        return {}
    s = s.astype(float).dropna()
    if len(s) < 2:
        return {}

    def _chg(k: int) -> float:
        n = min(k, len(s) - 1)
        return float(s.iloc[-1]) / float(s.iloc[-1 - n]) - 1.0

    return {"last": float(s.iloc[-1]), "date": str(s.index[-1]),
            "yoy": _chg(252), "m60": _chg(60), "m20": _chg(20), "n": int(len(s))}
