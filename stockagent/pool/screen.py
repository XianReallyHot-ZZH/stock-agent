"""装配层(唯一读 store 的模块): universe → 逐股装配 → 六表快照 dict(render 的契约)。

流程(计划 §7): consensus∩spot → derive_universe → join_industry → 逐股(分红前复权序列 →
偏离/回撤/企稳/嫌疑带 → 当期三环 → 窗口状态 → S1 复合分 / S2 猛分 / 修正动量 / 变脸 / PEAD)
→ {s1_rows, s2_rows, revision, pead, face_up/down, 元数据}。全部只读,永不喂引擎。
纯函数姊妹模块见 universe/prices/scoring/fierce/calendar/revision/pead/facechange。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import pandas as pd

from ..config import get_config
from ..research.timing import deviation_extremes
from ..tracker import leading
from ..tracker.stock_diagnose import diagnose_davis, price_reversal, stabilize_factor
from . import calendar as cal
from . import facechange as fc
from . import fierce as fe
from . import pead as pe
from . import prices as pr
from . import revision as rv
from . import scoring as sc
from . import universe as uni


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


# ---------- 全市场面板 → 逐股 dict(一次拉 8 期,比逐股 2800 次调用省) ----------
def gather_forecasts(store, codes: set[str]) -> dict[str, pd.DataFrame]:
    """预告面板 → {symbol: df[report_period,yoy,type,announce_date]}(validate 脚本共用)。"""
    out: dict[str, list] = {}
    for period, _n in store.forecast_period_counts():
        frame = store.get_stock_forecast_period(period)
        for code, row in frame.iterrows():
            if code in codes:
                out.setdefault(code, []).append(
                    {"report_period": period, "yoy": row.get("yoy"),
                     "type": row.get("type"), "announce_date": row.get("announce_date")})
    return {c: pd.DataFrame(r) for c, r in out.items() if r}


def gather_actuals(store, codes: set[str]) -> dict[str, pd.DataFrame]:
    """正式报面板 → {symbol: df(index=report_period [np_yoy, announce_date])}。"""
    out: dict[str, list] = {}
    for period, _n in store.forecast_period_counts():
        try:
            frame = store.get_stock_report_period(period)
        except Exception:  # noqa: BLE001 — 缺期跳过
            continue
        for code, row in frame.iterrows():
            if code in codes:
                out.setdefault(code, []).append(
                    {"report_period": period, "np_yoy": row.get("np_yoy"),
                     "announce_date": row.get("announce_date")})
    merged: dict[str, pd.DataFrame] = {}
    for c, r in out.items():
        df = pd.DataFrame(r)
        merged[c] = df.set_index("report_period").sort_index()
    return merged


def _ring_dates(fc_df, ex_frame, ac_frame, period: str, code: str) -> dict:
    """当期三环的公告日(forecast 从逐股面板;express/actual 从当期帧)。None = 未落地。"""
    f = e = a = None
    if fc_df is not None and len(fc_df):
        row = fc_df[fc_df["report_period"] == period]
        if len(row):
            f = str(row.iloc[-1].get("announce_date") or "")[:10] or None
    if ex_frame is not None and code in ex_frame.index:
        v = ex_frame.loc[code, "announce_date"]
        e = str(v)[:10] if isinstance(v, str) and v else None
    if ac_frame is not None and code in ac_frame.index:
        v = ac_frame.loc[code, "announce_date"]
        a = str(v)[:10] if isinstance(v, str) and v else None
    return {"forecast": f, "express": e, "actual": a}


def build_pool_snapshot(store, config=None, asof: str | None = None) -> dict:
    """装配六表快照(读 store,只读)。asof = "YYYY-MM-DD"(回放/测试)| None=今天。"""
    cfg = config or get_config()
    p = cfg.params.get("stock_pool", {}) or {}
    ucfg = p.get("universe", {}) or {}
    s1cfg = p.get("strategy1", {}) or {}
    s2cfg = p.get("strategy2", {}) or {}
    fccfg = p.get("facechange", {}) or {}
    revcfg = p.get("revision", {}) or {}
    peadcfg = p.get("pead", {}) or {}
    now = (datetime.strptime(asof, "%Y-%m-%d") if asof else datetime.now())

    # ---- universe + 行业 ----
    _, cons = store.get_consensus_snapshot(asof=now.strftime("%Y%m%d"))
    spot = store.latest_stock_spot()
    uni_df = uni.derive_universe(
        cons, spot, min_reports=int(ucfg.get("min_reports", 3)),
        exclude_prefixes=tuple(ucfg.get("exclude_name_prefixes", uni.EXCLUDED_NAME_PREFIXES)))
    ind_map = store.industry_map()
    class_cfg = cfg.industry_class()
    joined = uni.join_industry(uni_df, ind_map, class_cfg)
    cov = uni.industry_coverage(joined)
    codes = [str(c) for c in joined.index]
    codeset = set(codes)
    # 名称查表(spot join 而来): PEAD/修正动量等按 code 装配的表补 name 用——
    # 不补则看板股票列退 code 兜底,显示「688308 688308」(2026-08 抓回)
    uni_names = joined["name"].astype(str).to_dict() if "name" in joined.columns else {}

    # ---- 当期三环帧 + 逐股面板 ----
    period = cal.current_period(now)
    ex_frame = store.get_stock_express_period(period)
    ac_frame = store.get_stock_report_period(period)
    fc_map = gather_forecasts(store, codeset)
    ac_map = gather_actuals(store, codeset)

    # ---- 修正动量(全池一表) ----
    n_dates = len(store.consensus_snapshot_dates())
    snap_then_date = (now - timedelta(days=7 * int(revcfg.get("lookback_weeks", 4)))).strftime("%Y%m%d")
    _, snap_then = store.get_consensus_snapshot(asof=snap_then_date)
    rev = rv.revision_table(cons, snap_then, dates_available=n_dates, codes=codes, cfg=revcfg)
    for r in rev["rows"]:                      # 纯函数按 code 出行,名称由装配层补
        r["name"] = uni_names.get(r["code"], r["code"])
    rev_by_code = {r["code"]: r for r in rev["rows"]}

    # ---- 前瞻 g 横截面分位(成长猛 rank 腿) ----
    g_series = None
    if "eps_fy1" in joined.columns and "eps_fy2" in joined.columns:
        e1 = pd.to_numeric(joined["eps_fy1"], errors="coerce")
        e2 = pd.to_numeric(joined["eps_fy2"], errors="coerce")
        g = (e2 / e1 - 1.0).where((e1 > 0) & (e2 > 0))
        g_series = g.rank(pct=True)

    # ---- 商品信号(品种级一次) ----
    varieties = sorted({v for v in joined.get("commodity_variety", pd.Series(dtype=object))
                        if isinstance(v, str) and v})
    com_signals = {v: leading.commodity_signal(store, v, asof=now.strftime("%Y-%m-%d"), config=cfg)
                   for v in varieties}

    # ---- PEAD 事件(近 periods_back 期,公告日 ≤ now) ----
    pead_events: list[dict] = []
    snap_cache: dict[str, pd.DataFrame] = {}

    def _snap(asof_day: str) -> pd.DataFrame:
        key = asof_day.replace("-", "")
        if key not in snap_cache:
            snap_cache[key] = store.get_consensus_snapshot(asof=key)[1]
        return snap_cache[key]

    periods = [pt for pt, _n in store.forecast_period_counts()][:int(peadcfg.get("periods_back", 8))]
    recent_cut = (now - timedelta(days=120)).strftime("%Y-%m-%d")
    for pt in periods:
        frame = store.get_stock_forecast_period(pt)
        sub = frame[[c in codeset for c in frame.index]]
        prior_frame = store.get_stock_report_period(f"{int(pt[:4]) - 1}{pt[4:]}") \
            if pt[4:] else None
        for code, row in sub.iterrows():
            yoy, ann = row.get("yoy"), str(row.get("announce_date") or "")[:10]
            if pd.isna(yoy) or not ann or ann > now.strftime("%Y-%m-%d") or ann < recent_cut:
                continue
            s_now = _snap(ann)
            s_prior = _snap((datetime.strptime(ann, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d"))
            eps_now = fy1 = eps_prior = None
            if code in s_now.index and "eps_fy1" in s_now.columns:
                eps_now, fy1 = s_now.loc[code, "eps_fy1"], s_now.loc[code, "fy1_year"]
            if code in s_prior.index and "eps_fy1" in s_prior.columns:
                eps_prior = s_prior.loc[code, "eps_fy1"]
            prior_np = None
            if prior_frame is not None and code in prior_frame.index:
                prow = prior_frame.loc[code]
                pann = str(prow.get("announce_date") or "")[:10]
                v = prow.get("np_yoy")
                if pann and pann < ann and v is not None and not pd.isna(v):
                    prior_np = float(v)
            r = pe.pead_surprise(float(yoy), pt, eps_now=eps_now, eps_prior=eps_prior,
                                 fy1_year=fy1, prior_actual_yoy=prior_np)
            if r["valid"]:
                pead_events.append({"code": str(code),
                                    "name": uni_names.get(str(code), str(code)),
                                    "period": pt, "announce_date": ann,
                                    "type": row.get("type"), "forecast_yoy": float(yoy),
                                    "surprise_pp": r["surprise_pp"], "expected": r["expected"],
                                    "leg": r["leg"]})
    pead_rows = pe.pead_rank(pead_events,
                             surprise_min_pp=float(peadcfg.get("surprise_min_pp", 10.0)),
                             top_n=int(peadcfg.get("top_n", 50)))

    # ---- 逐股装配 ----
    s1_rows: list[dict] = []
    s2_rows: list[dict] = []
    face_up: list[dict] = []
    face_down: list[dict] = []
    n_price_fresh = 0
    fresh_cut = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    cliff_lo = float((p.get("prices", {}) or {}).get("cliff_lo", 0.008))
    cliff_hi = float((p.get("prices", {}) or {}).get("cliff_hi", 0.08))
    for code in codes:
        row = joined.loc[code]
        name = str(row.get("name", code))
        stype = row.get("type")
        price_df = store.get_series(code)
        if len(price_df) < 80:
            continue
        last_px_date = str(price_df.index[-1])
        if last_px_date >= fresh_cut:
            n_price_fresh += 1
        div = store.get_stock_dividend_series(code)
        adj, _ = pr.dividend_adjusted_close(price_df["close"].astype(float),
                                            div if len(div) else None)
        # 三环 + 窗口
        rings = _ring_dates(fc_map.get(code), ex_frame, ac_frame, period, code)
        win = cal.per_stock_window(now, rings["forecast"], rings["express"], rings["actual"],
                                   stock_type=stype)
        pr_ = price_reversal(adj)
        dev = deviation_extremes(adj)
        suspects = pr.unexplained_cliffs(adj, div if len(div) else None, lo=cliff_lo, hi=cliff_hi)
        suspect = any(s >= str(adj.index[-6]) for s in suspects)   # 最近 5 根内
        # 变脸(8 期面板)
        fcd = fc.facechange(ac_map.get(code), cfg=fccfg) if code in ac_map else \
            fc.facechange(None)
        if fcd["valid"]:
            rec = {"code": code, "name": name, "industry": row.get("industry", "未映射"),
                   "direction": fcd["direction"], "kinds": fcd["kinds"], "detail": fcd["detail"],
                   "tail": fcd["tail"]}
            (face_up if fcd["direction"] == "up" else
             face_down if fcd["direction"] == "down" else []).append(rec)
        # S1
        fc_type = None
        fcdf = fc_map.get(code)
        if fcdf is not None and len(fcdf):
            past = fcdf[fcdf["announce_date"].astype(str).str[:10] <= now.strftime("%Y-%m-%d")]
            if len(past):
                fc_type = past.sort_values("report_period").iloc[-1].get("type")
        rev_row = rev_by_code.get(code)
        rev_pct = rev_row["rev_pct"] / 100.0 if rev_row else None
        guards = sc.guardrails(fc_type, rev_pct, fcd.get("direction"), cfg=s1cfg)
        stab = stabilize_factor(pr_["recent_return"])
        s1 = sc.oversold_score(dev, stab, guards, cfg=s1cfg)
        if s1["trigger"]:
            run_start = sc.oversold_run_start(adj)
            days_in = None
            if run_start and run_start in adj.index:
                days_in = len(adj.loc[run_start:]) - 1
            s1_rows.append({
                "code": code, "name": name, "industry": row.get("industry", "未映射"),
                "type": stype, "dev_pct": dev["pct"], "cur_dev": dev["cur_dev"],
                "stabilize": stab, "score": s1["score"], "depth": s1["depth"],
                "guard_pass": s1["guard_pass"], "guard_flags": s1["guard_flags"],
                "suspect": suspect, "run_start": run_start, "days_in_run": days_in,
            })
        # S2
        dd = fe.deep_drawdown(adj, min_dd=float(s2cfg.get("drawdown_min", 0.40)),
                              lookback=int(s2cfg.get("drawdown_lookback_days", 250)),
                              recent=int(s2cfg.get("recent_window", 60)))
        if dd["deep"]:
            fierce = None
            variety = row.get("commodity_variety")
            if stype == "cyclic":
                com = com_signals.get(variety) if isinstance(variety, str) else None
                cg = None
                if "eps_fy1" in row.index and "eps_fy2" in row.index:
                    e1, e2 = row.get("eps_fy1"), row.get("eps_fy2")
                    if not _nan(e1) and not _nan(e2) and float(e1) > 0 and float(e2) > 0:
                        cg = float(e2) / float(e1) - 1.0
                fierce = fe.cyclic_fierce(com, dd["recent_return"], cg,
                                          cfg=(s2cfg.get("cyclic") or {}))
                legs_txt = f"健康{fierce.get('health') or 0:.1f}×落后{fierce.get('lag_factor') or 0:.2f}"
            elif stype == "growth":
                grp = g_series.get(code) if g_series is not None else None
                rev_up = rev_row["up"] if rev_row else None
                accel = fe.accel_from_actuals(ac_map.get(code)) if code in ac_map else None
                fierce = fe.growth_fierce(g_rank_pct=None if _nan(grp) else float(grp),
                                          revision_up=rev_up, accel_pp=accel,
                                          cfg=(s2cfg.get("growth") or {}))
                legs_txt = "/".join(f"{k}={v:.2f}" for k, v in fierce["legs"].items()) or "无腿"
            if fierce is not None and fe.eligible_s2(True, win["state"], fierce, stype, s2cfg):
                s2_rows.append({
                    "code": code, "name": name, "industry": row.get("industry", "未映射"),
                    "type": stype, "chip": win["chip"], "days_to_formal": win["days_to_formal"],
                    "drawdown": dd["drawdown"], "recent_return": dd["recent_return"],
                    "fierce": fierce["fierce"], "legs": legs_txt,
                    "confirmed": fierce.get("confirmed"),
                    "variety": variety if isinstance(variety, str) else None,
                })

    s1_rows.sort(key=lambda r: (-(r["score"] if r["score"] is not None else -1), r["dev_pct"]))
    s2_rows.sort(key=lambda r: -r["fierce"])
    face_up.sort(key=lambda r: r["code"])
    face_down.sort(key=lambda r: r["code"])

    # ---- stage-2: 候选集(每策略 Top-N,S1 排除除权嫌疑)→ davis 表 + 卡摘要(腿齐才出) ----
    top_n2_stage = int((p.get("stage2", {}) or {}).get("top_n", 30))
    s1_stage = [r["code"] for r in s1_rows
                if r.get("score") is not None and not r.get("suspect")][:top_n2_stage]
    s2_stage = [r["code"] for r in s2_rows][:top_n2_stage]
    stage2_codes = list(dict.fromkeys(s1_stage + s2_stage))
    name_map = {str(c): str(joined.loc[c].get("name", c)) for c in stage2_codes if c in joined.index}
    davis_rows: list[dict] = []
    stage2_cards: list[dict] = []
    for code in stage2_codes:
        if store.last_stock_valuation_date(code, "pe_ttm") is None:
            continue  # 估值腿未拉(周度刷新后下次出)
        try:
            d = diagnose_davis(code, store, cfg)
        except Exception:  # noqa: BLE001 — 单股腿缺不炸整板
            continue
        if not d.get("valid"):
            continue
        tag = ("②" if code in s2_stage else "") + ("①" if code in s1_stage else "")
        label = d.get("label") or "—"
        notes = []
        if not _nan(d.get("pe_pct")):
            notes.append(f"PE分位{d['pe_pct']:.0%}")
        if not _nan(d.get("profit_yoy_latest")):
            notes.append(f"年报净利{d['profit_yoy_latest'] * 100:+.0f}%")
        if d.get("quality_warning"):
            notes.append("⚠️含金量低(一次性利润)")
        davis_rows.append({"code": code, "name": name_map.get(code, code),
                           "label": label, "note": " · ".join(notes)})
        stage2_cards.append({"code": code, "name": name_map.get(code, code), "tag": tag,
                             "summary": f"{label} · {' · '.join(notes)}"})

    top_n1 = int(s1cfg.get("top_n", 50))
    top_n2 = int(s2cfg.get("top_n", 50))
    return {
        "as_of": now.strftime("%Y-%m-%d"),
        "period": period,
        "cfg_note": {"trigger_pct": float(s1cfg.get("trigger_pct", 0.05))},
        "davis_rows": davis_rows,     # stage-2 估值/财务腿就绪的候选才有
        "stage2_cards": stage2_cards,
        "stage2_codes": stage2_codes,  # CLI 周度腿刷新用(每策略 Top-N 并集)
        "universe_stats": cov,
        "price_freshness": {"fresh": n_price_fresh, "n": len(codes)},
        "spot_date": store.get_meta("last_stock_spot_update", ""),
        "industry_snapshot": store.last_industry_snapshot() or "",
        "n_snapshots": n_dates,
        "revision": {"rows": rev["rows"][:int(revcfg.get("top_n", 50))],
                     "cold_start": rev["cold_start"]},
        "s1_rows": s1_rows[:top_n1],
        "s1_total": len(s1_rows),
        "s2_rows": s2_rows[:top_n2],
        "s2_total": len(s2_rows),
        "pead_rows": pead_rows,
        "face_rows_up": face_up[:int(fccfg.get("top_n", 50))],
        "face_rows_down": face_down[:int(fccfg.get("top_n", 50))],
        "conclusions": {
            "deviation": store.get_meta("deviation_extreme_conclusion"),
            "pead": store.get_meta("pead_conclusion"),
        },
        "window_note": cal.window_b_period(now, grace_days=int(s2cfg.get("window_b_grace_days", 14))),
    }
