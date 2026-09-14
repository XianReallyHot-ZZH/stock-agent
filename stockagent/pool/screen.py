"""装配层(唯一读 store 的模块): 全市场宇宙 → 三环地板粗筛 → 幸存者精筛 → 高业绩池快照。

流程(V8 重写, 2026-09; 旧六表装配已退役): spot 非 ST 宇宙 → 当期三环(逐股活跃环+
地板判定) → 过地板幸存者(批量粗筛,归母口径) → 逐股懒装配(sina 扣非/商誉精筛腿 +
PE_ttm/PEG 或 PB 分位两轨估值 + 红黄风险旗) → 两轨各自排名合并 Top-N → 池快照 dict
(render 的契约)。全部只读,永不喂引擎;纯函数姊妹模块见 universe/gates/valuation/risk/
sector/calendar/prices。验证器(validate_high_earnings_pool)复用同一套判定原语做回放。
"""
from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from ..config import get_config
from . import calendar as cal
from . import gates as gt
from . import prices as pr
from . import risk as rk
from . import sector as sec
from . import universe as uni
from . import valuation as vl


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _period_frame(frame: pd.DataFrame, codes: set[str]) -> pd.DataFrame:
    """期间帧过滤到宇宙 codes(保留全列, index=code)。"""
    if frame is None or len(frame) == 0:
        return pd.DataFrame()
    return frame[[str(c) in codes for c in frame.index]]


def _ring_input(fc_row, ex_row, ac_row) -> tuple[dict | None, dict | None, dict | None]:
    """三环行 → gates 契约 dict(公告日 + 环数据; None=未落地)。"""
    forecast = express = actual = None
    if fc_row is not None:
        forecast = {"announce_date": fc_row.get("announce_date"),
                    "yoy": fc_row.get("yoy"), "type": fc_row.get("type")}
    if ex_row is not None:
        express = {"announce_date": ex_row.get("announce_date"),
                   "np_yoy": ex_row.get("np_yoy"), "rev_yoy": ex_row.get("rev_yoy")}
    if ac_row is not None:
        actual = {"announce_date": ac_row.get("announce_date"),
                  "np_yoy": ac_row.get("np_yoy"), "rev_yoy": ac_row.get("rev_yoy")}
    return forecast, express, actual


def _window_periods(now, n: int = 3) -> list[str]:
    """最近 n 个季末报告期(YYYYMMDD 降序;含尚未开披露的当期——环帧空自然跳过)。"""
    from datetime import date as _date
    d = now.date() if hasattr(now, "date") else now
    out: list[str] = []
    y = d.year
    while len(out) < n:
        for qm, qd in ((12, 31), (9, 30), (6, 30), (3, 31)):
            if _date(y, qm, qd) <= d:
                key = f"{y}{qm:02d}{qd:02d}"
                if key not in out:
                    out.append(key)
                    if len(out) >= n:
                        break
        y -= 1
    return out



def _forward_settle(store, hist: list[dict]) -> list[dict]:
    """给满 30 交易日的留档快照补算后视 30 日成绩(池等权 vs 沪深300 同窗)——留档的用途落地:
    说出时点固化,成绩后算不可篡改。窗口=快照日后首个交易日起 30 个交易日(沪深300 日历);
    未满窗/成员价格缺 → None(「未满窗」诚实留白)。价格缓存跨快照复用。"""
    if not hist:
        return hist
    bench = store.get_index_daily_series("000300")
    if bench is None or len(bench) < 40:
        for h in hist:
            h.update(ret30=None, bench30=None, excess30=None)
        return hist
    bdates = [str(d) for d in bench.index]
    bclose = bench["close"].astype(float)
    adj_cache: dict[str, pd.Series] = {}   # 分红前复权 close(除权缺口修正;窗口内收益不失真)
    for h in hist:
        asof = h["asof"]
        idx0 = next((i for i, d in enumerate(bdates) if d > asof), None)
        if idx0 is None or idx0 + 29 >= len(bdates):
            h.update(ret30=None, bench30=None, excess30=None)
            continue
        d0, d1 = bdates[idx0], bdates[idx0 + 29]
        members = store.pool_membership_asof(asof)
        rets = []
        for code in members.index:
            if code not in adj_cache:
                df = store.get_series(code)
                if df is not None and len(df):
                    div = store.get_stock_dividend_series(code)
                    series, _ = pr.dividend_adjusted_close(
                        df["close"].astype(float), div if len(div) else None)
                    adj_cache[code] = series
                else:
                    adj_cache[code] = pd.Series(dtype=float)
            s = adj_cache[code]
            if not len(s):
                continue
            idx = s.index.astype(str)
            sub = s[(idx >= d0) & (idx <= d1)]
            if len(sub) >= 2:
                rets.append(float(sub.iloc[-1]) / float(sub.iloc[0]) - 1.0)
        bsub = bclose[(bclose.index.astype(str) >= d0) & (bclose.index.astype(str) <= d1)]
        bret = float(bsub.iloc[-1]) / float(bsub.iloc[0]) - 1.0 if len(bsub) >= 2 else None
        ret30 = float(sum(rets) / len(rets)) if rets else None
        h.update(ret30=ret30, bench30=bret,
                 excess30=(ret30 - bret) if (ret30 is not None and bret is not None) else None)
    return hist

def build_high_earnings_snapshot(store, config=None, asof: str | None = None,
                                 persist: bool = True) -> dict:
    """装配高业绩池快照(读 store,只读)。asof="YYYY-MM-DD"(回放/测试)|None=今天;
    persist=asof 为今天时默认把池成员追记进 pool_membership(环比 diff/历史回放的底座)。"""
    cfg = config or get_config()
    p = cfg.params.get("stock_pool", {}) or {}
    hcfg = p.get("high_pool", {}) or {}
    ucfg = p.get("universe", {}) or {}
    now = (datetime.strptime(asof, "%Y-%m-%d") if asof else datetime.now())
    asof_str = now.strftime("%Y-%m-%d")

    # ---- 宇宙 + 行业(申万优先[tushare 月更,in/out 带历史],东财回退) ----
    spot = store.latest_stock_spot()
    uni_df = uni.derive_universe(
        spot, exclude_prefixes=tuple(ucfg.get("exclude_name_prefixes",
                                              uni.EXCLUDED_NAME_PREFIXES)))
    ind_cfg = ucfg.get("industry_source", "sw")
    ind_map, class_cfg = store.industry_map(), cfg.industry_class()
    if ind_cfg == "sw" or len(ind_map) == 0:
        sw_map = store.sw_industry_map()
        if len(sw_map):
            ind_map = sw_map
            try:
                class_cfg = cfg.sw_industry_class()   # config/stock_industry_sw.yaml
            except Exception:  # noqa: BLE001 — yaml 缺 → 未映射降级
                class_cfg = {}
    joined = uni.join_industry(uni_df, ind_map, class_cfg)
    cov = uni.industry_coverage(joined)
    codeset = {str(c) for c in joined.index}

    # ---- 三环帧: 最近 3 个报告期(跨期取最新落地环——披露间隙期池由上期环撑着,
    #      这正是状态机语义: Q2 环撑到 Q3 披露落地才换血,无任意批重建时点) ----
    period = cal.current_period(now)
    win_periods = _window_periods(now, 3)
    frames = {}
    for p in win_periods:
        frames[p] = {
            "forecast": _period_frame(store.get_stock_forecast_period(p), codeset),
            "express": _period_frame(store.get_stock_express_period(p), codeset),
            "actual": _period_frame(store.get_stock_report_period(p), codeset),
        }
    bal_frames = {p: store.get_stock_balance_period(p) for p in win_periods}
    ring_order = {"forecast": 0, "express": 1, "actual": 2}

    # ---- 市值过滤(默认关;开=全市场 P80 分位) ----
    mkt_on = bool(hcfg.get("mktcap_filter_on", False))
    mkt_cap_pct = None
    if mkt_on and "mktcap" in joined.columns:
        caps = pd.to_numeric(joined["mktcap"], errors="coerce").dropna()
        if len(caps) > 100:
            mkt_cap_pct = float(caps.quantile(float(hcfg.get("mktcap_filter_pct", 0.80))))

    # ---- 第一段: 三环地板粗筛(批量帧, 归母口径;幸存者再走逐股精筛) ----
    survivors: list[dict] = []
    n_by_ring = {"forecast": 0, "express": 0, "actual": 0}
    n_by_period: dict[str, int] = {}
    for code in joined.index:
        row = joined.loc[code]
        # 跨期找该股最新落地的环(announce ≤ asof;同日取更后环)
        landed = []
        for p in win_periods:
            fr = frames[p]
            for ring_name in ("forecast", "express", "actual"):
                fring = fr[ring_name]
                if code not in fring.index:
                    continue
                r = fring.loc[code]
                ann = str(r.get("announce_date") or "")[:10]
                # 闸门: 公告日在报告期末之前 = 上游错标(预告面板每期~30-40行脏),剔除
                if not ann or ann > asof_str or ann.replace("-", "") < p:
                    continue
                landed.append((ann, ring_order[ring_name], p, ring_name, r))
        if not landed:
            continue
        ann, _o, period_used, ring_name, r = max(landed, key=lambda t: (t[0], t[1]))
        fc_r = r if ring_name == "forecast" else None
        ex_r = r if ring_name == "express" else None
        ac_r = r if ring_name == "actual" else None
        forecast, express, actual = _ring_input(fc_r, ex_r, ac_r)
        active = gt.resolve_active_ring(forecast, express, actual, asof_str)
        if active is None:
            continue
        floor = gt.ring_floor(active, hcfg)
        if not floor["passed"]:
            continue
        if mkt_cap_pct is not None:
            cap = row.get("mktcap")
            if _nan(cap) or float(cap) > mkt_cap_pct:
                continue
        n_by_ring[ring_name] += 1
        n_by_period[period_used] = n_by_period.get(period_used, 0) + 1
        survivors.append({
            "code": str(code), "name": str(row.get("name", code)),
            "industry": row.get("industry", "未映射"), "type": row.get("type"),
            "commodity_variety": row.get("commodity_variety"),
            "mktcap": row.get("mktcap"), "spot_close": row.get("close"),
            "spot_pe_dyn": row.get("pe_dyn"), "spot_pb": row.get("pb"),
            "ring": ring_name, "announce_date": ann, "period_used": period_used,
            "np_yoy_bulk": floor["np_yoy"], "rev_yoy": floor["rev_yoy"],
            "np_axis": floor["np_axis"], "flags": list(floor["flags"]),
        })

    # ---- 第二段: 幸存者逐股懒装配(精筛+估值+风险) ----
    bal_full = {}   # tushare 资产负债明细(入场环报告期,惰性按期取)
    rows: list[dict] = []
    gaps: dict[str, int] = {}
    sina_needed: list[str] = []
    for s in survivors:
        code = s["code"]
        report_rows = store.stock_report_rows_for(code)
        if s["period_used"] not in bal_full:
            try:
                bal_full[s["period_used"]] = store.get_balance_full_period(s["period_used"])
            except Exception:  # noqa: BLE001
                bal_full[s["period_used"]] = pd.DataFrame()
        bf_row = (bal_full[s["period_used"]].loc[code]
                  if len(bal_full[s["period_used"]]) and code in bal_full[s["period_used"]].index
                  else None)
        # 扣非源: tushare fina_indicator 批量(优先) ∪ sina 逐股(期级合并补缺——
        # tushare 本期 profit_dedt 偶有 NaN 源缺,2026-09-13 实测;两源期并集,sina 只补缺期)
        np_ded = store.profit_dedt_map(code) or None
        sina_panel = store.get_stock_financials_panel(code, metrics=["np_deducted", "goodwill"])
        if sina_panel is not None and len(sina_panel) and "np_deducted" in sina_panel.columns:
            sina_ded = {str(k): v for k, v in sina_panel["np_deducted"].dropna().items()}
            if sina_ded:
                np_ded = {**sina_ded, **(np_ded or {})}
        goodwill_series: dict[str, float] = {}
        if sina_panel is not None and len(sina_panel):
            if "np_deducted" in sina_panel.columns and not np_ded:
                np_ded = {str(k): v for k, v in sina_panel["np_deducted"].dropna().items()}
            if "goodwill" in sina_panel.columns:
                gw = sina_panel["goodwill"].dropna()
                goodwill_series = {str(k): v for k, v in gw.items()}
        goodwill_now = None
        # 商誉优先 tushare 资产负债明细(精确+带披露日);sina 序列留作环比激增的历史轴
        # (bf_row 已在上方按 [period_used].loc[code] 取好;勿再用 bal_full.get(code) 覆盖——
        #  bal_full 按报告期 keyed,按代码查恒 None,会把精确口径整体打回 zcfz 代理)
        if bf_row is not None and _num(bf_row.get("goodwill")) is not None:
            known_gw = [k for k in goodwill_series if k <= s["period_used"]]
            if known_gw:
                goodwill_now = goodwill_series[max(known_gw)]  # 序列与明细一致时用序列末值
            else:
                goodwill_now = float(bf_row["goodwill"])
        elif sina_panel is None or not len(sina_panel):
            gaps["精筛腿未拉"] = gaps.get("精筛腿未拉", 0) + 1
            sina_needed.append(code)
        # 正式环: 扣非复核(有 sina 才精化;无=归母回退+未精筛旗,gates 已带)
        if s["ring"] == "actual" and np_ded:
            ded = gt.deducted_yoy(np_ded, s["period_used"])
            if ded is not None:
                s["np_yoy_bulk"] = ded
                s["np_axis"] = "deducted"
                if "未精筛" in s["flags"]:
                    s["flags"] = [f for f in s["flags"] if f != "未精筛"]
        if goodwill_series:
            # 入场环报告期或更早的最新已披露商誉(阶梯: ≤period_used 的最大报告期)
            known = [k for k in goodwill_series if k <= s["period_used"]]
            if known:
                goodwill_now = goodwill_series[max(known)]

        # ---- 估值两轨 ----
        stype = s["type"]
        track = "pb" if stype == "cyclic" else "peg"
        score = None
        score_note = ""
        price_df = store.get_series(code)
        if track == "peg":
            ttm = None
            shares = None
            if len(report_rows) and report_rows["np_abs"].notna().any():
                np_abs = {str(k): v for k, v in report_rows["np_abs"].dropna().items()}
                # TTM 用 ≤asof 已公告的最新期(np_abs 阶梯,point-in-time)
                known = [rp for rp in np_abs
                         if str(report_rows.loc[rp, "announce_date"] or "")[:10] <= asof_str]
                if known:
                    latest = max(known)
                    ttm = vl.ttm_net_profit(np_abs, latest)
                # 股本: spot 市值/现价反推(主) → np_abs/eps 反推(兜底——push2 被拦时
                # 自洽可用,且与 TTM 同源;两列都是累计口径,比值=总股本)
                for rp in sorted(known or [], reverse=True):
                    eps_v = report_rows.loc[rp, "eps"]
                    if not _nan(eps_v) and float(eps_v) > 0:
                        shares = float(np_abs[rp]) / float(eps_v)
                        break
            shares = vl.implied_shares(s["mktcap"], s["spot_close"]) or shares
            price_now = None if _nan(s["spot_close"]) else float(s["spot_close"])
            if price_now is None and price_df is not None and len(price_df):
                price_now = float(price_df["close"].iloc[-1])   # spot 被拦 → 最后日线
            pe = vl.pe_ttm(price_now, shares, ttm)
            if pe is not None and s["np_yoy_bulk"] is not None and s["np_yoy_bulk"] > 0:
                score = vl.peg(pe, float(s["np_yoy_bulk"]))
            s.update(pe_ttm=pe, peg=score, shares_implied=shares)
            if score is None:
                score_note = "估值缺(TTM/市值/价格腿不齐)" if pe is None else "g≤0"
        else:
            if len(report_rows) and report_rows["bvps"].notna().any() \
                    and price_df is not None and len(price_df) >= 120:
                bvps_events = [(str(report_rows.loc[rp, "announce_date"] or "")[:10], str(rp),
                                float(report_rows.loc[rp, "bvps"]))
                               for rp in report_rows.index
                               if not _nan(report_rows.loc[rp, "bvps"])
                               and str(report_rows.loc[rp, "announce_date"] or "")[:10] <= asof_str]
                raw_close = price_df["close"].astype(float)
                pb = vl.pb_series(raw_close, bvps_events)
                pct = vl.pb_percentile(pb)
                s.update(pb_series_len=len(pb), pb_pct=pct)
                score = pct
                if score is None:
                    score_note = "PB 样本不足"
            else:
                s.update(pb_series_len=0, pb_pct=None)
                score_note = "PB 数据缺(bvps/价格腿)"

        # ---- 风险红黄旗(资产负债: tushare 精确表优先 → zcfz 代理回退) ----
        bal_f = bal_frames.get(s["period_used"])
        bal = None
        if bf_row is not None:
            bal = {k: bf_row.get(k) for k in ("monetary_cap", "accounts_receiv",
                                              "total_assets", "total_liab", "st_borr",
                                              "lt_borr", "bond_payable", "goodwill",
                                              "total_cur_assets", "total_cur_liab")}
        elif bal_f is not None and len(bal_f) and code in bal_f.index:
            b = bal_f.loc[code]
            bal = {k: b.get(k) for k in ("cash", "receivables", "total_assets",
                                         "equity", "debt_ratio")}
        rev_abs = None
        if len(report_rows) and s["period_used"] in report_rows.index:
            rev_abs = report_rows.loc[s["period_used"], "rev_abs"]
        flags = rk.risk_flags(bal, goodwill_now, goodwill_series, rev_abs, s["period_used"],
                              extra_yellow=s["flags"], cfg=hcfg)
        if flags["red"]:
            gaps[f"红旗剔除({'+'.join(flags['red'])})"] = gaps.get(
                f"红旗剔除({'+'.join(flags['red'])})", 0) + 1
            continue
        for g in flags["data_gaps"]:
            gaps[g] = gaps.get(g, 0) + 1

        rows.append({**s, "track": track, "score": score, "score_note": score_note,
                     "red": flags["red"], "yellow": flags["yellow"],
                     "metrics": flags["metrics"]})

    # ---- 估值门 → 两轨排名 → 合并 Top-N(无配额;门内才排名,「贵的」不占坑) ----
    top_n = int(hcfg.get("top_n", 100))
    peg_max = float(hcfg.get("peg_max", 1.0))
    pb_pct_max = float(hcfg.get("pb_pct_max", 0.30))
    peg_rows = [r for r in rows if r["track"] == "peg"
                and r["score"] is not None and r["score"] <= peg_max]
    pb_rows = [r for r in rows if r["track"] == "pb"
               and r["score"] is not None and r["score"] <= pb_pct_max]
    peg_rows.sort(key=lambda r: r["score"])       # PEG 升序=便宜优先
    pb_rows.sort(key=lambda r: r["score"])        # PB 分位升序=资产便宜优先
    for i, r in enumerate(peg_rows):
        r["track_rank"] = (i + 1) / max(len(peg_rows), 1)
    for i, r in enumerate(pb_rows):
        r["track_rank"] = (i + 1) / max(len(pb_rows), 1)
    pool = sorted(peg_rows + pb_rows, key=lambda r: r["track_rank"])[:top_n]
    for i, r in enumerate(pool):
        r["rank"] = i + 1

    # ---- 池内市值中位(状态行卡片——体型结构一眼可见;中位抗极端值,陈述事实不倾斜) ----
    caps = sorted(float(r["mktcap"]) for r in pool
                  if r.get("mktcap") is not None and not _nan(r.get("mktcap")))
    if not caps:
        mkt_median = None
    else:
        m = len(caps) // 2
        mkt_median = caps[m] if len(caps) % 2 else (caps[m - 1] + caps[m]) / 2.0

    # ---- 构成 / 涌现 / 时钟 / diff / 历史 ----
    comp = sec.composition(pool)
    emerg = sec.emergent(pool, threshold=0.50)
    dl = cal.formal_deadline(period)
    data_period = max(n_by_period, key=lambda k: n_by_period[k]) if n_by_period else period
    clock = {
        "period": period, "formal_deadline": dl.isoformat(),
        "days_to_formal": (dl - now.date()).days,
        "forecast_open": cal.forecast_open(period).isoformat(),
        "ring_mix": n_by_ring, "period_mix": n_by_period,
        "data_period": data_period,
        "phase": cal.pool_phase(now),
        "theme_windows": list(hcfg.get("theme_windows", []) or []),
    }
    diff = {"entered": [], "exited": []}
    history = store.latest_pool_snapshots(12)
    prior = next((h["asof"] for h in history if h["asof"] < asof_str), None)
    if prior:
        prev_members = store.pool_membership_asof(prior)
        prev_codes = set(prev_members.index)
        cur_codes = {r["code"] for r in pool}
        names = {str(c): str(joined.loc[c]["name"]) if c in joined.index else c
                 for c in cur_codes | prev_codes}
        for c in sorted(cur_codes - prev_codes):
            diff["entered"].append({"code": c, "name": names.get(c, c)})
        for c in sorted(prev_codes - cur_codes):
            diff["exited"].append({"code": c, "name": names.get(c, c)})

    # ---- 池成员留档(live 才写;回放/测试不写) ----
    if persist and asof is None and pool:
        store.insert_pool_membership(asof_str, [
            (r["code"], r["period_used"], r["ring"], r["announce_date"], r["rank"], r["score"])
            for r in pool])
        if prior is None and not history:
            history = store.latest_pool_snapshots(12)
    history = _forward_settle(store, history)

    return {
        "as_of": asof_str,
        "period": period,
        "clock": clock,
        "mktcap_median": mkt_median,
        "universe_stats": cov,
        "spot_date": store.get_meta("last_stock_spot_update", ""),
        "industry_snapshot": store.last_industry_snapshot() or "",
        "n_floor_pass": len(survivors),
        "n_gated_pool": len(pool),
        "n_track_peg": len(peg_rows), "n_track_pb": len(pb_rows),
        "rows": pool,
        "gaps": gaps,
        "sina_needed": sina_needed,
        "composition": comp,
        "emergent": emerg,
        "diff": diff,
        "history": history,
        "conclusion": store.get_meta("high_earnings_pool_conclusion"),
        "cfg_note": {
            "floor_np_yoy": float(hcfg.get("floor_np_yoy", 50.0)),
            "floor_rev_yoy": float(hcfg.get("floor_rev_yoy", 20.0)),
            "top_n": top_n, "peg_max": float(hcfg.get("peg_max", 1.0)),
            "pb_pct_max": float(hcfg.get("pb_pct_max", 0.30)),
            "mktcap_filter_on": mkt_on,
        },
    }
