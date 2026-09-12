"""候选池 universe 推导(纯函数)——全市场现货快照非 ST(V8, 2026-09 重写)。

宇宙口径(重写锁定): stock_spot 最新快照(名称过滤 ST/*ST/S*ST/SST/退 前缀排除) ∩ 代码段过滤
(60/68/00/30 = 沪主板/科创/深主板/创业;北交所 8/4 与 B股 9 排除)。**不再 ∩ consensus**——
陈氏高业绩池是纯财务筛选,不依赖研报覆盖(方法论对齐;consensus 仍由研究看板维护但与本池无关)。
spot 扩列(V8): mktcap/float_mktcap/pe_dyn/pb 随宇宙带出(市值列+股本反推原料)。
行业 join: industry_member(东财板块,月更) × config/stock_industry.yaml(板块→三类+商品)。
未映射板块 → type=None:不入周期 PB 轨(走 PEG 轨),诚实降级。
"""
from __future__ import annotations

import pandas as pd

# 与 params.yaml stock_pool.universe.exclude_name_prefixes 同默认(配置优先,此处兜底)
EXCLUDED_NAME_PREFIXES = ("ST", "*ST", "S*ST", "SST", "退")
SUPPORTED_CODE_PREFIXES = ("60", "68", "00", "30")


def is_excluded_name(name, prefixes: tuple[str, ...] = EXCLUDED_NAME_PREFIXES) -> bool:
    """ST/*ST/S*ST/SST/退市风险 名称排除。None/空名 → 排除(拿不到名字不冒险)。"""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return True
    s = str(name).strip()
    if not s:
        return True
    return s.startswith(tuple(prefixes))


def is_supported_code(code) -> bool:
    """沪主板 60/科创 68/深主板 00(含 002)/创业 30(含 301)。北交所(8/4/92)与 B股(9)排除——
    sina stock_zh_a_daily 不支持,价格腿会 FetchError。"""
    s = str(code).zfill(6)
    return s.startswith(SUPPORTED_CODE_PREFIXES)


def derive_universe(spot: pd.DataFrame,
                    exclude_prefixes: tuple[str, ...] = EXCLUDED_NAME_PREFIXES) -> pd.DataFrame:
    """现货快照(latest_stock_spot 输出, indexed by code,含 name + V8 扩列)
    → indexed by code [name, close, mktcap, float_mktcap, pe_dyn, pb](扩列缺列安全降级)。

    纯函数:输入帧由调用方(screen.py)从 store 取。空输入契约:带列空帧(冷启动期)。"""
    keep = [c for c in ("name", "close", "mktcap", "float_mktcap", "pe_dyn", "pb")
            if c in spot.columns]
    if len(spot) == 0 or "name" not in spot.columns:
        return pd.DataFrame(columns=keep or ["name"])
    df = spot.copy()
    df = df[[not is_excluded_name(n, exclude_prefixes) for n in df["name"]]]
    df = df[[is_supported_code(c) for c in df.index]]
    return df[keep]


def join_industry(universe: pd.DataFrame, industry_map: pd.DataFrame,
                  class_cfg: dict) -> pd.DataFrame:
    """universe + industry_map(indexed by code [industry]) + class_cfg(板块→{type,commodity})
    → 追加列 [industry, type, commodity_variety]。无行业/行业未映射 → type=None,
    industry 列保留实际板块名或「未映射」(展示用;type 才是分流依据——周期 PB 轨 vs PEG 轨)。"""
    imap = industry_map[["industry"]] if "industry" in industry_map.columns else industry_map
    df = universe.join(imap, how="left")

    def _classify(ind: str):
        if ind is None or (isinstance(ind, float) and pd.isna(ind)):
            return None, None, None
        entry = (class_cfg or {}).get(str(ind)) or {}
        return str(ind), entry.get("type"), entry.get("commodity")

    ind_col = df["industry"] if "industry" in df.columns else \
        pd.Series([None] * len(df), index=df.index, dtype=object)
    triples = [_classify(ind) for ind in ind_col]
    # dtype=object: pandas 3 str dtype 会把 None 强转 NaN,下游 is None 契约会破
    df = df.assign(
        industry=pd.Series([t[0] or "未映射" for t in triples], index=df.index, dtype="object"),
        type=pd.Series([t[1] for t in triples], index=df.index, dtype="object"),
        commodity_variety=pd.Series([t[2] for t in triples], index=df.index, dtype="object"),
    )
    return df.drop(columns=[], errors="ignore")


def industry_coverage(joined: pd.DataFrame) -> dict:
    """头部覆盖率统计: {n, n_typed, n_cyclic, n_growth, n_value, pct}——看板头展示 + 未映射提醒。"""
    n = len(joined)
    if n == 0:
        return {"n": 0, "n_typed": 0, "n_cyclic": 0, "n_growth": 0, "n_value": 0, "pct": 0.0}
    typed = joined["type"].notna().sum()
    return {
        "n": n,
        "n_typed": int(typed),
        "n_cyclic": int((joined["type"] == "cyclic").sum()),
        "n_growth": int((joined["type"] == "growth").sum()),
        "n_value": int((joined["type"] == "value").sum()),
        "pct": float(typed / n),
    }
