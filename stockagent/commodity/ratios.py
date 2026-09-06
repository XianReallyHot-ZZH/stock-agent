"""⚖ 比价矩阵(第八看板 · 纯函数)。

spec 来自 params.yaml commodity.ratio_pairs:{name, num, den, note};
num/den = sina 外盘符号(如 SI/GC/CL/CAD)或 '国内:品种'(如 '国内:螺纹钢')。
黄金只做分母不做主语(金银比/油金比的主语是白银/原油;黄金叙事归宏观框架看板)。
"""
from __future__ import annotations

import pandas as pd

from ..config import get_config
from .panel import intl_series

DOM_PREFIX = "国内:"


def resolve_series(store, key: str):
    """spec 键 → 序列:'国内:品种' → commodity_price;其余 → 外盘基准 fut 符号。无 → None。"""
    if key.startswith(DOM_PREFIX):
        s = store.get_commodity_series(key[len(DOM_PREFIX):])
        return s if s is not None and len(s) >= 2 else None
    return intl_series(store, key)


def ratio_series(num: pd.Series, den: pd.Series) -> pd.Series:
    """两腿 inner-join 对齐后相除(跨市场比价也走同一路径:交集日历)。不足 → 空。"""
    if num is None or den is None:
        return pd.Series(dtype=float)
    df = pd.concat([num.astype(float).rename("n"), den.astype(float).rename("d")],
                   axis=1, join="inner").dropna()
    if not len(df):
        return pd.Series(dtype=float)
    return df["n"] / df["d"]


def ratio_rows(store, specs: list[dict] | None = None, config=None) -> list[dict]:
    """比价矩阵行:当前值/全史分位/同比/近60日 + 覆盖区间。序列随行返回(figure 用)。"""
    if specs is None:
        cfg = config or get_config()
        specs = (cfg.params.get("commodity") or {}).get("ratio_pairs") or []
    rows: list[dict] = []
    for spec in specs or []:
        num, den = resolve_series(store, str(spec.get("num", ""))), \
            resolve_series(store, str(spec.get("den", "")))
        s = ratio_series(num, den)
        if s is None or len(s) < 20:          # 不足 20 期不做统计(诚实缺省)
            continue
        cur = float(s.iloc[-1])

        def _chg(k: int) -> float:
            n = min(k, len(s) - 1)
            return cur / float(s.iloc[-1 - n]) - 1.0

        rows.append({
            "name": spec.get("name", ""), "note": spec.get("note", ""),
            "series": s,
            "cur": cur, "pct": float((s < cur).sum()) / len(s),
            "yoy": _chg(252), "m60": _chg(60), "n": int(len(s)),
            "first": str(s.index[0]), "last": str(s.index[-1]),
        })
    return rows
