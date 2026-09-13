"""tushare 迁移对账 (docs/EXECUTION_PLAN-tushare迁移.md · 交付物3 · 对账容差表).

每腿一个子命令: 新旧源重叠区间逐期 diff, 按族判定——精确族零容差、高精度族
|Δ|≤0.001、口径差族只量化不拦(出报告人工放行)。产物落 data/recon/。

用法:
  python scripts/recon_tushare.py valuation [code ...]   # 1.1 个股估值 baidu vs daily_basic
  python scripts/recon_tushare.py macro                 # 1.2-1.6 金十宏观族 双实拉精确对账
  python scripts/recon_tushare.py tsf                   # 批次2.5 社融存量 新旧口径并排快照对照
  python scripts/recon_tushare.py wsr                   # 批次2.3 交易所仓单扩腿快照对照(3×修正验证)
  python scripts/recon_tushare.py roll                  # 批次2.4 展期口径 raw vs adjusted 快照对照
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


def recon_tsf_stock(_args=None) -> Path:
    """批次2.5 社融存量: 升级类——旧口径(增量TTM/M2 脉冲代理,DB) vs 新口径(存量同比,
    sf_month.stk_endval)并排快照对照 + 存量自洽(Δ存量 vs 增量)。"""
    import sqlite3
    from stockagent.config import get_config
    from stockagent.tracker import money_conditions as mcm
    cfg = get_config()
    conn = sqlite3.connect(cfg.db_path)
    tsf = pd.read_sql_query("SELECT month,tsf_inc,ts_stock FROM china_tsf ORDER BY month",
                            conn).set_index("month")
    money = pd.read_sql_query("SELECT month,m2_amt FROM china_money_supply ORDER BY month",
                              conn).set_index("month")
    conn.close()
    lines = ["# tushare 批次2.5 社融存量 (升级类: 旧口径存档+新口径并排快照对照,不设 PASS/FAIL)", "",
             "- 新口径: 存量同比 = sf_month.stk_endval(万亿,2002-12 起·早年仅年末值) 对 12 个月前",
             "- 旧口径: 脉冲代理 = 增量TTM(12月滚动和)/当期 M2 余额(两者分母口径不同,形态对照非数值对齐)", ""]
    try:
        rows = fetcher.fetch_tsf_stock_tushare()
    except Exception as e:  # noqa: BLE001
        rows = []
        lines.append(f"- SKIP — sf_month 拉取失败 {str(e)[:90]}")
    if rows:
        ts_new = pd.Series({r["month"]: r["ts_stock"] for r in rows}, dtype=float).sort_index()
        lines.append(f"- sf_month 覆盖 {len(ts_new)} 月({ts_new.index.min()}..{ts_new.index.max()})"
                     f" · 最新 {float(ts_new.iloc[-1]):.2f} 万亿")
        if len(tsf) and "ts_stock" in tsf.columns:
            db_stk = pd.to_numeric(tsf["ts_stock"], errors="coerce").dropna()
            both = pd.concat([db_stk.rename("db"), ts_new.rename("ts")], axis=1, join="inner").dropna()
            if len(both):
                n_bad = int(((both["db"] - both["ts"]).abs() > 1e-6).sum())
                lines.append(f"- DB 已灌存量 vs 重拉: 重叠 {len(both)} 月 · 不一致 {n_bad} 格"
                             f"(max {float((both['db'] - both['ts']).abs().max()):.4g})")
        # 存量自洽: Δ存量(亿) vs 当月增量(亿)——同源 sf_month,口径调整应小幅
        d = ts_new.diff().dropna() * 1e4
        inc_map = pd.to_numeric(tsf["tsf_inc"], errors="coerce") if len(tsf) else pd.Series(dtype=float)
        both2 = pd.concat([d.rename("dstk"), inc_map.rename("inc")], axis=1, join="inner").dropna()
        both2 = both2[both2["inc"].abs() > 1]
        if len(both2):
            rel = ((both2["dstk"] - both2["inc"]).abs() / both2["inc"].abs())
            lines.append(f"- 存量自洽(Δ存量 vs 当月增量,DB增量列): {len(both2)} 月 · 相对差中位 "
                         f"{float(rel.median()):.1%} / p95 {float(rel.quantile(0.95)):.1%}(官方口径调整,预期小幅)")
        # 新旧口径并排: 存量同比 vs 脉冲代理 形态对照(相关系数+近24月快照)
        yoy = mcm.tsf_stock_yoy_series(ts_new)
        n_monthly = len(yoy[yoy.index >= "2018-01-01"])
        lines.append(f"- 存量同比: {len(yoy)} 点(含早年年末对年末稀疏点) · 月度连续段 {n_monthly} 月"
                     f" · 最新 {yoy.index[-1]} = {float(yoy.iloc[-1]):.2f}%"
                     "(口径断点规则:单月 |MoM|>10% 判断点,其后 12 个月同比丢弃——2017-01 +18% 实证)")
        if len(tsf) and len(money) and "tsf_inc" in tsf.columns:
            pulse = mcm.tsf_pulse_series(tsf["tsf_inc"], money["m2_amt"])
            both3 = pd.concat([yoy.rename("yoy"), pulse.rename("pulse")], axis=1, join="inner").dropna()
            if len(both3) > 24:
                corr = float(both3["yoy"].corr(both3["pulse"]))
                lines.append(f"- 形态对照(全重叠): 重叠 {len(both3)} 月 · 相关系数 {corr:.3f}"
                             "(两口径水平不可比,读相关与拐点)")
                for since, tag in (("2018-01-01", "月度连续段"), ("2020-01-01", "近年")):
                    sub = both3[both3.index >= since]
                    if len(sub) > 12:
                        lines.append(f"- 形态对照({tag} ≥{since}): n={len(sub)} · 相关系数 "
                                     f"{float(sub['yoy'].corr(sub['pulse'])):.3f}")
                tail = both3.tail(24)
                snap = " · ".join(f"{m[5:7]}月 {a:.1f}/{b:.1f}" for m, a, b in
                                  zip(tail.index, tail["yoy"], tail["pulse"]))
                lines.append(f"- 近24月快照(存量同比%/脉冲代理%): {snap}")
    lines += ["", "> 放行判据(升级类): 无 PASS/FAIL——快照留档。要点:①月度连续段相关 ≥0.9(代理与真口径"
              "近年形态一致,水平差 ~2pp 为代理已知局限);②存量自洽相对差中位 <10%(官方口径调整小幅);"
              "③口径断点已按规则丢弃(2017-01)。三点齐即投入看板。"]
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    out = RECON_DIR / "tushare_tsf_stock.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[written] {out}")
    return out


def recon_wsr(_args=None) -> Path:
    """批次2.3 交易所仓单扩腿: 升级类快照对照——①CZCE 三品种新口径(总计) vs DB 旧存量
    (应 ≈3×,小计/总计高估实证);②fut_wsr 八品种单日快照(仓库和/单位/双段同名行统计);
    ③SHFE 侧 vol=pre_vol+vol_chg 恒等自洽。"""
    import sqlite3
    from stockagent.config import get_config
    cfg = get_config()
    conn = sqlite3.connect(cfg.db_path)
    lines = ["# tushare 批次2.3 交易所仓单 (升级类: 旧口径存档+新口径并排快照对照)", "",
             "- 腿A CZCE(FG/SA/UR): akshare 郑商所日报,聚合从「全列求和」改「总计行」——旧存量应 ≈3×",
             "- 腿B WSR(CU/AL/ZN/RB/AU/AG/SC/LC): tushare fut_wsr 仓库和(SHFE 含完税+保税双段)",
             "- CZCE 品种不吃 fut_wsr(混入升贴水/有效预报错位行,FG 实证 sum=真仓单+升贴水)", ""]
    # ① 最近一个有 DB 存量的周三: czce 实拉 vs DB
    db = pd.read_sql_query(
        "SELECT variety,date,volume FROM commodity_inventory WHERE source='czce' "
        "ORDER BY date DESC LIMIT 30", conn).drop_duplicates("variety")
    if len(db):
        d_iso = str(db["date"].max())
        try:
            fresh = fetcher.fetch_czce_receipts(d_iso.replace("-", ""))
            for _, r in db.iterrows():
                m = fresh[fresh["variety"] == r["variety"]]
                new_v = float(m["volume"].iloc[0]) if len(m) else float("nan")
                ratio = float(r["volume"]) / new_v if new_v else float("nan")
                lines.append(f"- CZCE {r['variety']} @ {d_iso}: DB(旧全列和)={r['volume']:.0f} → "
                             f"新(总计)={new_v:.0f} · 比值 {ratio:.2f}(预期 ≈3.0)")
        except Exception as e:  # noqa: BLE001
            lines.append(f"- CZCE 实拉失败: {str(e)[:90]}")
    else:
        lines.append("- CZCE: DB 无 czce 存量(先 backfill_commodity.py --inv)")
    conn.close()
    # ② fut_wsr 单日快照
    try:
        from stockagent.data import tushare_client as tc
        df = tc.query_paged("fut_wsr", page_size=800, trade_date="20260911").drop_duplicates()
        sub = df[df["symbol"].isin(fetcher.WSR_INVENTORY_SYMBOLS)]
        # 恒等自洽只在 pre_vol 在的行核(当日接口 pre_vol 多为 NaN,非数据错)
        chk = sub.dropna(subset=["pre_vol", "vol_chg", "vol"])
        dev = (pd.to_numeric(chk["pre_vol"]) + pd.to_numeric(chk["vol_chg"])
               - pd.to_numeric(chk["vol"])).abs().max() if len(chk) else 0.0
        lines.append(f"- fut_wsr@20260911: 总行 {len(df)}(去重) · WSR 八品种 {len(sub)} 行 · "
                     f"vol-(pre_vol+vol_chg) 最大偏差 {float(dev):.1f}"
                     f"(可核行 {len(chk)},恒等自洽)")
        for sym, g in sub.groupby("symbol"):
            dup = int(g.duplicated(["warehouse"]).sum())
            lines.append(f"  {sym}: {len(g)} 仓库 · vol和 {float(pd.to_numeric(g['vol']).sum()):,.0f} "
                         f"{g['unit'].iloc[0]} · 同名双行 {dup}(完税+保税)")
    except Exception as e:  # noqa: BLE001
        lines.append(f"- fut_wsr 快照失败: {str(e)[:90]}")
    lines += ["", "> 放行判据(升级类): 无 PASS/FAIL——①比值≈3 确认旧口径高估已修;②恒等偏差=0、"
              "同名双行集中在 SHFE 品种即入板(CZCE 品种已在腿A隔离)。"]
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    out = RECON_DIR / "tushare_wsr.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[written] {out}")
    return out


def recon_roll(_args=None) -> Path:
    """批次2.4 展期口径: 升级类快照对照——逐品种换月次数/跳空幅度(raw vs adjusted 换月日收益差)/
    回退日计数,量化「主连拼接未复权」对 event-study 前向收益的污染消除。"""
    from stockagent.commodity import panel as cpanel
    from stockagent.config import get_config
    from stockagent.data.store import Store
    cfg = get_config()
    st = Store(cfg.db_path)
    lines = ["# tushare 批次2.4 展期口径 (升级类: 换月日 raw vs adjusted 快照对照)", "",
             "- adjusted=换月日收益按旧合约 close 计(fundamentals.roll_adjusted_series);",
             "- raw=主连拼接原生收益(新约(t)/旧约(t−1),含跳空);差=被消除的展期跳空。", ""]
    n_var = 0
    for variety, code in fetcher.COMMODITY_CODES.items():
        if variety in fetcher.TS_COMMODITY_EXCLUDE:
            continue
        px = st.get_commodity_series(variety)
        adj, n_fb = cpanel.roll_adjusted_from_store(st, variety, code)
        if px is None or not len(px) or adj is None or not len(adj):
            lines.append(f"- {variety}({code}): SKIP — 无主连或无映射数据")
            continue
        n_var += 1
        suf = fetcher.COMMODITY_TS_SUFFIX.get(code)
        mapping = st.get_fut_mapping(f"{code}.{suf}")
        rolls = (mapping != mapping.shift(1)).fillna(False)
        n_rolls = int(rolls.sum())
        raw_ret = px / px.shift(1) - 1.0
        adj_ret = adj / adj.shift(1) - 1.0
        both = pd.concat([raw_ret.rename("raw"), adj_ret.rename("adj")], axis=1).dropna()
        diff_days = both[(both["raw"] - both["adj"]).abs() > 1e-9]
        if len(diff_days):
            d = (diff_days["raw"] - diff_days["adj"]) * 100
            lines.append(f"- {variety}({code}.{suf}): 映射 {len(mapping)} 日 · 换月 {n_rolls} 次 · "
                         f"修正日 {len(diff_days)} · 跳空差中位 {float(d.abs().median()):+.2f}pp / "
                         f"最大 {float(d.abs().max()):+.2f}pp · 回退日 {n_fb or 0}")
        else:
            lines.append(f"- {variety}({code}.{suf}): 映射 {len(mapping)} 日 · 换月 {n_rolls} 次 · 无修正差")
    lines += ["", f"合计 {n_var} 品种有映射(碳酸锂/LPG 无主连映射);> 放行判据(升级类): 回退日占比小"
              "(<10% 换月日)、跳空差量级=换月价差(品种自身 contango/backwardation 决定)即可入 event-study。"]
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    out = RECON_DIR / "tushare_roll.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[written] {out}")
    return out


SUBS = {"valuation": recon_valuation, "macro": recon_macro, "nav": recon_nav,
        "index": recon_index, "dividend": recon_dividend, "commodity": recon_commodity,
        "margin": recon_margin, "turnover": recon_turnover, "tsf": recon_tsf_stock,
        "wsr": recon_wsr, "roll": recon_roll}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in SUBS:
        print(__doc__)
        sys.exit(2)
    SUBS[sys.argv[1]](sys.argv[2:] or None)


if __name__ == "__main__":
    main()
