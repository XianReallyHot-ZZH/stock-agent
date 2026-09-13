"""高业绩池验证器(V8)——状态机回放 vs 七宽基, 消融三臂, 结论写 meta 活注入看板。

检验 claim(docs/CLAIMS_LEDGER.md 同步登记): 「季报池 8 年 70% 场合跑赢所有宽基指数」
(重远投资观·图片6)。本仓库纪律: 未过 event-study 礼遇的策略只是观察项——结论照实注入,
可能显示无 edge,那是诚实。小样本注记: 8 期 × 3 环 ≈ 20 个窗口。

Usage:
  python scripts/validate_high_earnings_pool.py                # 全量回放 + 渲染 + meta
  python scripts/validate_high_earnings_pool.py --periods 6    # 最近 6 期
  python scripts/validate_high_earnings_pool.py --no-meta      # 只出 HTML 不写 meta(调试)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from stockagent.config import get_config
from stockagent.data.store import Store
from stockagent.pool import calendar as cal
from stockagent.pool import study
from stockagent.pool import valuation as vl
from stockagent.pool.report import _e  # 简单转义复用(fat renderer 同款)

ROOT = Path(__file__).resolve().parent.parent
ARMS = ["full", "no_valuation", "no_risk", "mktcap_on"]
ARM_LABELS = {"full": "全门", "no_valuation": "去估值门", "no_risk": "去风险旗",
              "mktcap_on": "全门+市值P80"}


def _load_frames(store: Store, periods: list[str]) -> dict[str, dict[str, pd.DataFrame]]:
    frames: dict[str, dict[str, pd.DataFrame]] = {}
    for p in periods:
        frames[p] = {
            "forecast": store.get_stock_forecast_period(p),
            "express": store.get_stock_express_period(p),
            "actual": store.get_stock_report_period(p),
        }
    return frames


def _shares_map(store: Store) -> dict[str, float | None]:
    """现市值/现价 → 反推股本(回放全程恒定,股本缓变近似)。"""
    spot = store.latest_stock_spot()
    out: dict[str, float | None] = {}
    if len(spot) == 0 or "mktcap" not in spot.columns:
        return out
    for code, r in spot.iterrows():
        out[str(code)] = vl.implied_shares(r.get("mktcap"), r.get("close"))
    return out


def _price_cache(store: Store):
    """惰性价格缓存(只读落到估值门的股票)。Returns (getter, stats)。"""
    cache: dict[str, pd.DataFrame] = {}
    stats = {"hits": 0, "misses": 0, "missing": set()}

    def getter(code: str):
        if code not in cache:
            df = store.get_series(code)
            cache[code] = df if df is not None and len(df) else pd.DataFrame()
        df = cache[code]
        if len(df):
            stats["hits"] += 1
        else:
            stats["misses"] += 1
            stats["missing"].add(code)
        return cache[code] if len(cache[code]) else None

    return getter, stats


def _index_dfs(store: Store) -> dict[str, pd.Series]:
    out = {}
    for sym in study.INDEX_BASELINES:
        df = store.get_index_daily_series(sym)
        if df is not None and len(df):
            out[sym] = df["close"].astype(float)
    return out


def _windows(periods: list[str], events: list[dict]) -> list[dict]:
    """每期一窗: start=该期首个环事件日, end=下期首个环事件日;末期=正式报截止与今天取早
    (截止未到=数据未满窗,诚实截断)。"""
    first_event: dict[str, str] = {}
    for ev in events:  # 已按日期升序
        p = ev["period"]
        if p not in first_event:
            first_event[p] = ev["date"]
    ordered = sorted(first_event)
    today = date.today().isoformat()
    wins = []
    for i, p in enumerate(ordered):
        start = first_event[p]
        if i + 1 < len(ordered):
            end = first_event[ordered[i + 1]]
        else:
            end = min(cal.formal_deadline(p).isoformat(), today)
        if start < end:
            wins.append({"period": p, "start": start, "end": end})
    return wins


def _conclusion(agg_by_arm: dict, n_win: int) -> str:
    full = agg_by_arm.get("full") or {}
    wr, med = full.get("win_rate_all"), full.get("median_pool")
    parts = []
    if wr is None:
        parts.append(f"高业绩池回放 {n_win} 窗: 无有效对照窗口")
    else:
        parts.append(f"高业绩池回放 {n_win} 窗: 跑赢全部宽基的窗口占比 {wr:.0%}")
    if med is not None:
        parts.append(f"池收益中位 {med:+.1%}")
    for arm in ("no_valuation", "no_risk", "mktcap_on"):
        a = agg_by_arm.get(arm) or {}
        w = a.get("win_rate_all")
        if w is not None:
            parts.append(f"{ARM_LABELS[arm]} {w:.0%}")
    per = full.get("per_index") or {}
    rates = [v["beat_rate"] for v in per.values() if v.get("beat_rate") is not None]
    if rates:
        parts.append(f"最难基准 {min(rates):.0%}")
    parts.append(f"小样本 n={n_win},观察口径,温度计非开关")
    return ";".join(parts)


def _html_report(agg_by_arm: dict, windows_by_arm: dict, price_stats: dict,
                 approx_notes: list[str], took_s: float,
                 concentration: dict | None = None,
                 concentration_wins: dict | None = None,
                 smallcaps: dict | None = None,
                 smallcaps_wins: dict | None = None) -> str:
    idx_syms = study.INDEX_BASELINES
    rows_html = []
    for w in windows_by_arm.get("full") or []:
        p = w.get("pool")
        pool_cell = f"<td><b>{p * 100:+.1f}%</b></td>" if p is not None else "<td>—</td>"
        cells = [f"<td>{_e(w['period'])}</td>", f"<td>{_e(w['start'])}→{_e(w['end'])}</td>",
                 f"<td>{w.get('n_members', 0)}</td>", pool_cell]
        for sym in idx_syms:
            r = (w.get("indices") or {}).get(sym)
            if r is None or p is None:
                cells.append("<td>—</td>")
            else:
                beat = p > r
                cells.append(f"<td class='{'ok' if beat else 'crit'}'>{r * 100:+.1f}%"
                             f"{'✓' if beat else '✗'}</td>")
        rows_html.append("<tr>" + "".join(cells) + "</tr>")

    arm_rows = []
    for arm in ARMS:
        a = agg_by_arm.get(arm) or {}
        wr, med = a.get("win_rate_all"), a.get("median_pool")
        arm_rows.append(
            f"<tr><td>{_e(ARM_LABELS[arm])}</td><td>{a.get('n_windows', 0)}</td>"
            f"<td><b>{('—' if wr is None else f'{wr:.0%}')}</b></td>"
            f"<td>{('—' if med is None else f'{med * 100:+.1f}%')}</td></tr>")

    conc_html = ""
    if concentration:
        rows_c = []
        for size in (100, 25, 20, 15, 10, 5):
            a = concentration.get(size)
            if not a:
                continue
            wr = "—" if a["win_rate_all"] is None else f"{a['win_rate_all']:.0%}"
            med = "—" if a["median_pool"] is None else f"{a['median_pool'] * 100:+.1f}%"
            tag = " <span class='hint'>(对照=主口径)</span>" if size == 100 else ""
            rows_c.append(f"<tr><td>Top-{size}{tag}</td><td>{a['n_windows']}</td>"
                          f"<td><b>{wr}</b></td><td>{med}</td></tr>")
        conc_html = (
            "<h2>集中度扫描(散户真实持有量级 · 全门 · 单股敞口=1/N)</h2>"
            + "<table><tr><th>持仓数</th><th>窗口</th><th>全胜率</th><th>池中位</th></tr>"
            + "".join(rows_c) + "</table>"
            + "<div class='hint'>门判定/进出纪律与 Top-100 主口径完全相同,仅容量不同"
              "(排序分=PEG 升序);零成本;n=37 小样本。Top-5 意味着单股 20% 敞口——"
              "集中度放大的是波动与路径依赖,读数时与仓位纪律(仓位看板⑥)对表。</div>")
    if concentration_wins:
        sizes = [s for s in (100, 25, 20, 15, 10, 5) if s in concentration_wins]
        base = concentration_wins[sizes[0]] if sizes else []
        rows_m = []
        for i, w0 in enumerate(base):
            idxs = w0["indices"] or {}
            avail = [r for r in idxs.values() if r is not None]
            cells = [f"<td>{_e(w0['period'])}</td>",
                     f"<td>{_e(w0['start'])}→{_e(w0['end'])}</td>"]
            for s in sizes:
                wr = concentration_wins[s][i]
                v = wr.get("pool")
                if v is None:
                    cells.append("<td class='dim'>—</td>")
                    continue
                beat = bool(avail) and all(v > r for r in avail)
                cls = "pos" if v > 0 else ("neg" if v < 0 else "")
                mark = "✓" if beat else "✗"
                mcls = "ok" if beat else "crit"
                cells.append(f"<td><span class='{cls}'>{v * 100:+.1f}%</span>"
                             f"<span class='{mcls}'>{mark}</span></td>")
            for sym in study.INDEX_BASELINES:
                r = idxs.get(sym)
                if r is None:
                    cells.append("<td class='dim'>—</td>")
                else:
                    cls = "pos" if r > 0 else ("neg" if r < 0 else "")
                    cells.append(f"<td><span class='{cls}'>{r * 100:+.1f}%</span></td>")
            rows_m.append("<tr>" + "".join(cells) + "</tr>")
        head_m = "".join(f"<th>Top-{s}</th>" for s in sizes)
        head_i = "".join(f"<th>{_e(study.INDEX_LABELS.get(s, s))}</th>"
                         for s in study.INDEX_BASELINES)
        conc_html += ("<details class='conc-detail'><summary>📋 集中度扫描 · 逐期明细"
                      "(点开/收起)</summary><div style='overflow-x:auto'><table "
                      "style='font-size:12px;min-width:900px'>"
                      "<tr><th>报告期</th><th>窗口</th>" + head_m + head_i + "</tr>"
                      + "".join(rows_m) + "</table></div>"
                      "<div class='hint'>✓/✗=该容量当期是否跑赢当窗全部可用宽基(科创50 "
                      "2020-07 前不存在,缺席不计);七列指数=同窗对照;Top-5 单格波动巨大"
                      "(单股 20% 敞口),读单格谨慎。</div></details>")
    small_html = ""
    if smallcaps:
        rows_s = []
        for (pool_k, n_small), a in smallcaps.items():
            wr = "—" if a["win_rate_all"] is None else f"{a['win_rate_all']:.0%}"
            med = "—" if a["median_pool"] is None else f"{a['median_pool'] * 100:+.1f}%"
            rows_s.append(f"<tr><td>Top-{pool_k}(PEG) → 市值最小 {n_small}</td>"
                          f"<td>{a['n_windows']}</td><td><b>{wr}</b></td><td>{med}</td></tr>")
        small_html = (
            "<h2>小市值精选(全门 · Top-K PEG 池内选市值最小 N · 直接检验「中小市值弹性」)</h2>"
            + "<table><tr><th>组合</th><th>窗口</th><th>全胜率</th><th>池中位</th></tr>"
            + "".join(rows_s) + "</table>"
            "<div class='hint'>市值=事件日冻结值(股本×事件日价,与排序分同纪律不逐日重排);"
            "市值缺失者排尾不入选;门/进出纪律与主口径完全相同,仅选择规则不同;零成本;"
            "单股敞口=1/N(Top5=20%)。</div>")
        if smallcaps_wins:
            combos = list(smallcaps_wins.keys())
            base = smallcaps_wins[combos[0]]
            rows_sm = []
            for i, w0 in enumerate(base):
                idxs = w0["indices"] or {}
                avail = [r for r in idxs.values() if r is not None]
                cells = [f"<td>{_e(w0['period'])}</td>",
                         f"<td>{_e(w0['start'])}→{_e(w0['end'])}</td>"]
                for c in combos:
                    v = smallcaps_wins[c][i].get("pool")
                    if v is None:
                        cells.append("<td class='dim'>—</td>")
                        continue
                    beat = bool(avail) and all(v > r for r in avail)
                    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
                    mark = "✓" if beat else "✗"
                    mcls = "ok" if beat else "crit"
                    cells.append(f"<td><span class='{cls}'>{v * 100:+.1f}%</span>"
                                 f"<span class='{mcls}'>{mark}</span></td>")
                for sym in study.INDEX_BASELINES:
                    r = idxs.get(sym)
                    if r is None:
                        cells.append("<td class='dim'>—</td>")
                    else:
                        cls = "pos" if r > 0 else ("neg" if r < 0 else "")
                        cells.append(f"<td><span class='{cls}'>{r * 100:+.1f}%</span></td>")
                rows_sm.append("<tr>" + "".join(cells) + "</tr>")
            head_c = "".join(f"<th>池{k}·小{n}</th>" for (k, n) in combos)
            head_i = "".join(f"<th>{_e(study.INDEX_LABELS.get(s, s))}</th>"
                             for s in study.INDEX_BASELINES)
            small_html += ("<details class='conc-detail'><summary>📋 小市值精选 · 逐期明细"
                           "(点开/收起)</summary><div style='overflow-x:auto'><table "
                           "style='font-size:12px;min-width:1100px'>"
                           "<tr><th>报告期</th><th>窗口</th>" + head_c + head_i + "</tr>"
                           + "".join(rows_sm) + "</table></div>"
                           "<div class='hint'>✓/✗=当期跑赢当窗全部可用宽基;七列指数=同窗对照。</div>"
                           "</details>")
    idx_head = "".join(f"<th>{_e(study.INDEX_LABELS.get(s, s))}</th>" for s in idx_syms)
    notes_html = "".join(f"<li>{_e(n)}</li>" for n in approx_notes)
    missing_n = len(price_stats.get("missing") or ())
    return f"""<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>
<title>高业绩池验证器 · {datetime.now().strftime('%Y-%m-%d')}</title>
<style>body{{font-family:system-ui,'Segoe UI',sans-serif;margin:24px;color:#1c1c1a;background:#f9f9f7}}
table{{border-collapse:collapse;font-size:13px;margin:14px 0;background:#fff}}
th,td{{border:1px solid #e1e0d9;padding:6px 10px;text-align:right}}
th:first-child,td:first-child{{text-align:left}} th{{background:#f3f2ee}}
.ok{{color:#0ca30c}} .crit{{color:#d03b3b}} details.conc-detail{{margin:6px 0}} details.conc-detail summary{{cursor:pointer;font-weight:600;color:#52514e}} .hint{{color:#898781;font-size:12px;line-height:1.8}}
h2{{font-size:16px}} li{{font-size:13px;line-height:1.8;color:#52514e}}</style></head><body>
<h2>高业绩池 · 状态机回放验证</h2>
<table><tr><th>臂</th><th>窗口数</th><th>跑赢全部宽基</th><th>池收益中位</th></tr>{''.join(arm_rows)}</table>
<h2>逐窗口(全门臂)</h2>
<table><tr><th>报告期</th><th>窗口</th><th>成员</th><th>池收益</th>{idx_head}</tr>{''.join(rows_html)}</table>
{conc_html}{small_html}<h2>口径与近似(诚实注记)</h2><ul>{notes_html}</ul>
<div class='hint'>价格缺失股票 {missing_n} 只(未回放,backfill_stock_pool --prices 补) ·
命中 {price_stats.get('hits', 0)} 次 · 耗时 {took_s:.1f}s ·
生成: python scripts/validate_high_earnings_pool.py · 温度计非开关——本报告只陈述回放事实</div>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description="高业绩池验证器(V8.1·Top-100 逐日组合模拟)")
    ap.add_argument("--periods", type=int, default=8, help="回放最近 N 个报告期(默认 8)")
    ap.add_argument("--no-meta", action="store_true", help="不写 meta(调试)")
    args = ap.parse_args()

    cfg = get_config()
    store = Store(cfg.db_path)
    t0 = time.time()

    periods = store.stock_report_periods()[:args.periods]
    if len(periods) < 2:
        print(f"正式报报告期不足({len(periods)} < 2)——先跑 backfill_stock_pool --report")
        return
    print(f"回放期间: {periods[-1]} → {periods[0]}({len(periods)} 期)")

    frames = _load_frames(store, periods)
    events = study.collect_events(frames)
    print(f"三环事件: {len(events)} 条(首 {events[0]['date']} 末 {events[-1]['date']})")

    shares = _shares_map(store)
    np_abs = {}
    report_eps = {}
    for _, r in store.stock_np_abs_all().iterrows():
        code_p = str(r["symbol"])
        np_abs.setdefault(code_p, {})[str(r["report_period"])] = float(r["np_abs"])
        eps_v = r.get("eps")
        if eps_v is not None and not (isinstance(eps_v, float) and eps_v != eps_v):
            report_eps[(code_p, str(r["report_period"]))] = float(eps_v)
    balance = {}
    for p in periods:
        bf = store.get_stock_balance_period(p)
        for code, r in (bf.iterrows() if len(bf) else []):
            balance[(p, str(code))] = {k: r.get(k) for k in
                                       ("cash", "receivables", "total_assets",
                                        "total_liab", "equity", "debt_ratio")}
    idx_dfs = _index_dfs(store)
    print(f"指数基准 {len(idx_dfs)} 个 · 股本反推 {sum(v is not None for v in shares.values())} 只"
          f" · np_abs {len(np_abs)} 只 · 资产负债 {len(balance)} 行")

    hcfg = (cfg.params.get("stock_pool", {}) or {}).get("high_pool", {}) or {}
    price_getter, price_stats = _price_cache(store)
    wins = _windows(periods, events)
    print(f"窗口: {len(wins)} 个")

    spot_df = store.latest_stock_spot()
    mkt_thr = None
    if len(spot_df) and "mktcap" in spot_df.columns:
        caps = pd.to_numeric(spot_df["mktcap"], errors="coerce").dropna()
        if len(caps) > 1000:
            mkt_thr = float(caps.quantile(float(hcfg.get("mktcap_filter_pct", 0.80))))
            print(f"市值门阈值: P80 = {mkt_thr / 1e8:.0f} 亿")

    from stockagent.pool import prices as pr
    bench300 = store.get_index_daily_series("000300")
    calendar = [str(d) for d in bench300.index]
    adjpx = {}

    def _px(code):
        if code in adjpx:
            return adjpx[code]
        df = price_getter(code)
        if df is None or not len(df):
            adjpx[code] = None
            return None
        div = store.get_stock_dividend_series(code)
        d = div if len(div) else None
        adj_c, _ = pr.dividend_adjusted_close(df["close"].astype(float), d)
        adj_o = pr.dividend_adjusted_open(df["open"].astype(float), df["close"].astype(float), d)
        adjpx[code] = (adj_o, adj_c)
        return adjpx[code]

    def make_decide(arm, cfg_arm):
        def decide(ev):
            g = study.gate_at_event(ev, cfg_arm, price_getter, shares, np_abs, balance,
                                    arm=arm, report_eps=report_eps)
            passed = g["passed"] and g["valuation_pass"] and not g["risk_red"]
            score = g.get("peg")
            if score is None:
                score = -(ev.get("np_yoy") or 0.0)
            return passed, score, g.get("cap")
        return decide

    wide_codes = set()
    for arm in ARMS:
        cfg_arm = {**hcfg, "mktcap_threshold": mkt_thr} if arm == "mktcap_on" else hcfg
        for iv in study.replay_membership(events, cfg_arm, price_getter, shares,
                                          np_abs, balance, arm=arm, report_eps=report_eps):
            wide_codes.add(iv["code"])
    for c in sorted(wide_codes):
        _px(c)
    print(f"复权价就绪: {len(wide_codes)} 只 · 日历 {calendar[0]}→{calendar[-1]}({len(calendar)} 日)")

    top_n = int(hcfg.get("top_n", 100))
    agg_by_arm = {}
    windows_by_arm = {}
    for arm in ARMS:
        cfg_arm = {**hcfg, "mktcap_threshold": mkt_thr} if arm == "mktcap_on" else hcfg
        daily, members_by_day = study.daily_pool_returns(
            events, make_decide(arm, cfg_arm), adjpx, calendar, top_n=top_n)
        import math as _m
        wins_ret = []
        for w in wins:
            wdays = [d for d in calendar if w["start"] < d <= w["end"]]
            pool_cum = None
            dr = daily[[d for d in daily.index if w["start"] < d <= w["end"]]]
            if len(dr):
                pool_cum = float(_m.prod(1.0 + dr.values) - 1.0)
            uniq = set()
            for d in wdays:
                uniq.update(members_by_day.get(d, ()))
            idx_ret = {}
            for sym, s in idx_dfs.items():
                idx = s.index.astype(str)
                sub = s[(idx > w["start"]) & (idx <= w["end"])]
                if len(sub) >= 2:
                    idx_ret[sym] = float(sub.iloc[-1] / sub.iloc[0]) - 1.0
            wins_ret.append({"pool": pool_cum, "indices": idx_ret, "n_members": len(uniq),
                             "period": w["period"], "start": w["start"], "end": w["end"]})
        windows_by_arm[arm] = wins_ret
        agg_by_arm[arm] = study.aggregate(wins_ret)
        a = agg_by_arm[arm]
        wr = "—" if a["win_rate_all"] is None else f"{a['win_rate_all']:.0%}"
        med = "—" if a["median_pool"] is None else f"{a['median_pool']:+.1%}"
        print(f"[{ARM_LABELS[arm]}] 窗口 {a['n_windows']} · 全胜率 {wr} · 池中位 {med}")

    # ---- 集中度扫描(用户指令 2026-09-13): 散户真实持有量级 Top-5/10/15/20/25 vs Top-100 对照 ----
    # 门判定与 top_n 无关 → 逐事件 memoize,5 档尺寸零重复判定成本
    decide_full = make_decide("full", hcfg)
    _memo = {}

    def decide_memo(ev):
        k = id(ev)
        if k not in _memo:
            _memo[k] = decide_full(ev)
        return _memo[k]

    import math as _m

    def _window_cums(daily):
        out = []
        for w in wins:
            dr = daily[[d for d in daily.index if w["start"] < d <= w["end"]]]
            pool_cum = float(_m.prod(1.0 + dr.values) - 1.0) if len(dr) else None
            idx_ret = {}
            for sym, s in idx_dfs.items():
                idx = s.index.astype(str)
                sub = s[(idx > w["start"]) & (idx <= w["end"])]
                if len(sub) >= 2:
                    idx_ret[sym] = float(sub.iloc[-1] / sub.iloc[0]) - 1.0
            out.append({"pool": pool_cum, "indices": idx_ret,
                        "period": w["period"], "start": w["start"], "end": w["end"]})
        return out

    concentration = {}
    concentration_wins = {}
    for size in (100, 25, 20, 15, 10, 5):
        daily_c, _mbd = study.daily_pool_returns(events, decide_memo, adjpx, calendar,
                                                 top_n=size)
        concentration_wins[size] = _window_cums(daily_c)
        concentration[size] = study.aggregate(concentration_wins[size])
        a = concentration[size]
        wr = "—" if a["win_rate_all"] is None else f"{a['win_rate_all']:.0%}"
        med = "—" if a["median_pool"] is None else f"{a['median_pool']:+.1%}"
        print(f"[Top-{size}] 窗口 {a['n_windows']} · 全胜率 {wr} · 中位 {med}", flush=True)

    # ---- 小市值精选扫描(用户指令 2026-09-13): Top-K(PEG) 池内选市值最小 N 只 ----
    # 检验陈老师「中小市值弹性更好」: 在同一套门内, 倾斜小市值是否带来增量
    print("小市值精选扫描: Top-100/50 × 最小市值 5/10/15/20/25", flush=True)
    smallcaps = {}
    smallcaps_wins = {}
    for pool_k in (100, 50):
        for n_small in (5, 10, 15, 20, 25):
            daily_s, _ = study.daily_pool_returns(events, decide_memo, adjpx, calendar,
                                                  top_n=n_small, pre_rank_k=pool_k)
            smallcaps_wins[(pool_k, n_small)] = _window_cums(daily_s)
            smallcaps[(pool_k, n_small)] = study.aggregate(smallcaps_wins[(pool_k, n_small)])
            a = smallcaps[(pool_k, n_small)]
            wr = "—" if a["win_rate_all"] is None else f"{a['win_rate_all']:.0%}"
            med = "—" if a["median_pool"] is None else f"{a['median_pool']:+.1%}"
            print(f"  [池{pool_k}·小{n_small}] 窗口 {a['n_windows']} · 全胜率 {wr} · 中位 {med}",
                  flush=True)

    conclusion = _conclusion(agg_by_arm, agg_by_arm["full"]["n_windows"])
    approx_notes = [
        "V8.1 口径(2026-09-13 用户指令): Top-100 容量 + 逐日组合模拟——任意时刻持仓=当前过门"
        "股票按 PEG 升序前 100(排序分冻结于披露时点,与实盘每日重排有差异),事件次日开盘进出"
        "(分红前复权价),等权日内再平衡,停牌当日剔除",
        "零成本假设: 无佣金/滑点/冲击成本(乐观向,读超额打折)",
        "回放统一归母口径(扣非精筛腿是幸存者逐股腿,回放退归母保持全市场一致)",
        "股本=现市值/现价反推→np_abs/eps 反推兜底(股本缓变);历史 universe 无逐期 ST 名单(代码段过滤)",
        "窗口=报告期首个环事件日→下期首个环事件日(滚动覆盖);指数=窗口 close 简单收益",
        "市值P80 臂=全门+总市值≤当前全市场 P80(阈值当前 spot 分位,回放共用)",
        f"集中度扫描与主口径同门同纪律,仅 Top-N 容量不同;小样本 n={len(wins)},对「70% 场合跑赢所有指数」是小样本检验——观察口径,温度计非开关",
    ]
    out_html = ROOT / "data" / "high_earnings_pool_study.html"
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(_html_report(agg_by_arm, windows_by_arm, price_stats,
                                     approx_notes, time.time() - t0,
                                     concentration=concentration,
                                     concentration_wins=concentration_wins,
                                     smallcaps=smallcaps,
                                     smallcaps_wins=smallcaps_wins), encoding="utf-8")
    print(f"-> {out_html}")

    if not args.no_meta:
        store.set_meta("high_earnings_pool_conclusion", conclusion)
        print(f"meta high_earnings_pool_conclusion = {conclusion}")

    missing = price_stats.get("missing") or set()
    if len(missing) > 50:
        sample = ",".join(sorted(missing)[:20])
        print(f"⚠️ {len(missing)} 只过门股票无价格数据(回放缺失)——"
              f"补法: python scripts/backfill_stock_pool.py --prices --codes {sample},…")


if __name__ == "__main__":
    main()
