"""预告队列实盘视图 + 共享装配层(2026-10-18 · Claim 004 附产品)。

职责切分:
  period_members   单期队列装配(门+旗+两轨+商誉档+红旗剔除)——**validate_forecast_cohort
                   与本模块共用同一份**(仓库铁律: 回测和实盘共用同一套判定;改这里=两边一起变,
                   验证器重跑后数字应与台账结算版逐期一致)。
  build_cohort_view  看板⑥节「预告队列」数据(当前管道期三态: gap/forming/active)。

口径与冻结件一致(docs/CLAIMS_LEDGER.md Claim 004 + CONTEXT.md「预告队列/截点/持有窗/精选臂」):
队列=预告窗内公告∧过预告环地板(≥50%/扭亏)的全体(含续留再过门),硬剔 ST(当时名称)+
存贷双高(zcfz 代理·as-of)+商誉/净资产>30%(行项 NULL≈0);臂C=估值门两轨+两轨合并排序
+Top-N 封顶。措辞纪律: 只陈列名单与日期,不出现买卖建议,永不喂引擎。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from . import calendar as cal
from . import cohort as ch
from . import study


# ---------- 日期工具(与验证器同款;库内 ISO/紧凑混存,fina 紧凑·report/balance ISO) ----------

def _c(d) -> str:
    return str(d).replace("-", "")[:8]


def _avail(announce, period: str) -> str:
    """某期数据可得日(紧凑)=min(公告日, 该期正式报法定截止)。真公告早→真值;被刷新腿重盖成
    晚值→退法定截止:永不前视、最多保守滞后。公告日早于期末=脏行→法定截止。"""
    dl = cal.formal_deadline(period).strftime("%Y%m%d")
    a = _c(announce) if announce else ""
    if not a or a < period:
        return dl
    return min(a, dl)


def balance_asof_rows(store) -> dict[str, list[tuple]]:
    """全库资产负债 → {code: [(可得日, period, {cash,receivables,total_assets,debt_ratio,equity})..]}
    升序;可得日=_avail(公告日,期)。"""
    out: dict[str, list[tuple]] = {}
    for p in store.stock_balance_periods(min_rows=100):
        bf = store.get_stock_balance_period(p)
        if not len(bf):
            continue
        for code, r in bf.iterrows():
            out.setdefault(str(code), []).append(
                (_avail(r.get("announce_date"), p), p,
                 {k: r.get(k) for k in ("cash", "receivables", "total_assets",
                                        "debt_ratio", "equity")}))
    for lst in out.values():
        lst.sort(key=lambda t: t[0])
    return out


def _latest_asof(rows: list[tuple] | None, asof: str) -> dict | None:
    if not rows:
        return None
    target = _c(asof)
    pick = None
    for ann, _p, row in rows:
        if ann <= target:
            pick = row
        else:
            break
    return pick


def goodwill_band_row(store, code: str, bal_row: dict | None, cutoff: str,
                      bf_cache: dict) -> str:
    """商誉/净资产档: goodwill = balance_full 保守可得日≤cutoff 的最新一期行(行项 NULL≈0)
    → 全无行者 sina 精筛腿兜底;equity = zcfz as-of 行。档位归 cohort.goodwill_band。"""
    y = int(cutoff[:4])
    cands = []
    for yy in (y, y - 1, y - 2):
        for tail in ("1231", "0930", "0630", "0331"):
            rp = f"{yy}{tail}"
            try:
                if cal.formal_deadline(rp).strftime("%Y%m%d") <= _c(cutoff):
                    cands.append(rp)
            except ValueError:
                continue
    cands.sort(reverse=True)
    gw = None
    for rp in cands:
        if rp not in bf_cache:
            try:
                bfp = store.get_balance_full_period(rp)
            except Exception:  # noqa: BLE001
                bfp = pd.DataFrame()
            bf_cache[rp] = bfp if bfp is not None else pd.DataFrame()
        bf = bf_cache[rp]
        if bf is None or not len(bf) or code not in bf.index:
            continue
        gw_v = bf.loc[code].get("goodwill")
        gw = 0.0 if (gw_v is None or pd.isna(gw_v)) else float(gw_v)
        break
    if gw is None:
        try:
            panel = store.get_stock_financials_panel(code, metrics=["goodwill"])
        except Exception:  # noqa: BLE001
            panel = None
        if panel is not None and len(panel) and "goodwill" in panel.columns:
            s = panel["goodwill"].dropna()
            for rp in sorted((str(x) for x in s.index), reverse=True):
                try:
                    if cal.formal_deadline(rp).strftime("%Y%m%d") <= _c(cutoff):
                        gw = float(s[rp])
                        break
                except (ValueError, KeyError):
                    continue
    eq = (bal_row or {}).get("equity")
    eq_f = None if eq is None or pd.isna(eq) else float(eq)
    return ch.goodwill_band(gw, eq_f)


# ---------- 单期队列装配(共享核心: 验证器与本模块同一份) ----------

def period_members(store, hcfg: dict, events: list[dict], period: str, cutoff: str,
                   balance_rows: dict, shares: dict, np_abs: dict, report_eps: dict,
                   price_getter, ind_info: dict, bf_cache: dict
                   ) -> tuple[list[dict], int, int]:
    """某期队列装配(与 Claim 004 结算版逐行同构): 事件=ch.cohort_events(events, period, cutoff);
    门=study.gate_at_event(队列不拦估值门);旗=扭亏(gate)+应收黄旗(年报量纲分母)+未精筛
    (fina 覆盖);两轨=行业级 type;商誉档+红旗剔除。Returns (members, n_pre, n_gw_red)。"""
    cutoff_c = _c(cutoff)
    evs = ch.cohort_events(events, period, cutoff)
    balance_asof: dict[tuple[str, str], dict] = {}
    for ev in evs:
        row = _latest_asof(balance_rows.get(ev["code"]), cutoff)
        if row is not None:
            balance_asof[(period, ev["code"])] = row

    members: list[dict] = []
    for ev in evs:
        g = study.gate_at_event(ev, hcfg, price_getter, shares, np_abs, balance_asof,
                                arm="full", report_eps=report_eps)
        if not (g["passed"] and not g["risk_red"]):
            continue                  # 队列=预告环过门 ∧ 非ST非存贷双高(估值门不拦队列)
        members.append({
            "code": ev["code"], "period": period, "np_yoy": g["np_yoy"],
            "ftype": ev.get("type"), "flags": list(g["flags"]),
            "peg": g["peg"], "cap": g["cap"],
            "score": g["peg"] if g["peg"] is not None else -(ev.get("np_yoy") or 0.0),
        })

    rep_cache: dict[str, pd.DataFrame] = {}
    for m in members:
        code = m["code"]
        if code not in rep_cache:
            rep_cache[code] = store.stock_report_rows_for(code)
        rep = rep_cache[code]
        latest_pub, rev_ann = None, None
        bvps_events: list[tuple] = []
        if len(rep):
            for rp, r in rep.iterrows():
                rp_s = str(rp)
                avail = _avail(r.get("announce_date"), rp_s)
                if avail > cutoff_c:
                    continue
                if latest_pub is None or rp_s > latest_pub:
                    latest_pub = rp_s
                if rp_s.endswith("1231"):
                    rev_ann = r.get("rev_abs")   # 升序遍历 → 留下最新年报
                bv = r.get("bvps")
                if bv is not None and pd.notna(bv) and float(bv) > 0:
                    bvps_events.append((avail, rp_s, float(bv)))
        m["bvps_events"] = bvps_events
        bal = balance_asof.get((period, code))
        m["receivable_yellow"] = False
        try:
            thr = float((hcfg.get("risk") or {}).get("receivable_rev_yellow", 0.50))
        except (TypeError, ValueError):
            thr = 0.50
        if bal and rev_ann is not None and pd.notna(rev_ann) and float(rev_ann) > 0:
            rec = bal.get("receivables")
            if rec is not None and pd.notna(rec):
                m["receivable_yellow"] = float(rec) / float(rev_ann) > thr
        m["unrefined"] = True
        if latest_pub is not None:
            for fp, fa, _v in store.profit_dedt_rows(code):
                if fp == latest_pub and _avail(fa, fp) <= cutoff_c:
                    m["unrefined"] = False
                    break
        # 扭亏·上年计提占亏损比(MANUAL_REVIEW 3.1 头号检查项的程序化;展示 chip 不进门)
        m["tk_depr_pct"] = None
        if "扭亏" in m["flags"]:
            base_p = f"{int(period[:4]) - 1}{period[4:]}"    # 上年同期=扭亏的基数期
            for fp, fa, imp, ni in store.income_impairment_rows(code):
                if fp == base_p:
                    if _avail(fa, fp) <= cutoff_c:
                        m["tk_depr_pct"] = ch.wash_sale_ratio(imp, ni)
                    break

    # 两轨分流(行业级 type) + 周期 PB 分位(保守可得日阶梯) + 商誉档
    for m in members:
        info_c = ind_info.get(m["code"]) or {}
        m["track"] = "pb" if info_c.get("type") == "cyclic" else "peg"
        m["pb_pct"] = None
        if m["track"] == "pb":
            pdf = price_getter(m["code"])
            if pdf is not None and len(pdf):
                m["pb_pct"] = ch.pb_percentile_asof(
                    pdf["close"], [e for e in m["bvps_events"] if e[0] <= cutoff_c],
                    cutoff, min_history=120)
        m["gw_band"] = goodwill_band_row(store, m["code"],
                                         balance_asof.get((period, m["code"])), cutoff, bf_cache)
    n_pre = len(members)
    n_gw_red = sum(1 for m in members if m["gw_band"] == ch.GOODWILL_RED_BAND)
    if n_gw_red:
        members = [m for m in members if m["gw_band"] != ch.GOODWILL_RED_BAND]
    return members, n_pre, n_gw_red


# ---------- 看板⑥节数据装配 ----------

def _prev_period(period: str) -> str:
    tails = ["0331", "0630", "0930", "1231"]
    y, tail = int(period[:4]), period[4:]
    pt = tails[tails.index(tail) - 1]           # Python 负索引: 0331 的上期=上年 1231 ✔
    return f"{y - (1 if tail == '0331' else 0)}{pt}"


def _assemble_context(store, config):
    """静态原料(events/shares/np_abs/行业/价格缓存)——单期装配的公共输入。"""
    from . import valuation as vl
    hcfg = ((config.params.get("stock_pool") or {}).get("high_pool")) or {}
    nc = store.get_namechange_all()
    st_filter = None
    if nc is not None and len(nc):
        st_filter = study.make_st_filter(study.st_intervals_from_namechange(nc))
    spot = store.latest_stock_spot()
    shares: dict[str, Optional[float]] = {}
    names: dict[str, str] = {}
    if len(spot):
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
    ind_df = store.sw_industry_map()
    class_cfg = config.sw_industry_class()
    ind_info: dict[str, dict] = {}
    if len(ind_df):
        for code, r in ind_df.iterrows():
            entry = class_cfg.get(str(r.get("industry"))) or {}
            ind_info[str(code)] = {"industry": str(r.get("industry") or "未映射"),
                                   "l1": str(r.get("l1") or "") or "未映射",
                                   "type": entry.get("type"),
                                   "commodity": entry.get("commodity")}
    cache: dict[str, pd.DataFrame] = {}

    def price_getter(code: str):
        if code not in cache:
            df = store.get_series(code)
            cache[code] = df if df is not None and len(df) else pd.DataFrame()
        df = cache[code]
        return cache[code] if len(cache[code]) else None

    return hcfg, st_filter, shares, names, np_abs, report_eps, ind_info, price_getter


def _trading_around(store, cutoff_iso: str, deadline_iso: str) -> tuple[Optional[str], Optional[str]]:
    """买入日=截点后首个交易日;卖出日=截止(含)前最后交易日(000300 日历)。截止日在指数数据
    末端之后(未来)=按法定截止日原样显示计划日,临期非交易日自动前移由执行时定(显示注记)。"""
    df = store.get_index_daily_series("000300")
    if df is None or not len(df):
        return None, deadline_iso
    idx = [str(d) for d in df.index]
    buy = next((d for d in idx if d > cutoff_iso), None)
    if deadline_iso > idx[-1]:
        return buy, deadline_iso
    sell = None
    for d in idx:
        if cutoff_iso < d <= deadline_iso:
            sell = d
    return buy, sell


def build_cohort_view(store, config, now=None) -> dict:
    """⑥节「预告队列」数据(三态时钟 + 活队列定格名单 + 臂C 徽标 + 台账结论 chip)。
    只陈列事实——名单/日期/成绩表述锁定台账口径,不出现买卖建议。"""
    now = now or datetime.now()
    today = now.date()
    period = cal.current_period(today)
    win = cal.cohort_window_state(period, today)
    out: dict = {"period": period, **win,
                 "claim": store.get_meta("forecast_cohort_conclusion")}

    if win["state"] == "gap":
        prev = _prev_period(period)
        out["prev_period"] = prev
        out["prev_deadline"] = cal.formal_deadline(prev).isoformat()
        return out

    (hcfg, st_filter, shares, names, np_abs, report_eps, ind_info,
     price_getter) = _assemble_context(store, config)
    frames = {period: {"forecast": store.get_stock_forecast_period(period)}}
    events = study.collect_events(frames, st_filter=st_filter)
    cutoff_eff = win["cutoff"] if win["state"] == "active" else today.isoformat()
    bf_cache: dict = {}
    members, n_pre, n_gw_red = period_members(
        store, hcfg, events, period, cutoff_eff, balance_asof_rows(store),
        shares, np_abs, report_eps, price_getter, ind_info, bf_cache)

    out.update(n=len(members), n_pre=n_pre, n_gw_red=n_gw_red)
    arm_c: list[str] = []
    if win["state"] == "active":
        arm_c = ch.select_pool_mirror(
            members, top_n=int(hcfg.get("top_n", 100)),
            peg_max=float(hcfg.get("peg_max", 1.0)),
            pb_pct_max=float(hcfg.get("pb_pct_max", 0.30)))
        buy, sell = _trading_around(store, win["cutoff"], win["deadline"])
        out.update(arm_c=arm_c, n_arm_c=len(arm_c), buy_date=buy, sell_date=sell)
    arm_c_set = set(arm_c)
    rows = []
    for m in sorted(members, key=lambda x: (x.get("score") if x.get("score") is not None
                                            else float("inf"), x["code"])):
        info = ind_info.get(m["code"]) or {}
        if m["track"] == "peg":
            val = "—" if m.get("peg") is None else f"PEG {m['peg']:.2f}"
        else:
            val = "—" if m.get("pb_pct") is None else f"PB分位 {m['pb_pct']:.0%}"
        flags = []
        for f in m["flags"]:
            if f == "扭亏" and m.get("tk_depr_pct") is not None:
                flags.append(f"扭亏·上年计提{m['tk_depr_pct']:.0%}")
            else:
                flags.append(f)
        if m["receivable_yellow"]:
            flags.append("应收黄")
        if m["unrefined"]:
            flags.append("未精筛")
        rows.append({
            "code": m["code"], "name": names.get(m["code"], m["code"]),
            "industry": info.get("industry", "未映射"), "ftype": str(m["ftype"] or "未标注"),
            "track": "PB轨" if m["track"] == "pb" else "PEG轨", "val": val,
            "flags": "·".join(flags) or "无", "gw_band": m["gw_band"],
            "in_c": m["code"] in arm_c_set,
        })
    out["rows"] = rows
    return out
