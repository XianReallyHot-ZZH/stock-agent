"""预告队列验证器(v1)——预告截点队列 · 三臂 vs 沪深300 · 归因,结论写 meta。

研究设计 2026-10-17 grill 定稿(术语见 docs/stock_pool/CONTEXT.md「预告队列/截点/持有窗/
精选臂」): 每季预告法定截止截点(1/31·4/15·7/15·10/15),当季预告环过门队列(**含池内
续留者的再过门**)截点后首交易日开盘买入 → 本季正式报法定截止(非交易日前移)开盘卖出;
三臂 A=队列全体等权 / B1=排序分前K / B2=B1再剔黄旗·扭亏·未精筛;基准只取沪深300;
期数等权聚合;全维度归因+每期输家Top5。v1=在库已结算期(2024Q3→2026Q2 共8期),
**不登记 CLAIMS_LEDGER**——判据已批(≥5/8 期超额>0=方向一致,≤4/8=证伪),v2 跑数前
登记(两段式,Q10c)。

Usage:
  python scripts/validate_forecast_cohort.py              # 全量(在库预告期)+渲染+meta
  python scripts/validate_forecast_cohort.py --no-meta    # 只出 HTML 不写 meta(调试)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from stockagent.config import get_config
from stockagent.data.store import Store
from stockagent.pool import calendar as cal
from stockagent.pool import cohort as ch
from stockagent.pool import cohort_live
from stockagent.pool import prices as pr
from stockagent.pool import study
from stockagent.pool.report import _e

ROOT = Path(__file__).resolve().parent.parent
ARMS_ALL = ("A", "B1", "B2", "C")
ARM_NAMES = {"A": "臂A·队列全体等权", "B1": "臂B1·排序分前K",
             "B2": "臂B2·排序前K再剔黄旗/扭亏/未精筛",
             "C": "臂C·实盘镜面(估值门两轨+两轨排序Top-N)"}


# ---------- I/O 装配层(单期队列装配已抽 cohort_live.period_members——与看板⑥节共用) ----------

def _price_cache(store: Store):
    """惰性原始价缓存(gate 的 PE 用 raw close)。Returns (getter, stats)。"""
    cache: dict[str, pd.DataFrame] = {}
    stats = {"hits": 0, "missing": set()}

    def getter(code: str):
        if code not in cache:
            df = store.get_series(code)
            cache[code] = df if df is not None and len(df) else pd.DataFrame()
        df = cache[code]
        if len(df):
            stats["hits"] += 1
        else:
            stats["missing"].add(code)
        return cache[code] if len(cache[code]) else None

    return getter, stats


def _adj_open_cache(store: Store, price_getter):
    """分红前复权 open 缓存(腿收益用;与 Claim 003 验证器同款管线)。"""
    cache: dict[str, pd.Series | None] = {}

    def getter(code: str):
        if code in cache:
            return cache[code]
        df = price_getter(code)
        if df is None or not len(df):
            cache[code] = None
            return None
        div = store.get_stock_dividend_series(code)
        d = div if div is not None and len(div) else None
        adj_c, _ = pr.dividend_adjusted_close(df["close"].astype(float), d)
        adj_o = pr.dividend_adjusted_open(df["open"].astype(float),
                                          df["close"].astype(float), d)
        cache[code] = adj_o if len(adj_o) else None
        return cache[code]

    return getter


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser(description="预告队列验证器(v1·截点队列三臂回测)")
    ap.add_argument("--no-meta", action="store_true", help="不写 meta(调试)")
    ap.add_argument("--limit", type=int, default=12,
                    help="预告期回看深度(默认 12;v2 全史跑 60)")
    ap.add_argument("--dump-members", action="store_true",
                    help="成员级明细落 data/forecast_cohort_members.csv(复盘/二阶矩分析原料)")
    args = ap.parse_args()

    cfg = get_config()
    store = Store(cfg.db_path)
    t0 = time.time()
    sp_cfg = cfg.params.get("stock_pool") or {}
    hcfg = sp_cfg.get("high_pool") or {}
    ccfg = sp_cfg.get("cohort") or {}
    bench_sym = str(ccfg.get("benchmark", "000300"))
    k_cap = int(ccfg.get("k_cap", 20))
    k_frac = float(ccfg.get("k_frac", 0.25))
    sens_ks = [int(x) for x in (ccfg.get("sens_ks") or [10, 20, 40])]

    # ---- 期清单: 已结算(正式报截止<今天) vs 活样本(截止已过、正式报截止未到) ----
    today = date.today()
    periods_desc = [p for p, _n in store.forecast_period_counts(min_rows=100, limit=args.limit)]
    settled, live = [], []
    for p in periods_desc:
        if ch.forecast_cutoff(p) >= today:
            continue                      # 预告窗还没关,队列未定格
        (live if cal.formal_deadline(p) >= today else settled).append(p)
    settled.sort()
    live.sort()
    if not settled:
        print("无已结算预告期(需 ≥1 个正式报截止已过的期)——先跑 backfill_stock_pool")
        return
    print(f"预告期: 已结算 {len(settled)} 期({settled[0]}→{settled[-1]})"
          + (f" · 活样本 {live}" if live else ""))

    # ---- 预告事件流(仅 forecast 环;collect_events 自带代码段过滤+错标闸门+ST 标记) ----
    frames = {p: {"forecast": store.get_stock_forecast_period(p)} for p in periods_desc}
    st_filter = None
    try:
        nc = store.get_namechange_all()
        if len(nc):
            st_filter = study.make_st_filter(study.st_intervals_from_namechange(nc))
    except Exception:  # noqa: BLE001
        st_filter = None
    events = study.collect_events(frames, st_filter=st_filter)
    print(f"预告事件: {len(events)} 条")

    # ---- 静态原料: 股本/np_abs/eps · 资产负债 as-of · 名字/行业/类型 ----
    spot = store.latest_stock_spot()
    shares: dict[str, float | None] = {}
    names: dict[str, str] = {}
    if len(spot):
        from stockagent.pool import valuation as vl
        for code, r in spot.iterrows():
            shares[str(code)] = vl.implied_shares(r.get("mktcap"), r.get("close"))
            names[str(code)] = str(r.get("name") or code)
    np_abs: dict[str, dict[str, float]] = {}
    report_eps: dict[tuple[str, str], float] = {}
    for _, r in store.stock_np_abs_all().iterrows():
        c, p = str(r["symbol"]), str(r["report_period"])
        np_abs.setdefault(c, {})[p] = float(r["np_abs"])
        eps_v = r.get("eps")
        if eps_v is not None and not (isinstance(eps_v, float) and eps_v != eps_v):
            report_eps[(c, p)] = float(eps_v)
    print(f"股本反推 {sum(v is not None for v in shares.values())} 只 · np_abs {len(np_abs)} 只")

    bal_rows = cohort_live.balance_asof_rows(store)
    print(f"资产负债 as-of 行: {len(bal_rows)} 只")

    ind_df = store.sw_industry_map()
    class_cfg = cfg.sw_industry_class()
    ind_info: dict[str, dict] = {}
    if len(ind_df):
        for code, r in ind_df.iterrows():
            entry = class_cfg.get(str(r.get("industry"))) or {}
            ind_info[str(code)] = {"industry": str(r.get("industry") or "未映射"),
                                   "l1": str(r.get("l1") or "") or "未映射",
                                   "type": entry.get("type"),
                                   "commodity": entry.get("commodity")}

    price_getter, price_stats = _price_cache(store)
    _bf_cache: dict = {}                  # balance_full 逐期惰性缓存(商誉档用)
    adj_open = _adj_open_cache(store, price_getter)
    bench_df = store.get_index_daily_series(bench_sym)

    # ---- 逐期: 队列 → 门 → 腿 → 臂 ----
    period_rows: list[dict] = []          # 逐期×三臂汇总
    member_rows: list[dict] = []          # 已结算期全成员明细(归因/输家)
    live_info: dict | None = None

    for p in settled + live:
        cutoff = ch.forecast_cutoff(p).isoformat()
        deadline = cal.formal_deadline(p).isoformat()
        # 单期队列装配与看板⑥节共用同一份(cohort_live.period_members——回测/实盘同一套判定)
        members, n_pre, n_gw_red = cohort_live.period_members(
            store, hcfg, events, p, cutoff, bal_rows, shares, np_abs, report_eps,
            price_getter, ind_info, _bf_cache)
        mset = {m["code"]: m for m in members}
        k = ch.adaptive_k(len(members), k_cap, k_frac)

        is_live = p in live
        legs: dict[str, float] = {}
        if not is_live:
            for m in members:
                leg = ch.leg_return(adj_open(m["code"]), cutoff, deadline)
                if leg is not None:
                    legs[m["code"]] = leg["ret"]
            ileg = (ch.index_window_return(bench_df, cutoff, deadline, col="open")
                    or ch.index_window_return(bench_df, cutoff, deadline, col="close"))
        else:
            ileg = None

        # 成员明细行(归因): 窗口/分桶/标签
        caps = {m["code"]: m["cap"] for m in members}
        dec = ch.bucket_labels(caps, 10)
        pegs = {m["code"]: m["peg"] for m in members}
        quin = ch.bucket_labels(pegs, 5)
        for m in members:
            info = ind_info.get(m["code"]) or {}
            typ, com = info.get("type"), info.get("commodity")
            if not is_live and m["code"] in legs and ileg is not None:
                ret, iret = legs[m["code"]], ileg["ret"]
                member_rows.append({
                    "period": p, "code": m["code"],
                    "name": names.get(m["code"], m["code"]),
                    "industry": info.get("industry", "未映射"),
                    "l1": info.get("l1", "未映射"), "type": typ or "未分类",
                    "cluster": ("行业未映射" if typ is None
                                else ("商品周期簇" if typ == "cyclic" and com else "非簇")),
                    "ftype": str(m["ftype"] or "未标注"),
                    "扭亏": "扭亏" if m["flags"] else "无",
                    "应收黄旗": "黄" if m["receivable_yellow"] else "无",
                    "未精筛": "未精筛" if m["unrefined"] else "已精筛",
                    "gw_band": m.get("gw_band", "未知"),
                    "cap_decile": dec.get(m["code"]),
                    "peg_quintile": quin.get(m["code"]),
                    "ret": ret, "index_ret": iret, "excess": ret - iret,
                })

        row = {"period": p, "cutoff": cutoff, "deadline": deadline,
               "n_cohort": len(members), "n_pre": n_pre, "n_gw_red": n_gw_red, "k": k,
               "n_valid": len(legs), "index": (ileg or {}).get("ret"),
               "n_b2_eligible": sum(1 for m in members
                                    if not (m["flags"] or m["receivable_yellow"]
                                            or m["unrefined"])),
               "_members": members, "_legs": legs}   # 敏感性扫描复用(不进 HTML)
        if is_live:
            row["arm_A"] = row["arm_B1"] = row["arm_B2"] = row["arm_C"] = None
            row["n_C"] = None
            live_info = {"period": p, "n": len(members), "k": k,
                         "b1": ch.select_arm(members, k)[:k],
                         "names": names, "members": mset}
        else:
            row["arm_A"] = ch.arm_return([legs.get(m["code"]) for m in members])
            b1 = ch.select_arm(members, k)
            b2 = ch.select_arm(members, k, exclude_flagged=True)
            row["arm_B1"] = ch.arm_return([legs.get(c) for c in b1])
            row["arm_B2"] = ch.arm_return([legs.get(c) for c in b2])
            codes_c = ch.select_pool_mirror(
                members, top_n=int(hcfg.get("top_n", 100)),
                peg_max=float(hcfg.get("peg_max", 1.0)),
                pb_pct_max=float(hcfg.get("pb_pct_max", 0.30)))
            row["n_C"] = len(codes_c)
            row["arm_C"] = ch.arm_return([legs.get(c) for c in codes_c])
        period_rows.append(row)
        a_txt = "—"
        if not is_live and row["arm_A"] is not None:
            a_txt = f"{row['arm_A']:+.1%}"
        gw_txt = f"(剔商誉{n_gw_red})" if n_gw_red else ""
        print(f"[{p}] 队列 {n_pre}{gw_txt} · 有效腿 {len(legs)} · K={k}"
              f" · A {a_txt} · 基准 {row['index'] if row['index'] is None else format(row['index'], '+.1%')}",
              flush=True)

    settled_rows = [r for r in period_rows if r["period"] in settled]
    agg = {arm: ch.aggregate_arm([{"period": r["period"], "ret": r[f"arm_{arm}"],
                                   "index_ret": r["index"]} for r in settled_rows])
           for arm in ARMS_ALL}
    for arm in ARMS_ALL:
        a = agg[arm]
        wr = "—" if a["win_rate"] is None else f"{a['wins']}/{a['n_valid']}"
        me = "—" if a["mean_excess"] is None else f"{a['mean_excess']:+.1%}"
        print(f"[{ARM_NAMES[arm]}] 胜率 {wr} · 平均超额 {me}")

    # ---- K 敏感性(B1 固定K,探索性·主循环已缓存成员与腿) ----
    sens = {}
    for kk in sens_ks:
        rows_s = []
        for r in settled_rows:
            members_p = r.get("_members") or []
            b1 = ch.select_arm(members_p, kk)
            legs_p = r.get("_legs") or {}
            ret = ch.arm_return([legs_p.get(c) for c in b1])
            rows_s.append({"period": r["period"], "ret": ret, "index_ret": r["index"]})
        sens[kk] = ch.aggregate_arm(rows_s)
        a = sens[kk]
        print(f"[B1·固定K={kk}] 胜率 {a['wins']}/{a['n_valid']}"
              f" · 平均超额 {'—' if a['mean_excess'] is None else format(a['mean_excess'], '+.1%')}")

    # ---- 归因 ----
    ATTRS = [("industry", "申万 L3 行业"), ("l1", "申万一级行业"), ("cluster", "商品周期簇"),
             ("ftype", "预告类型"), ("扭亏", "扭亏旗"), ("应收黄旗", "应收黄旗"),
             ("未精筛", "扣非精筛覆盖"), ("gw_band", "商誉/净资产档"),
             ("cap_decile", "市值十分位(1最小)"),
             ("peg_quintile", "PEG 五分位(1最低)"), ("type", "行业类型")]
    attr_tables = {label: ch.attribution(member_rows, key) for key, label in ATTRS}

    # 逐期归因(用户复盘裁定 2026-10-17: 混合表有「大期主导 + 期×组混杂」两偏差,关键维度分期呈现;
    # 行业 L3 组太多留混合表,二元/分桶维度逐期)
    PER_DIMS = [("cluster", "商品周期簇·逐期"), ("ftype", "预告类型·逐期"),
                ("扭亏", "扭亏旗·逐期"), ("cap_decile", "市值十分位·逐期"),
                ("peg_quintile", "PEG五分位·逐期")]
    per_attr: dict[str, dict[str, list[dict]]] = {}
    for key, label in PER_DIMS:
        per_attr[label] = {}
        for p in settled:
            rows_p = [r for r in member_rows if r["period"] == p]
            per_attr[label][p] = ch.attribution(rows_p, key, top_n=12)

    # ---- 输家个案(每期 Top5) ----
    losers = []
    for p in settled:
        rows_p = sorted([r for r in member_rows if r["period"] == p and r["excess"] is not None],
                        key=lambda r: r["excess"])[:5]
        losers.extend(rows_p)

    if args.dump_members and member_rows:
        out_csv = ROOT / "data" / "forecast_cohort_members.csv"
        pd.DataFrame(member_rows).to_csv(out_csv, index=False, encoding="utf-8-sig")
        print(f"-> {out_csv} ({len(member_rows)} 行)")

    conclusion = _conclusion(agg, len(settled), settled[0], settled[-1])
    html = _html_report(agg, settled_rows, sens, attr_tables, ATTRS, losers,
                        live_info, price_stats, time.time() - t0,
                        bench_sym, k_cap, k_frac, per_attr=per_attr, settled=settled)
    out_html = ROOT / "data" / "forecast_cohort_study.html"
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html, encoding="utf-8")
    print(f"-> {out_html}")
    if not args.no_meta:
        store.set_meta("forecast_cohort_conclusion", conclusion)
        print(f"meta forecast_cohort_conclusion = {conclusion}")
    missing = price_stats.get("missing") or set()
    if len(missing) > 30:
        print(f"⚠️ {len(missing)} 只队列成员无价格数据——补法: backfill_stock_pool --prices")


def _conclusion(agg: dict, n: int, first: str, last: str) -> str:
    parts = [f"预告队列({n}期·{first[:6]}→{last[:6]}·期数等权·vs沪深300):"]
    for arm in ARMS_ALL:
        a = agg[arm]
        if a["win_rate"] is None:
            parts.append(f"{ARM_NAMES[arm]} 无有效期")
            continue
        parts.append(f"{ARM_NAMES[arm]} {a['wins']}/{a['n_valid']}"
                     f"·平均超额{a['mean_excess']:+.1%}")
    parts.append("Claim004判据=臂C胜率≥60%方向一致/≤50%证伪(2026-10-18终审:方向一致·"
                 "纹理警示 2017-2022恰50%);温度计非开关")
    return ";".join(parts)


def _fmt(v, pct=True):
    if v is None:
        return "—"
    return f"{v:+.1%}" if pct else f"{v:g}"


def _per_attr_html(per_attr: dict, settled: list[str]) -> str:
    """逐期归因矩阵(纯渲染): 行=分组,列=8 期,格=该期组内平均超额%(n);末列=跨期等权
    (先期内均值后跨期平均——大期不主导,修正混合表权重偏差)。"""
    out = []
    for label, by_period in per_attr.items():
        tot: dict[str, int] = {}
        for _p, lst in by_period.items():
            for g in lst:
                tot[g["label"]] = tot.get(g["label"], 0) + g["n"]
        labels = [l for l, _n in sorted(tot.items(), key=lambda kv: (-kv[1], kv[0]))][:12]
        head = "".join(f"<th>{_e(p[2:])}</th>" for p in settled)
        rows = []
        for lab in labels:
            cells = [f"<td>{_e(lab)}</td><td>{tot[lab]}</td>"]
            vals = []
            for p in settled:
                g = next((x for x in by_period.get(p, []) if x["label"] == lab), None)
                if g is None:
                    cells.append("<td class='dim'>·</td>")
                    continue
                cls = "pos" if g["mean_excess"] > 0 else "neg"
                cells.append(f"<td><span class='{cls}'>{g['mean_excess'] * 100:+.1f}</span>"
                             f"<span class='hint'>({g['n']})</span></td>")
                vals.append(g["mean_excess"])
            eq = (sum(vals) / len(vals)) if vals else None
            eq_txt = "—" if eq is None else f"{eq * 100:+.1f}"
            cells.append(f"<td><b>{eq_txt}</b></td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        out.append(
            f"<details class='conc-detail'><summary>📊 {_e(label)}(点开/收起)</summary>"
            f"<div style='overflow-x:auto'><table style='font-size:12px;min-width:900px'>"
            f"<tr><th>分组</th><th>总n</th>{head}<th>跨期等权</th></tr>{''.join(rows)}</table></div>"
            "<div class='hint'>格=该期组内平均超额%(样本n) · ·=该期无此组 · 跨期等权=有样本期先期内"
            "均值再跨期简单平均(修正混合表的大期权重偏差);列头=报告期 YYMMDD</div></details>")
    return "".join(out)


def _html_report(agg, settled_rows, sens, attr_tables, attrs, losers, live_info,
                 price_stats, took_s, bench_sym, k_cap, k_frac,
                 per_attr=None, settled=None) -> str:
    bench_name = study.INDEX_LABELS.get(bench_sym, bench_sym)
    # 四臂总表
    arm_rows = []
    for arm in ARMS_ALL:
        a = agg[arm]
        wr_txt = "—" if a["win_rate"] is None else f"{a['wins']}/{a['n_valid']}"
        cap_cls = "pos" if (a["capital"] or 0) > 0 else "neg"
        arm_rows.append(
            f"<tr><td>{_e(ARM_NAMES[arm])}</td><td>{a['n_valid']}/{a['n_periods']}</td>"
            f"<td><b>{wr_txt}</b></td>"
            f"<td>{_fmt(a['mean_excess'])}</td><td>{_fmt(a['median_excess'])}</td>"
            f"<td class='{cap_cls}'>{_fmt(a['capital'])}</td>"
            f"<td>{_fmt(a['index_capital'])}</td></tr>")
    # 逐期表
    per_rows = []
    for r in settled_rows:
        iret = r["index"]
        q_txt = f"{r.get('n_pre', r['n_cohort'])}"
        if r.get("n_gw_red"):
            q_txt += f"<span class='hint'>(剔商誉{r['n_gw_red']})</span>"
        cells = [f"<td>{_e(r['period'])}</td>", f"<td>{_e(r['cutoff'])}→{_e(r['deadline'])}</td>",
                 f"<td>{q_txt}</td>", f"<td>{r['k']}(剔后{r['n_b2_eligible']})</td>",
                 f"<td>{r['n_valid']}</td>"]
        for arm in ARMS_ALL:
            v = r[f"arm_{arm}"]
            if v is None or iret is None:
                cells.append("<td class='dim'>—</td>")
            else:
                ex = v - iret
                n_c = f"<span class='hint'>({r.get('n_C')})</span>" if arm == "C" else ""
                cells.append(f"<td>{v * 100:+.1f}%{n_c}<span class='{'ok' if ex > 0 else 'crit'}'>"
                             f"{'✓' if ex > 0 else '✗'}</span></td>")
        cells.append(f"<td>{'—' if iret is None else format(iret, '+.1%')}</td>")
        per_rows.append("<tr>" + "".join(cells) + "</tr>")
    # 敏感性
    sens_rows = "".join(
        f"<tr><td>B1 固定 K={kk}</td><td>{a['wins']}/{a['n_valid']}</td>"
        f"<td>{_fmt(a['mean_excess'])}</td><td>{_fmt(a['median_excess'])}</td></tr>"
        for kk, a in (sens or {}).items())
    # 归因
    attr_html = ""
    for key, label in attrs:
        t = attr_tables.get(label) or []
        if not t:
            continue
        rows_a = "".join(
            f"<tr><td>{_e(g['label'])}</td><td>{g['n']}</td>"
            f"<td>{g['wins']}/{g['n']} ({g['win_rate'] * 100:.0f}%)</td>"
            f"<td class='{'pos' if g['mean_excess'] > 0 else 'neg'}'>{g['mean_excess'] * 100:+.1f}%</td>"
            f"<td>{g['mean_ret'] * 100:+.1f}%</td></tr>" for g in t)
        attr_html += (f"<details class='conc-detail'><summary>📊 {_e(label)}(点开/收起)</summary>"
                      f"<table><tr><th>分组</th><th>n</th><th>个股胜率</th>"
                      f"<th>平均超额</th><th>平均收益</th></tr>{rows_a}</table></details>")
    # 输家
    loser_rows = "".join(
        f"<tr><td>{_e(r['period'])}</td><td>{_e(r['code'])}</td><td>{_e(r['name'])}</td>"
        f"<td>{_e(r['industry'])}</td><td>{_e(r['ftype'])}</td>"
        f"<td>{_e(r['扭亏'])}/{_e(r['应收黄旗'])}/{_e(r['未精筛'])}</td>"
        f"<td>{r['ret'] * 100:+.1f}%</td><td>{r['excess'] * 100:+.1f}%</td>"
        f"<td class='dim'>{r['cap_decile'] if r['cap_decile'] is not None else '—'}</td>"
        f"<td class='dim'>{r['peg_quintile'] if r['peg_quintile'] is not None else '—'}</td></tr>"
        for r in losers)
    live_html = ""
    if live_info:
        chips = "".join(
            f"<span class='chip'>{_e(live_info['names'].get(c, c))} {_e(c)}</span>"
            for c in live_info["b1"][:20])
        live_html = (f"<h2>活样本队列({live_info['period']} · 截点已过·正式报截止未到·不入结算)</h2>"
                     f"<div>队列 {live_info['n']} 只 · K={live_info['k']} · B1 名单(截点定格):</div>"
                     f"<div class='chips'>{chips}</div>")
    notes = [
        "队列=该期预告窗(开窗→法定截止)公告且过预告环地板(净利≥50%/扭亏带黄旗)的全体,"
        "含池内续留者的再过门;迟到预告(公告日>截点)不计;硬剔=ST(namechange 当时名称)"
        "+存贷双高(zcfz 代理,as-of 截点已公告的最新期资产负债)",
        "商誉红旗需 sina 逐股精筛腿,历史回放不可得——队列偏宽,B2 不含商誉维度(诚实注记)",
        "持有窗=截点后首交易日开盘买入→本季正式报法定截止(非交易日前移)开盘卖出,同季完成;"
        "零成本;停牌缺价成员剔除计数;分红前复权价",
        f"排序分=PEG(PE_ttm 自算/预告幅度 g,TTM 原料=已公告 np_abs 阶梯);两轨中的 PB 分位轨"
        "在回放中简化为 PEG 单轨(与 Claim 003 验证器同款近似),周期股排序口径差异注记",
        "B2 剔除=扭亏旗+应收黄旗(zcfz as-of 应收÷最新已公告**年报**营收>50%——存量/年度流量"
        "同量纲;v1 首跑曾误用季频累计营收分母致 43% 误旗,已修)+未精筛(扣非 fina_indicator"
        " 截至截点未覆盖最新已公告期);商誉激增黄旗需 sina 序列,回放不可得",
        "臂C·实盘镜面=队列∧估值门(非周期 PEG≤1.0 / 周期 PB 自身历史分位≤30%,样本<120 留白"
        "=不过门——与实屏「分位不可靠不判」同义)∧两轨合并排序∧Top-N 封顶(队列内排位,非全池"
        "排位);PB 阶梯用保守可得日(报表公告日被刷新腿重盖,不可靠)",
        "商誉红旗已入队筛选(2026-10-17 用户指令·tushare balancesheet 补拉 ~1.4k 缺口后): "
        "队列硬剔=商誉/净资产>30%(balance_full as-of 最新可得期,行项 NULL≈0——全历史专项拉取"
        "的读法,与实屏 sina 腿「商誉未精筛」缺口语义不同,注记);双源全无行者保留为「未知」"
        "计数不删(缺数据≠危险,不因覆盖残缺删人)",
        "股本=现市值/现价反推(np_abs/eps 兜底)恒定近似;报表/扣非的 point-in-time 可得日"
        "=min(公告日,该期正式报法定截止)——report/balance 的 announce 疑被刷新腿重盖(MAX 顶到"
        "当天)、fina 为紧凑格式,v1 二跑三处一并修:真公告早用真值、被重盖退法定截止,永不前视"
        "最多保守滞后",
        "一季报截点取 4/15(earnings 模块法定截止口径)——research._DISCLOSURE_WINDOWS 的看板"
        "窗收在 4/30(贴正式报季),差异在此注明;否则一季持有窗退化为零",
        f"聚合=期数等权(每期一票,~2周窗与~3个月窗同权);资金曲线仅展示;不年化;基准只取"
        f"{_e(bench_name)}(同窗开盘口径);K=min({k_cap},{k_frac}×队列)",
        "v1 不登记 CLAIMS_LEDGER、不结算——判据已批(≥5/8 期超额>0=方向一致,≤4/8=证伪),"
        "v2(补历史预告扩样本)跑数前登记;敏感性固定K为探索性附表不入判据",
    ]
    notes_html = "".join(f"<li>{_e(n)}</li>" for n in notes)
    n_members = len(settled_rows)
    return f"""<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>
<title>预告队列研究 · {date.today().isoformat()}</title>
<style>body{{font-family:system-ui,'Segoe UI',sans-serif;margin:24px;color:#1c1c1a;background:#f9f9f7}}
table{{border-collapse:collapse;font-size:13px;margin:14px 0;background:#fff}}
th,td{{border:1px solid #e1e0d9;padding:6px 10px;text-align:right}}
th:first-child,td:first-child{{text-align:left}} th{{background:#f3f2ee}}
.ok{{color:#0ca30c}} .crit{{color:#d03b3b}} .pos{{color:#0ca30c}} .neg{{color:#d03b3b}} .dim{{color:#898781}}
details.conc-detail{{margin:6px 0}} details.conc-detail summary{{cursor:pointer;font-weight:600;color:#52514e}}
.hint{{color:#898781;font-size:12px;line-height:1.8}} h2{{font-size:16px}} li{{font-size:13px;line-height:1.8;color:#52514e}}
.chip{{display:inline-block;background:#fff;border:1px solid #e1e0d9;border-radius:12px;padding:2px 10px;margin:2px;font-size:12px}}
.chips{{margin:8px 0}}</style></head><body>
<h2>预告队列研究 v1 · 截点回测({n_members} 个已结算期)</h2>
<table><tr><th>臂</th><th>有效期</th><th>胜率(超额&gt;0)</th><th>平均超额</th><th>中位超额</th><th>资金曲线</th><th>基准曲线</th></tr>{''.join(arm_rows)}</table>
<h2>逐期明细(✓/✗=当期跑赢基准)</h2>
<table><tr><th>报告期</th><th>截点→卖出</th><th>队列</th><th>K(B2可选)</th><th>有效腿</th><th>臂A</th><th>臂B1</th><th>臂B2</th><th>臂C(n)</th><th>{_e(bench_name)}</th></tr>{''.join(per_rows)}</table>
<h2>K 敏感性(B1 固定K · 探索性·不入判据)</h2>
<table><tr><th>组合</th><th>胜率</th><th>平均超额</th><th>中位超额</th></tr>{sens_rows}</table>
<h2>归因(个股级·超额 vs 同窗基准)</h2>
<div class='hint'>混合口径=全 8 期个股池按维度分组:统计力最高,但大期(年报/中报队列)天然权重大、
组间差异可能被单期行情混杂——关键维度见下方逐期矩阵修正</div>{attr_html}
<h2>逐期归因矩阵(先期内·后跨期等权)</h2>{_per_attr_html(per_attr or {}, settled or [])}
<h2>每期输家 Top5(复盘素材)</h2>
<table><tr><th>期</th><th>代码</th><th>名称</th><th>行业</th><th>预告类型</th><th>扭亏/应收/精筛</th><th>腿收益</th><th>超额</th><th>市值十分位</th><th>PEG五分位</th></tr>{loser_rows}</table>
{live_html}
<h2>口径与近似(诚实注记)</h2><ul>{notes_html}</ul>
<div class='hint'>价格缺失 {len(price_stats.get('missing') or ())} 只 · 耗时 {took_s:.1f}s ·
生成: python scripts/validate_forecast_cohort.py · 温度计非开关——本报告只陈述回放事实</div>
</body></html>"""


if __name__ == "__main__":
    main()
