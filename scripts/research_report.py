"""ETF 行业研究 · 择时跟踪看板 CLI — backfill data + 跟踪 + render interactive HTML.

定位：从「性价比评估」转定位为「ETF 择时跟踪」——纯跟踪、不标买卖点。每个 ETF 跟踪
① 净值-MA60 偏离度（分位 + 第几极值）② 份额-净值剪刀差分化。读出视图（read-only，
不碰交易引擎）。告警推送已停用（仅可视化）。

Usage:
  # 1. one-time historical backfill (NAV + industry PE + SZSE shares)
  python scripts/research_report.py --backfill all --start 2021-01-01

  # 2. generate the dashboard (evaluates at latest available bar)
  python scripts/research_report.py
  python scripts/research_report.py --as-of 2026-06-30 --output data/research_report.html
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
from stockagent.research import report as rep
from stockagent.research import timing as rtm
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
    rp = cfg.params["research"]
    ma_period = int(rp["ma_period"])
    # 剪刀差参数可选调（默认窗口 20-120、地板 ±5%）；params.yaml 未配则用默认
    scissor_window = tuple(rp.get("scissor_window", [20, 120]))
    scissor_floor = float(rp.get("scissor_floor", 0.05))
    # 筹码方向（机构行为·代理）：近端加权投票窗口 + 死区 + 阈值 → 偏离度×筹码四象限提醒
    chip_windows = tuple(rp.get("chip_windows", [5, 10, 20, 30, 60]))
    chip_deadzone = float(rp.get("chip_deadzone", 0.01))
    chip_vote_threshold = int(rp.get("chip_vote_threshold", 2))
    snapshots: dict[str, dict] = {}
    series_map: dict[str, dict] = {}
    for sym in symbols:
        m = meta.get(sym, {})
        csrc = m.get("csrc_industry")
        style_main, _ = clf.classify(sym, cfg)

        price_df = store.get_series(sym, end=as_of)
        shares_df = store.get_scale_series(sym, end=as_of)
        nav_df = store.get_nav_series(sym, end=as_of)

        # 择时跟踪快照：净值-MA 偏离度（分位 + 第几极值）+ 份额/净值剪刀差 + 筹码方向
        snap = rtm.timing_snapshot(nav_df, shares_df, ma_period=ma_period,
                                   scissor_window=scissor_window, scissor_floor=scissor_floor,
                                   chip_windows=chip_windows, chip_deadzone=chip_deadzone,
                                   chip_vote_threshold=chip_vote_threshold)
        snap["style"] = style_main or "growth"
        snap["name"] = m.get("name", sym)
        snap["csrc_industry"] = csrc or "(宽基/无单一行业)"

        # 当下规模(亿)=最新份额×最新净值; 近5日均成交额(亿) — 流动性参考
        amount = _series_to(price_df, "amount", None)
        snap["turnover_5d_yi"] = (round(float(amount.tail(5).mean()) / 1e8, 2)
                                  if amount is not None and len(amount) else None)
        sh_now = _latest_value(shares_df, ["shares"])
        nav_now = _latest_value(nav_df, ["unit_nav", "acc_nav"])
        snap["aum_yi"] = round(sh_now * nav_now / 1e8, 2) if (sh_now and nav_now) else None

        # 业绩预期 (informational). Precomputed by update_etf_earnings.
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
        series_map[sym] = {"shares": shares_df, "nav": nav_df}

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
                    nav_now = _latest_value(series_map[s].get("nav"), ["unit_nav", "acc_nav"])
                    snapshots[s]["aum_yi"] = round(spot[s] * nav_now / 1e8, 2) if nav_now else None
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning("spot shares fetch failed: %s", str(e)[:100])
    return snapshots, series_map, meta


def build_flow_payload(cfg, series_map: dict, meta: dict, symbols: list[str],
                       as_of: str | None) -> dict | None:
    """组装「板块资金流向」payload（research/flow.py 纯函数）。

    组聚合用 etf_pool.yaml 的 group 字段（9 行业组，插入序=pool 顺序即规范序）；
    旧池无 group → None（render 端 section 静默省略，优雅降级）。阈值读
    params.yaml research.flow，缺省走 flow.py 内置默认（旧 params 不崩）。
    """
    from stockagent.research import flow as rfl
    groups: dict[str, list[str]] = {}
    for sym in symbols:
        g = meta.get(sym, {}).get("group")
        if g:
            groups.setdefault(g, []).append(sym)
    if not groups:
        return None
    fp = cfg.params["research"].get("flow", {}) or {}
    panel, excluded = rfl.flow_panel(series_map)
    if not panel:
        return None
    window = int(fp.get("window", 20))
    # 线图窗口切换（5/20/60 日）：预计算各窗口滚动；tile/state 仍用 window 主口径
    windows = sorted({int(w) for w in fp.get("windows", [5, 20, 60])} | {window})
    rolls = {w: rfl.group_rolling_flow(panel, groups, window=w) for w in windows}
    roll = rolls[window]
    state = rfl.pool_flow_state(
        roll, window=window,
        in_yi=float(fp.get("state_in_yi", 10.0)), out_yi=float(fp.get("state_out_yi", -10.0)),
        gross_floor_yi=float(fp.get("gross_floor_yi", 15.0)),
        breadth_floor_yi=float(fp.get("breadth_floor_yi", 1.0)),
        breadth_min=float(fp.get("breadth_min", 0.5)))
    monthly = rfl.group_monthly_matrix(panel, groups,
                                       start_month=str(fp.get("month_start", "2021-01")))
    return {
        "window": window, "state": state, "group_roll": roll, "rolls": rolls,
        "monthly": monthly,
        "aum": rfl.group_aum_yi(panel, groups),
        "aum_series": rfl.group_aum_series(panel, groups),   # % 态逐日分母
        "groups": list(groups),
        "members": {g: [(s, meta.get(s, {}).get("name", s)) for s in syms]
                    for g, syms in groups.items()},   # 看板展示组构成（chips 悬停+明细）
        "excluded": [(s, meta.get(s, {}).get("name", s)) for s in excluded],
        "as_of": as_of,
    }


def main():
    ap = argparse.ArgumentParser(description="ETF 行业研究 · 择时跟踪看板 (read-only)")
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
    ap.add_argument("--push-alerts", action="store_true",
                    help="(已停用) 本看板仅可视化，不再推送告警到微信；保留 flag 仅为向后兼容")
    ap.add_argument("--no-llm", "--llm-per-etf", dest="legacy_llm", action="store_true",
                    help="(已停用) LLM 解读随性价比模型一并退役；保留 flag 仅为向后兼容")
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

    symbols = args.symbols or cfg.rotation_symbols()
    as_of = args.as_of
    snapshots, series_map, meta = build_snapshots(store, cfg, symbols, as_of)

    # resolve as_of for the header (latest share/nav date across symbols if not given)
    if as_of is None:
        dates = []
        for sm in series_map.values():
            for k in ("shares", "nav"):
                s = sm.get(k)
                if s is not None and hasattr(s, "index") and len(s.index):
                    dates.append(str(s.index[-1]))
        as_of = max(dates) if dates else end

    flow_payload = build_flow_payload(cfg, series_map, meta, symbols, as_of)
    html = rep.render(snapshots, series_map, meta, as_of=as_of,
                      signal_note="纯跟踪·无LLM解读",
                      ma_period=int(cfg.params["research"]["ma_period"]),
                      pinned=list(cfg.params["research"].get("pinned_etfs", [])),
                      flow=flow_payload)
    out = rep.write_html(html, args.output)

    if args.push_alerts:
        print("  ℹ 告警推送已停用（本看板仅可视化，不发微信）")

    # console summary — ranked by 偏离度极值 (|nav_dev_pct − 0.5|), then data-insufficient ones
    def _ext(sn):
        p = sn.get("nav_dev_pct")
        return abs(p - 0.5) if p == p else -1.0

    print(f"\n🏭 ETF 择时跟踪看板 -> {out}")
    ranked = [(s, sn) for s, sn in snapshots.items() if sn.get("data_sufficient", True)]
    excluded = [(s, sn) for s, sn in snapshots.items() if not sn.get("data_sufficient", True)]
    print(f"   as_of={as_of}  参与排名 {len(ranked)}/{len(snapshots)}\n")
    for sym, snap in sorted(ranked, key=lambda kv: _ext(kv[1]), reverse=True):
        cur, pct = snap.get("nav_dev_cur"), snap.get("nav_dev_pct")
        cur_s = f"{cur:+.1%}" if cur == cur else "NA"
        pct_s = f"{pct:.0%}" if pct == pct else "NA"
        sc = snap.get("scissor") or {}
        sc_s = ""
        if sc.get("detected"):
            arrow = "份↑净↓" if sc.get("direction") == "share_up_nav_down" else "份↓净↑"
            sc_s = f"  剪刀差 {arrow} 份{sc['share_drift']:+.0%}/净{sc['nav_drift']:+.0%}"
        print(f"   {snap['name']:12} {sym}  偏离 {cur_s:>6}  分位 {pct_s:>4}{sc_s}")
    if excluded:
        names = "、".join(f"{sn['name']}({s})" for s, sn in excluded)
        print(f"\n   ⚠ NAV 历史不足未参与排名({len(excluded)}): {names}")
    if flow_payload and flow_payload["state"].get("label_key") != "insufficient":
        st = flow_payload["state"]
        print(f"   💰 板块资金流向[{st['label']}] 近{flow_payload['window']}日全池净流入 "
              f"{st['pool_net_yi']:+.0f}亿 · 毛额 {st['pool_gross_yi']:.0f}亿 · "
              f"轮动强度 {st['intensity']:.2f}")
    print(f"\n   open: file:///{out.resolve()}")


if __name__ == "__main__":
    main()
