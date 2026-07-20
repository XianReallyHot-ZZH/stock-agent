"""ETF 行业研究报告 CLI — backfill data + score + render interactive HTML.

Pure research output (read-only, does NOT touch the trading engine).

Usage:
  # 1. one-time historical backfill (NAV + industry PE + SZSE shares)
  python scripts/research_report.py --backfill all --start 2021-01-01

  # 2. generate the dashboard (evaluates at latest available bar)
  python scripts/research_report.py
  python scripts/research_report.py --as-of 2026-06-30 --output data/research_report.html
  python scripts/research_report.py --no-llm
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from stockagent.config import get_config
from stockagent.data import Store, DataManager
from stockagent.research import scoring as rs
from stockagent.research import commentary as rc
from stockagent.research import report as rep
from stockagent.utils.logging_setup import setup_logging


def _series_to(df: pd.DataFrame, col: str, as_of: str | None):
    """Slice a stored DataFrame up to as_of and return the column Series (or None)."""
    if df is None or len(df) == 0 or col not in df.columns:
        return None
    s = df[col]
    if as_of:
        s = s[s.index <= as_of]
    s = pd.to_numeric(s, errors="coerce").dropna()
    return s if len(s) else None


def _latest_value(df: pd.DataFrame | None, cols: list[str]) -> float | None:
    """Last non-NaN value across priority-ordered cols (e.g. unit_nav before acc_nav)."""
    if df is None or len(df) == 0:
        return None
    for c in cols:
        if c in df.columns:
            s = pd.to_numeric(df[c], errors="coerce").dropna()
            if len(s):
                return float(s.iloc[-1])
    return None


def do_backfill(dm: DataManager, kind: str, start: str, end: str, step: int, sleep: float,
                source: str = "all") -> None:
    if kind in ("nav", "all"):
        print(f"  backfill NAV {start}..{end} ...")
        dm.backfill_etf_nav(start, end)
    if kind in ("pe", "all"):
        print(f"  backfill industry PE {start}..{end} step={step} sleep={sleep} ...")
        dm.backfill_industry_pe(start, end, step_days=step, sleep=sleep)
    if kind in ("scale", "all"):
        print(f"  backfill shares source={source} {start}..{end} step={step} ...")
        dm.backfill_etf_scale(start, end, step_days=step, source=source)


def build_snapshots(store: Store, cfg, symbols: list[str], as_of: str | None):
    from stockagent.data import fetcher
    from stockagent.research import earnings as ern
    from stockagent.tracker import classifier as clf
    meta = cfg.symbol_meta()
    snapshots: dict[str, dict] = {}
    series_map: dict[str, dict] = {}
    for sym in symbols:
        m = meta.get(sym, {})
        csrc = m.get("csrc_industry")
        has_val = bool(csrc)

        price_df = store.get_series(sym, end=as_of)
        close = _series_to(price_df, "close", None)  # already sliced by end=
        shares_df = store.get_scale_series(sym, end=as_of)
        shares = _series_to(shares_df, "shares", None)
        nav_df = store.get_nav_series(sym, end=as_of)
        pe_df = store.get_industry_pe_series(csrc, end=as_of) if has_val else None
        pe = _series_to(pe_df, "pe", None)

        # Phase 1-A: 按 style 分流(价值/成长/周期各走各的估值解读);value 额外算股息率
        style_main, _ = clf.classify(sym, cfg)
        div_df = store.get_etf_dividend_series(sym) if style_main == "value" else None
        snap = rs.analyze_etf(close, shares, pe, cfg.params, has_valuation=has_val,
                              style=style_main or "growth", dividend_df=div_df)
        snap["style"] = style_main or "growth"
        snap["name"] = m.get("name", sym)
        snap["csrc_industry"] = csrc or "(宽基/无单一行业)"

        # 当下规模(亿)=最新份额×最新单位净值; 近5日均成交额(亿) — 流动性参考, 不进性价比
        amount = _series_to(price_df, "amount", None)
        snap["turnover_5d_yi"] = (round(float(amount.tail(5).mean()) / 1e8, 2)
                                  if amount is not None and len(amount) else None)
        sh_now = _latest_value(shares_df, ["shares"])
        nav_now = _latest_value(nav_df, ["unit_nav", "acc_nav"])
        snap["aum_yi"] = round(sh_now * nav_now / 1e8, 2) if (sh_now and nav_now) else None

        # 业绩预期 (informational — does NOT enter composite). Precomputed by update_etf_earnings.
        earn = store.get_etf_earnings(sym)
        if earn:
            escore, elabel = ern.earnings_score(earn, cfg.params)
            snap["earnings_label"] = elabel
            snap["earnings_score"] = escore
            snap["earnings_yoy"] = earn["weighted_yoy"]
            snap["earnings_bull"] = earn["bull_ratio"]
            snap["earnings_bear"] = earn["bear_ratio"]
            snap["earnings_cov"] = earn["coverage"]
            snap["earnings_period"] = earn["report_period"]

        snapshots[sym] = snap
        series_map[sym] = {"close": close, "shares": shares_df, "nav": nav_df, "pe": pe_df}

    # ETFs with no share history (e.g. 515880 absent from fund_etf_scale_sse) →
    # fetch current spot shares once (batched) so the chart can draw a reference level.
    missing = [s for s in symbols if (series_map[s].get("shares") is None
                                      or len(series_map[s]["shares"]) == 0)]
    if missing:
        try:
            spot = fetcher.fetch_etf_spot_shares(missing)
            for s in missing:
                if s in spot:
                    series_map[s]["current_shares"] = spot[s]
                    # 当下规模回退: 用 spot 份额 × 最新净值
                    nav_now = _latest_value(series_map[s].get("nav"), ["unit_nav", "acc_nav"])
                    snapshots[s]["aum_yi"] = round(spot[s] * nav_now / 1e8, 2) if nav_now else None
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning("spot shares fetch failed: %s", str(e)[:100])
    return snapshots, series_map, meta


def main():
    ap = argparse.ArgumentParser(description="ETF 行业研究 dashboard (read-only)")
    ap.add_argument("--backfill", choices=("nav", "pe", "scale", "earnings", "all"), default=None,
                    help="run historical backfill instead of rendering")
    ap.add_argument("--period", default=None,
                    help="earnings backfill report period YYYYMMDD (default: latest complete FY)")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--step", type=int, default=1, help="backfill sampling step (days); 5=weekly, 30=monthly")
    ap.add_argument("--sleep", type=float, default=1.5,
                    help="seconds between backfill calls (raise to 8 for cninfo PE throttle)")
    ap.add_argument("--source", choices=("all", "sse", "szse"), default="all",
                    help="scale backfill source: 'szse' fills only the deep-market gap")
    ap.add_argument("--as-of", default=None, help="evaluation date YYYY-MM-DD (default: latest)")
    ap.add_argument("--symbols", nargs="*", default=None, help="override v1 symbol list")
    ap.add_argument("--output", default="data/research_report.html")
    ap.add_argument("--no-llm", action="store_true",
                    help="skip ALL LLM (pool summary + per-ETF = rule template)")
    ap.add_argument("--llm-per-etf", action="store_true",
                    help="also do per-ETF LLM commentary (27 calls, slow); default is pool summary only")
    args = ap.parse_args()
    setup_logging()

    cfg = get_config()
    store = Store(cfg.db_path)
    dm = DataManager(store=store, config=cfg)
    end = args.end or datetime.now().strftime("%Y-%m-%d")

    if args.backfill:
        if args.backfill in ("earnings", "all"):
            n = dm.update_etf_earnings(report_period=args.period)
            print(f"  earnings backfill: {n} ETFs updated")
            if args.backfill == "earnings":
                return
        do_backfill(dm, args.backfill, args.start, end, args.step, args.sleep, args.source)
        return

    symbols = args.symbols or cfg.rotation_symbols()  # all rotation ETFs (v1_symbols was the 6-ETF pilot)
    as_of = args.as_of
    snapshots, series_map, meta = build_snapshots(store, cfg, symbols, as_of)

    # resolve as_of for the header (latest close date across symbols if not given)
    if as_of is None:
        dates = []
        for sm in series_map.values():
            for k in ("close", "shares", "nav"):
                s = sm.get(k)
                if s is not None and hasattr(s, "index") and len(s.index):
                    dates.append(str(s.index[-1]))
        as_of = max(dates) if dates else end

    # LLM usage: pool summary is the default value-add (1 call); per-ETF LLM is opt-in (27 calls).
    use_llm = not args.no_llm
    pool_sum = rc.pool_summary(snapshots, meta, use_llm=use_llm)
    commentaries = rc.commentary(snapshots, meta, use_llm=(use_llm and args.llm_per_etf))

    if use_llm and rc.llm_client.llm_available():
        parts = ["全池格局LLM"]
        if args.llm_per_etf:
            parts.append("逐只LLM")
        src = "+".join(parts)
    else:
        src = "规则模板"
    html = rep.render(snapshots, series_map, meta, commentaries, as_of=as_of,
                      signal_note=f"解读源：{src}", pool_summary=pool_sum,
                      ma_period=int(cfg.params["research"]["ma_period"]))
    out = rep.write_html(html, args.output)

    # console summary — ranked ETFs first, then data-insufficient ones (excluded from ranking)
    def _comp(sn):
        c = sn.get("composite")
        return c if c == c else -1

    print(f"\n🏭 ETF 行业研究看板 -> {out}")
    ranked = [(s, sn) for s, sn in snapshots.items() if sn.get("data_sufficient", True)]
    excluded = [(s, sn) for s, sn in snapshots.items() if not sn.get("data_sufficient", True)]
    print(f"   as_of={as_of}  参与排名 {len(ranked)}/{len(snapshots)}  解读={src}\n")
    for sym, snap in sorted(ranked, key=lambda kv: _comp(kv[1]), reverse=True):
        comp = snap.get("composite")
        comp_s = f"{comp:.0f}" if comp == comp else "NA"
        pe = snap.get("pe_percentile")
        pe_s = f"{pe*100:.0f}%" if pe == pe else "NA"
        print(f"   {snap['name']:12} {sym}  性价比 {comp_s:>3}  PE分位 {pe_s:>4}  相位 {snap.get('chip_phase','')}")
    if excluded:
        names = "、".join(f"{sn['name']}({s})" for s, sn in excluded)
        print(f"\n   ⚠ 数据不足未参与排名({len(excluded)}): {names}")
    print(f"\n   open: file:///{out.resolve()}")


if __name__ == "__main__":
    main()
