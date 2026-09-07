"""🎫 投资标的映射(二期 · 品种→大A可投标的 + 错配度,2026-09)。

「拆大留小」共识的二期落地:商品价格研究(v1)之上,给每个品种列出大A可投标的
(期货ETF/股票ETF/现货ETF/LOF),核心增量=**错配度**:ETF(NAV)涨幅 − 标的商品涨幅(同窗口)。
正=ETF领先(情绪/展期溢价)、负=ETF落后(商品涨了ETF没涨=错杀观察,或股票端有独立逻辑)。
NAV 用 acc_nav 复权连续;期货ETF 的 NAV 含展期结构、QDII 含汇率——读数是「含摩擦的跟踪差」。
**永不进引擎宇宙**(T+0 品种与引擎 T+1 假设不合);偏离度/筹码全家桶在行业研究看板,此处不复刻。
"""
from __future__ import annotations

import pandas as pd

from ..config import get_config
from ..data import fetcher
from . import panel as pnl


def _ret_windows(s: pd.Series) -> dict:
    """序列 → {cur, date, m10, m20, m60, yoy}(近10/20/60日+同比涨幅,窗口不足自动收缩)。空 → {}。"""
    if s is None or len(s) < 2:
        return {}
    s = s.astype(float).dropna()
    s = s[s > 0]
    if len(s) < 2:
        return {}

    def _chg(k: int) -> float:
        n = min(k, len(s) - 1)
        return float(s.iloc[-1]) / float(s.iloc[-1 - n]) - 1.0

    return {"cur": float(s.iloc[-1]), "date": str(s.index[-1]),
            "m10": _chg(10), "m20": _chg(20), "m60": _chg(60), "yoy": _chg(252)}


def _ref_series(store, variety: str, ref: str):
    """错配对照腿:ref=dom → 国内价(大A投资指导口径);ref=intl → 该品种国际基准序列。"""
    bench = fetcher.COMMODITY_BENCHMARKS.get(variety)
    if ref == "intl" and bench:
        return pnl.intl_series(store, bench[0])
    return store.get_commodity_series(variety)


def target_rows(store, config=None) -> tuple[list[dict], list[dict]]:
    """投资标的映射行。返回 (rows, nav_missing):

    rows 每行 = (标的 × 品种) 错配快照:{symbol/name/kind/variety/in_pool/note, etf, comm,
    gap10/gap20/gap60/gap_yoy, ref_kind};nav_missing = NAV 未回填的标的(提示跑 --targets)。
    品种无数据 → 该行诚实跳过(不出假数字)。"""
    cfg = config or get_config()
    specs = (cfg.params.get("commodity") or {}).get("targets") or []
    pool = set(cfg.tracked_symbols())
    rows: list[dict] = []
    missing: list[dict] = []
    for spec in specs or []:
        sym = str(spec.get("symbol", ""))
        if not sym:
            continue
        etf: dict = {}
        if hasattr(store, "get_nav_series"):
            try:
                nav = store.get_nav_series(sym)
            except Exception:  # noqa: BLE001 — 表缺失,静默降级
                nav = None
            if nav is not None and len(nav) and "acc_nav" in nav.columns:
                etf = _ret_windows(nav["acc_nav"])
        if not etf:
            missing.append({"symbol": sym, "name": spec.get("name", "")})
            continue
        ref = str(spec.get("ref", "dom"))
        for v in spec.get("varieties") or []:
            st = _ret_windows(_ref_series(store, v, ref))
            if not st:
                continue
            rows.append({
                "symbol": sym, "name": spec.get("name", ""), "kind": spec.get("kind", ""),
                "variety": v, "in_pool": sym in pool, "note": spec.get("note", ""),
                "etf": etf, "comm": st, "ref_kind": ref,
                "gap10": etf["m10"] - st["m10"], "gap20": etf["m20"] - st["m20"],
                "gap60": etf["m60"] - st["m60"], "gap_yoy": etf["yoy"] - st["yoy"],
            })
    return rows, missing
