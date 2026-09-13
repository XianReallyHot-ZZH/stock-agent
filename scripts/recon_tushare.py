"""tushare 迁移对账 (docs/EXECUTION_PLAN-tushare迁移.md · 交付物3 · 对账容差表).

每腿一个子命令: 新旧源重叠区间逐期 diff, 按族判定——精确族零容差、高精度族
|Δ|≤0.001、口径差族只量化不拦(出报告人工放行)。产物落 data/recon/。

用法:
  python scripts/recon_tushare.py valuation [code ...]   # 1.1 个股估值 baidu vs daily_basic
  python scripts/recon_tushare.py macro                 # 1.2-1.6 金十宏观族 双实拉精确对账
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import pandas as pd  # noqa: E402

from stockagent.config import get_config  # noqa: E402
from stockagent.data import fetcher  # noqa: E402

RECON_DIR = ROOT / "data" / "recon"

# 与 baidu 可比的 4 指标(pcf 不迁; ps_ttm/dv_ratio/turnover/circ_mv 为 tushare 新增无对照)
BAIDU_COMMON = ["pe_ttm", "pe_static", "pb", "market_cap"]


def _diff_stats(baidu: pd.Series, ts: pd.Series) -> dict:
    """重叠期 diff 统计(口径差族: 量化不拦)。同日对齐, baidu 稀疏半月级→重叠=其全部日期。"""
    both = pd.concat([baidu.rename("baidu"), ts.rename("ts")], axis=1, join="inner")
    if not len(both):
        return {"n_overlap": 0}
    d = (both["baidu"] - both["ts"]).abs()
    rel = d / both["ts"].abs().where(both["ts"].abs() > 1e-6)
    return {
        "n_overlap": len(both),
        "mean_abs": float(d.mean()),
        "p95_abs": float(d.quantile(0.95)),
        "max_abs": float(d.max()),
        "median_rel": float(rel.median()) if rel.notna().any() else float("nan"),
        "max_rel": float(rel.max()) if rel.notna().any() else float("nan"),
    }


def recon_valuation(codes: list[str] | None = None) -> Path:
    """1.1 估值腿: DB 里的 baidu 历史(旧源) vs tushare daily_basic 实拉(新源) 重叠对账。"""
    from stockagent.data.manager import DataManager
    cfg = get_config()
    codes = codes or DataManager.STOCK_WATCHLIST
    conn = sqlite3.connect(cfg.db_path)
    lines = ["# tushare 迁移对账 · 1.1 个股估值 (口径差族: 量化不拦, 人工放行)", "",
             f"- 旧源: baidu(稀疏半月级, DB 存量) vs 新源: daily_basic(日频全史)",
             f"- 可比指标: {BAIDU_COMMON}(pcf 无消费方不迁; ps_ttm/dv_ratio/turnover/circ_mv 新增无对照)",
             "- 单位: market_cap 两源统一为亿元(tushare total_mv 万元 ×1e-4 对齐 baidu 存量)", ""]
    n_rows = n_pairs = 0
    for code in codes:
        try:
            frames = fetcher.fetch_valuation_tushare(code)
        except Exception as e:  # noqa: BLE001
            lines.append(f"- {code}: tushare 拉取失败 — {str(e)[:90]}")
            continue
        for ind in BAIDU_COMMON:
            baidu = pd.read_sql_query(
                "SELECT date,value FROM stock_valuation WHERE symbol=? AND indicator=? "
                "AND source='baidu' ORDER BY date", conn, params=(code, ind))
            if not len(baidu) or ind not in frames:
                continue
            b = baidu.set_index("date")["value"]
            t = frames[ind]["value"]
            st = _diff_stats(b, t)
            if st["n_overlap"] == 0:
                continue
            n_pairs += 1
            n_rows += st["n_overlap"]
            lines.append(
                f"- {code} {ind}: 重叠 {st['n_overlap']} 期 · |Δ| 均值 {st['mean_abs']:.3f}"
                f" / p95 {st['p95_abs']:.3f} / 最大 {st['max_abs']:.3f}"
                f" · 相对Δ中位 {st['median_rel']*100:.1f}% / 最大 {st['max_rel']*100:.1f}%"
                f" · 新源覆盖 {len(t)} 期(至 {t.index[-1]})")
        lines.append(f"- {code}: tushare 指标 {sorted(frames)}")
    conn.close()
    lines += ["", f"合计: {n_pairs} 组指标对照 · {n_rows} 期重叠",
              "> 放行判据(口径差族): PE 族相对Δ中位 <2%、PB <2%、总市值 <1% 即视为口径一致可切换;",
              "> 超限则先查加权口径差异(静/TTM、归母口径)再决定。"]
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    out = RECON_DIR / "tushare_valuation.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[written] {out}")
    return out


def recon_macro(_args=None) -> Path:
    """1.2-1.6 金十宏观族: 新旧源**双实拉**重叠对账(表无 source 列,DB 存量不可分源,
    故两侧都现拉)。精确族——官方同源数,容差 0。"""
    def _to_df(rows, key):
        return pd.DataFrame(rows).set_index(key) if rows else pd.DataFrame()

    pairs = [
        ("shibor", lambda: fetcher.fetch_shibor(), lambda: fetcher.fetch_shibor_tushare(),
         "date", ["overnight", "w1", "w2", "m1", "m3", "m6", "m9", "y1"]),
        ("lpr", lambda: fetcher.fetch_lpr(), lambda: fetcher.fetch_lpr_tushare(),
         "date", ["lpr1y", "lpr5y"]),   # base* 金十独有,不入对账
        ("money(M2/M1/M0)", lambda: fetcher.fetch_china_money_supply(),
         lambda: fetcher.fetch_money_supply_tushare(), "month",
         ["m2_amt", "m2_yoy", "m1_amt", "m1_yoy", "m0_amt", "m0_yoy"]),
        ("cpi_yoy", lambda: fetcher.fetch_china_real(skip=("ppi_yoy", "pmi", "retail_yoy", "ind_yoy"))["cpi_yoy"],
         lambda: fetcher.fetch_cpi_ppi_tushare()["cpi_yoy"], "month", ["value"]),
        ("ppi_yoy", lambda: fetcher.fetch_china_real(skip=("cpi_yoy", "pmi", "retail_yoy", "ind_yoy"))["ppi_yoy"],
         lambda: fetcher.fetch_cpi_ppi_tushare()["ppi_yoy"], "month", ["value"]),
        ("tsf_inc", lambda: fetcher.fetch_china_tsf(), lambda: fetcher.fetch_tsf_inc_tushare(),
         "month", ["tsf_inc"]),
    ]
    lines = ["# tushare 迁移对账 · 1.2-1.6 金十宏观族 (精确族: 容差 0)", "",
             "- 双侧现拉(金十 vs tushare),重叠期逐格比对;金十被拦的腿标 SKIP 不阻塞其余", ""]
    for name, fa, fb, key, cols in pairs:
        try:
            a, b = _to_df(fa(), key), _to_df(fb(), key)
        except Exception as e:  # noqa: BLE001
            lines.append(f"- {name}: SKIP — 源拉取失败 {str(e)[:80]}")
            continue
        if not len(a) or not len(b):
            lines.append(f"- {name}: SKIP — 一侧为空 (金十 {len(a)} / tushare {len(b)})")
            continue
        both = a.join(b, how="inner", lsuffix="_j", rsuffix="_t")
        bad = 0
        detail = []
        for c in cols:
            cj, ct = f"{c}_j", f"{c}_t"
            if cj not in both or ct not in both:
                continue
            d = (pd.to_numeric(both[cj], errors="coerce")
                 - pd.to_numeric(both[ct], errors="coerce")).abs()
            both_nan = both[cj].isna() & both[ct].isna()
            d = d[~both_nan].dropna()
            n_bad = int((d > 1e-6).sum())
            bad += n_bad
            if n_bad:
                detail.append(f"{c}: {n_bad} 格不一致(max |Δ|={float(d.max()):.4g})")
        verdict = "PASS — 全部一致" if bad == 0 else f"FAIL — {bad} 格不一致"
        lines.append(f"- {name}: 重叠 {len(both)} 期 · {verdict}"
                     + ("; ".join(detail) if detail else ""))
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    out = RECON_DIR / "tushare_macro.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[written] {out}")
    return out


def recon_nav(_args=None) -> Path:
    """1.7 ETF净值腿: DB 天天基金存量(source='em') vs tushare fund_nav 实拉。
    高精度族——重叠≥250日, 逐日 |Δ|≤0.001 元 且 ≥99.9% 落内。"""
    import sqlite3
    from stockagent.config import get_config
    cfg = get_config()
    conn = sqlite3.connect(cfg.db_path)
    syms = cfg.all_symbols()
    lines = ["# tushare 迁移对账 · 1.7 ETF净值 (高精度族: |Δ|≤0.001元 · ≥99.9%落内)", ""]
    n_ok = n_fail = n_skip = 0
    for sym in syms:
        em = pd.read_sql_query(
            "SELECT date,unit_nav,acc_nav FROM etf_nav WHERE symbol=? AND source='em' "
            "ORDER BY date", conn, params=(sym,))
        if not len(em):
            n_skip += 1
            continue
        try:
            ts = fetcher.fetch_etf_nav_tushare(sym)
        except Exception as e:  # noqa: BLE001
            lines.append(f"- {sym}: SKIP — tushare 失败 {str(e)[:80]}")
            n_skip += 1
            continue
        em = em.set_index("date")
        both = em.join(ts, how="inner", lsuffix="_em", rsuffix="_ts")
        if len(both) < 250:
            lines.append(f"- {sym}: SKIP — 重叠仅 {len(both)} 日(<250)")
            n_skip += 1
            continue
        verdicts = []
        ok_all = True
        for c in ("unit_nav", "acc_nav"):
            cj, ct = f"{c}_em", f"{c}_ts"
            if cj not in both or ct not in both:
                continue
            d = (pd.to_numeric(both[cj]) - pd.to_numeric(both[ct])).abs().dropna()
            if not len(d):
                continue
            within = float((d <= 0.001).mean())
            ok = within >= 0.999
            ok_all &= ok
            verdicts.append(f"{c}: 重叠 {len(d)} 日, 落内 {within:.3%}, max|Δ|={float(d.max()):.4f}"
                            + ("" if ok else " ⚠超阈"))
        if ok_all:
            n_ok += 1
        else:
            n_fail += 1
        lines.append(f"- {sym}: " + ("PASS — " if ok_all else "FAIL — ") + "; ".join(verdicts))
    conn.close()
    lines += ["", f"合计: PASS {n_ok} · FAIL {n_fail} · SKIP {n_skip} (共 {len(syms)} 标的)",
              "> 放行判据(高精度族): 全部标的 PASS 才切主源;个别 FAIL 先查该标的分红日口径。"]
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    out = RECON_DIR / "tushare_nav.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[written] {out}")
    return out


def recon_index(_args=None) -> Path:
    """1.8 指数日线: DB sina 存量 vs tushare index_daily 实拉。精确族(close 同官方容差0;
    volume 另报比值——单位若不一致只报不拦)。"""
    import sqlite3
    from stockagent.config import get_config
    from stockagent.data.manager import DataManager
    cfg = get_config()
    conn = sqlite3.connect(cfg.db_path)
    lines = ["# tushare 迁移对账 · 1.8 指数日线 (精确族: close 容差 0)", ""]
    for sym in DataManager.BROAD_INDICES:
        sina = pd.read_sql_query(
            "SELECT date,close,volume FROM index_daily WHERE symbol=? AND source='sina_raw' "
            "ORDER BY date", conn, params=(sym,))
        if not len(sina):
            lines.append(f"- {sym}: SKIP — DB 无 sina 存量")
            continue
        try:
            ts = fetcher.fetch_index_daily_tushare(sym)
        except Exception as e:  # noqa: BLE001
            lines.append(f"- {sym}: SKIP — tushare 失败 {str(e)[:80]}")
            continue
        both = sina.set_index("date").join(ts, how="inner", lsuffix="_s", rsuffix="_t")
        if not len(both):
            lines.append(f"- {sym}: SKIP — 无重叠")
            continue
        d_close = (both["close_s"] - both["close_t"]).abs()
        n_bad = int((d_close > 1e-6).sum())
        vol_ratio = (both["volume_t"] / both["volume_s"].where(both["volume_s"] > 0)).dropna()
        ratio_med = float(vol_ratio.median()) if len(vol_ratio) else float("nan")
        lines.append(
            f"- {sym}: 重叠 {len(both)} 日 · close 不一致 {n_bad} 格(max {float(d_close.max()):.2e})"
            f" · volume 比 t/s 中位 {ratio_med:.4f}({'单位一致' if 0.999 < ratio_med < 1.001 else '⚠单位不同'})")
    conn.close()
    lines += ["", "> 放行判据: close 全一致即切主源;volume 比值≈1 为单位一致佐证,≠1 则以 tushare 值为准重灌(单位自洽)。"]
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    out = RECON_DIR / "tushare_index.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[written] {out}")
    return out


def recon_dividend(_args=None) -> Path:
    """1.9 分红: DB sina 存量 vs tushare dividend 实拉(同 ex_date 对齐)。
    精确族: cash_per_share 容差 0;送转按 和 对账(tushare 送转合一)。"""
    import sqlite3
    from stockagent.config import get_config
    from stockagent.data.manager import DataManager
    cfg = get_config()
    conn = sqlite3.connect(cfg.db_path)
    lines = ["# tushare 迁移对账 · 1.9 分红 (精确族: cash 容差 0 · 送转按和对齐)", ""]
    n_pairs = 0
    for sym in DataManager.STOCK_WATCHLIST:
        sina = pd.read_sql_query(
            "SELECT ex_date,cash_per_share,stock_div_10,trans_10 FROM stock_dividend "
            "WHERE symbol=? AND source='sina' ORDER BY ex_date", conn, params=(sym,))
        if not len(sina):
            continue
        try:
            ts = fetcher.fetch_stock_dividend_tushare(sym)
        except Exception as e:  # noqa: BLE001
            lines.append(f"- {sym}: SKIP — tushare 失败 {str(e)[:80]}")
            continue
        if not len(ts):
            lines.append(f"- {sym}: ⚠ sina 有 {len(sina)} 行,tushare 实施行为 0")
            continue
        both = sina.set_index("ex_date").join(ts, how="inner", lsuffix="_s", rsuffix="_t")
        if not len(both):
            lines.append(f"- {sym}: ⚠ 无同 ex_date 重叠 (sina {len(sina)} / ts {len(ts)})")
            continue
        d_cash = (both["cash_per_share_s"] - both["cash_per_share_t"]).abs().dropna()
        sum_s = both["stock_div_10_s"].fillna(0) + both["trans_10_s"].fillna(0)
        sum_t = both["stock_div_10_t"].fillna(0) + both["trans_10_t"].fillna(0)
        d_sum = (sum_s - sum_t).abs()
        n_pairs += 1
        lines.append(
            f"- {sym}: 重叠 {len(both)} 次 · cash 不一致 {int((d_cash > 1e-6).sum())} 格"
            f"(max {float(d_cash.max()):.4f}) · 送转和 不一致 {int((d_sum > 1e-6).sum())} 格"
            f"(max {float(d_sum.max()):.2f})")
    conn.close()
    lines += ["", f"对照 {n_pairs} 组;>放行判据: cash/送转和 全一致(零星 >0 差异逐例核对除权日口径)。"]
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    out = RECON_DIR / "tushare_dividend.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[written] {out}")
    return out


def recon_commodity(_args=None) -> Path:
    """1.11 商品日线: DB sina 连续存量 vs tushare 主力连续实拉。口径差族——正常日应一致,
    换月附近差 1-2 天主力判定差属预期,只量化不拦。"""
    import sqlite3
    from stockagent.config import get_config
    cfg = get_config()
    conn = sqlite3.connect(cfg.db_path)
    lines = ["# tushare 迁移对账 · 1.11 商品主力连续 (口径差族: 换月差量化不拦)", ""]
    for v, code in fetcher.COMMODITY_CODES.items():
        suf = fetcher.COMMODITY_TS_SUFFIX.get(code)
        sina = pd.read_sql_query(
            "SELECT date,close FROM commodity_price WHERE variety=? AND source='akshare_futures' "
            "ORDER BY date", conn, params=(v,))
        if not len(sina) or not suf:
            lines.append(f"- {v}: SKIP — 无 sina 存量或无后缀映射")
            continue
        try:
            df = fetcher.fetch_commodity_price_tushare([v])
        except Exception as e:  # noqa: BLE001
            lines.append(f"- {v}: SKIP — tushare 失败 {str(e)[:80]}")
            continue
        both = sina.set_index("date").join(df.set_index("date"), how="inner",
                                           lsuffix="_s", rsuffix="_t")
        if not len(both):
            lines.append(f"- {v}: ⚠ 无重叠 (sina {len(sina)} / ts {len(df)})")
            continue
        rel = ((both["close_s"] - both["close_t"]).abs()
               / both["close_t"].where(both["close_t"] != 0)).dropna()
        n_big = int((rel > 0.01).sum())
        lines.append(
            f"- {v}({code}.{suf}): 重叠 {len(both)} 日 · |Δ|/价 ≤0.1% 占 {(rel <= 0.001).mean():.1%}"
            f" · 中位 {rel.median():.4%} · 最大 {rel.max():.2%} · >1% 日数 {n_big}"
            f"{'(换月判定差,预期内)' if 0 < n_big <= 30 else '⚠偏多需人工看'} · ts 覆盖 {len(df)} 日")
    conn.close()
    lines += ["", "> 放行判据(口径差族): 正常日 ≤0.1% 占比 >99%、>1% 日数 ≤30(两源主力切换日差)即可切主源。"]
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    out = RECON_DIR / "tushare_commodity.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[written] {out}")
    return out


def recon_margin(_args=None) -> Path:
    """批次2.1 两融: DB akshare 沪市存量 vs tushare margin 沪市侧。精确族(同官方数)。"""
    import sqlite3
    from stockagent.config import get_config
    cfg = get_config()
    conn = sqlite3.connect(cfg.db_path)
    sse = pd.read_sql_query(
        "SELECT date,financing_sse,total_margin_sse FROM market_margin "
        "WHERE source='sse' ORDER BY date", conn)
    conn.close()
    lines = ["# tushare 对账 · 批次2.1 两融 (精确族: 沪市侧容差 0)", ""]
    if not len(sse):
        lines.append("- SKIP — DB 无 akshare 沪市存量")
    else:
        try:
            ts = fetcher.fetch_margin_tushare(start=sse["date"].min(),
                                              end=sse["date"].max())
        except Exception as e:  # noqa: BLE001
            ts = None
            lines.append(f"- tushare 拉取失败: {str(e)[:100]}")
        if ts is not None:
            both = sse.set_index("date").join(ts, how="inner", rsuffix="_t")
            for c in ("financing_sse", "total_margin_sse"):
                d = (pd.to_numeric(both[c]) - pd.to_numeric(both[f"{c}_t"])).abs().dropna()
                if not len(d):
                    continue
                lines.append(f"- {c}: 重叠 {len(d)} 日 · 不一致 {int((d > 1.0).sum())} 格"
                             f"(max {float(d.max()):.0f} 元)")
            cs = ts["financing_cs"].dropna()
            lines.append(f"- tushare 沪深合计覆盖 {len(cs)} 日({cs.index.min()}..{cs.index.max()})"
                         f" · 最新合计 {float(cs.iloc[-1])/1e8:.0f} 亿元)")
    lines += ["", "> 放行判据: 沪市侧零格不一致(两源同为交易所官方数);合计列为新增无对照。"]
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    out = RECON_DIR / "tushare_margin.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[written] {out}")
    return out


def recon_turnover(_args=None) -> Path:
    """批次2.2 两市成交额: DB baostock 存量 vs tushare daily_info 官方口径。
    口径差族——两官方源统计范围或有差(A股/含基金债),⑧⑨消费为比值型,量化不拦。"""
    import sqlite3
    from stockagent.config import get_config
    cfg = get_config()
    conn = sqlite3.connect(cfg.db_path)
    bs = pd.read_sql_query(
        "SELECT date,sse,sz,total FROM market_turnover WHERE source='baostock' "
        "ORDER BY date", conn)
    conn.close()
    lines = ["# tushare 对账 · 批次2.2 两市成交额 (口径差族: 比值型消费,量化不拦)", ""]
    if not len(bs):
        lines.append("- SKIP — DB 无 baostock 存量")
    else:
        try:
            ts = fetcher.fetch_market_turnover_tushare(start=bs["date"].min(),
                                                       end=bs["date"].max())
        except Exception as e:  # noqa: BLE001
            ts = None
            lines.append(f"- tushare 拉取失败: {str(e)[:100]}")
        if ts is not None:
            both = bs.set_index("date").join(ts, how="inner", lsuffix="_b", rsuffix="_t")
            rel = ((both["total_b"] - both["total_t"]).abs()
                   / both["total_t"].where(both["total_t"] > 0)).dropna()
            if len(rel):
                lines.append(
                    f"- total: 重叠 {len(rel)} 日 · 相对Δ中位 {rel.median():.3%} · "
                    f"p95 {rel.quantile(0.95):.3%} · 最大 {rel.max():.2%} · "
                    f"≤0.5% 占 {(rel <= 0.005).mean():.1%}")
                lines.append(f"- tushare 覆盖 {len(ts)} 日({ts.index.min()}..{ts.index.max()})")
    lines += ["", "> 放行判据(口径差族): 相对Δ中位 <1% 即切官方口径(⑧地量=成交额/MA250 比值,口径平移无碍)。"]
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    out = RECON_DIR / "tushare_turnover.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[written] {out}")
    return out


SUBS = {"valuation": recon_valuation, "macro": recon_macro, "nav": recon_nav,
        "index": recon_index, "dividend": recon_dividend, "commodity": recon_commodity,
        "margin": recon_margin, "turnover": recon_turnover}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in SUBS:
        print(__doc__)
        sys.exit(2)
    SUBS[sys.argv[1]](sys.argv[2:] or None)


if __name__ == "__main__":
    main()
