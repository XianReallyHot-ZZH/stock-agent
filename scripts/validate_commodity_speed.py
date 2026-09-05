"""A 类快腿 event-study(只读诊断):商品 20 日动量 → 映射个股前向超额收益,实证"快信号有没有 edge"。

2026-09 商品看板提速改版(Q1-Q7 设计树)引入快腿:20日动量 ≥±10% + 60日新高/新低,进 🚦 横幅。
本脚本回答:商品动量触发后,映射周期股是否真的跑赢观察池等权(研究排序价值)——按仓库纪律,
观察项可先上,但必须有可证伪结论挂着(同 地量/支撑位/M2拐点 礼遇流程)。

设计(2026-09-05 预登记,git 历史即注册证明):
  事件臂 = 商品 20日涨幅 ≥ +10%(向上)/ ≤ -10%(向下),逐日 point-in-time(只用过去 20 日),
           每品种事件后 120 交易日冷却(同 validate_deviation_extreme)。
  结果   = 映射个股(commodity_map ∩ 有日线)事件后 10/20/60 日收益 − 观察池等权同窗收益(STOCK_WATCHLIST)。
  基线   = 无条件:同一批映射个股在**全部交易日**的同样本超额分布("随便哪天"对照)。
  判据   = 20日超额中位差 ≥ +1pp 且胜率差 ≥ +3pp → "有信号价值";否则"温度计非开关"。

Usage:
  python scripts/validate_commodity_speed.py                # 默认阈值 ±10%/20日
  python scripts/validate_commodity_speed.py --mom 0.15     # 更严的动量阈
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from stockagent.config import get_config
from stockagent.data.manager import DataManager
from stockagent.data.store import Store

COOLDOWN = 120          # 每品种事件后冷却(交易日)
HORIZONS = (10, 20, 60)  # 前向窗口(交易日)
_MOM_KEY = "commodity_momentum20"
_PAL = {"up": "#d03b3b", "dn": "#1c5cab", "base": "#898781", "ink": "#0b0b0b"}


def _momentum_events(px: pd.Series, mom_th: float) -> pd.DataFrame:
    """单品种:逐日 20 日动量 → 事件帧 [pos, date, momentum, arm],120 日冷却。

    point-in-time:第 t 日动量只用 t-20..t;冷却从事件日起算(期间双向都不再触发)。"""
    s = px.astype(float).dropna()
    if len(s) < 21:
        return pd.DataFrame(columns=["pos", "date", "momentum", "arm"])
    mom = s / s.shift(20) - 1.0
    rows, last_event = [], -10**9
    for pos in range(20, len(s)):
        m = mom.iloc[pos]
        if pd.isna(m):
            continue
        if pos - last_event < COOLDOWN:
            continue
        if m >= mom_th:
            arm = "up"
        elif m <= -mom_th:
            arm = "dn"
        else:
            continue
        rows.append({"pos": pos, "date": s.index[pos], "momentum": float(m), "arm": arm})
        last_event = pos
    return pd.DataFrame(rows)


def _fwd(close: pd.Series, master: list[str], t_pos: int, h: int) -> float | None:
    """master 日历第 t_pos 日起 h 日收益(停牌日 ffill 对齐;窗口尾部不足 → None)。"""
    n = len(master)
    if t_pos + h >= n:
        return None
    p0 = close.get(master[t_pos])
    p1 = close.get(master[t_pos + h])
    if p0 is None or p1 is None or pd.isna(p0) or pd.isna(p1) or float(p0) <= 0:
        return None
    return float(p1) / float(p0) - 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mom", type=float, default=0.10, help="20日动量触发阈(默认 0.10)")
    ap.add_argument("--out", default="data/commodity_speed_study.html")
    args = ap.parse_args()

    cfg = get_config()
    st = Store(cfg.db_path)
    commodity_map = (cfg.params.get("stock") or {}).get("commodity_map") or {}
    pool = list(DataManager.STOCK_WATCHLIST)

    # 映射个股:commodity_map ∩ 有日线;池:观察池 ∩ 有日线
    stock_close: dict[str, pd.Series] = {}
    for code in sorted(set(list(commodity_map.keys()) + pool)):
        px = st.get_series(code)
        if px is not None and len(px) > 0 and "close" in px.columns:
            c = px["close"].astype(float).dropna()
            if len(c) > 0:
                stock_close[code] = c
    mapped_stocks = {c for c in commodity_map if c in stock_close}
    pool_close = {c: stock_close[c] for c in pool if c in stock_close}
    if not mapped_stocks or len(pool_close) < 5:
        print("数据不足:映射个股或观察池日线缺失")
        sys.exit(1)

    # 主日历 = 观察池所有股票日期的并集(升序)
    master = sorted({d for c in pool_close.values() for d in c.index})
    pool_ff = {c: s.reindex(master).ffill() for c, s in pool_close.items()}

    def pool_fwd(t: int, h: int) -> float | None:
        rets = [_fwd(s, master, t, h) for s in pool_ff.values()]
        rets = [r for r in rets if r is not None]
        return float(np.mean(rets)) if rets else None

    # 事件收集(逐品种)
    events: list[dict] = []
    per_variety: list[tuple[str, int, int]] = []
    for variety in DataManager.COMMODITY_VARIETIES:
        px = st.get_commodity_series(variety)
        ev = _momentum_events(px, args.mom)
        if len(ev) == 0:
            per_variety.append((variety, 0, 0))
            continue
        codes = sorted({c for c, var in commodity_map.items() if var == variety} & mapped_stocks)
        if not codes:
            per_variety.append((variety, 0, 0))
            continue
        # 事件日 → 主日历位置(品种日期与股票日历基本重合;取 ≤ 事件日的最后主日历日)
        pos_of = {d: i for i, d in enumerate(master)}
        for _, r in ev.iterrows():
            t = pos_of.get(r["date"])
            if t is None:
                cand = [p for d, p in pos_of.items() if d <= r["date"]]
                if not cand:
                    continue
                t = max(cand)
            rets20 = [_fwd(stock_close[c], master, t, 20) for c in codes]
            rets20 = [x for x in rets20 if x is not None]
            events.append({"date": r["date"], "variety": variety, "arm": r["arm"],
                           "momentum": r["momentum"], "n_stocks": len(rets20),
                           "stock_mean_20": float(np.mean(rets20)) if rets20 else None})
        n_up = int((ev["arm"] == "up").sum())
        per_variety.append((variety, n_up, len(ev) - n_up))

    ev_df = pd.DataFrame(events)
    if len(ev_df) == 0:
        print(f"无事件:±{args.mom:.0%}/20日 在样本期内未触发(降低 --mom 再试)")
        sys.exit(1)

    # 股票级样本:(事件, 映射股) 前向超额;基线 = 同一批股全部交易日
    def excess_samples(arms: tuple[str, ...] | None, h: int) -> list[float]:
        out = []
        pf_cache: dict[int, float | None] = {}
        for _, r in ev_df.iterrows():
            if arms and r["arm"] not in arms:
                continue
            codes = sorted({c for c, var in commodity_map.items()
                            if var == r["variety"]} & mapped_stocks)
            t = pos_of.get(r["date"])
            if t is None:
                continue
            if t not in pf_cache:
                pf_cache[t] = pool_fwd(t, h)
            pf = pf_cache[t]
            if pf is None:
                continue
            for c in codes:
                sr = _fwd(stock_close[c], master, t, h)
                if sr is not None:
                    out.append(sr - pf)
        return out

    pos_of = {d: i for i, d in enumerate(master)}
    base_samples = {h: [] for h in HORIZONS}
    for t in range(0, len(master) - max(HORIZONS), 5):   # 每 5 日抽样("随便哪天")
        pf_cache = {h: pool_fwd(t, h) for h in HORIZONS}
        for c in mapped_stocks:
            for h in HORIZONS:
                sr = _fwd(stock_close[c], master, t, h)
                pf = pf_cache[h]
                if sr is not None and pf is not None:
                    base_samples[h].append(sr - pf)

    def stats(samples: list[float]) -> tuple[float, float, int]:
        if not samples:
            return float("nan"), float("nan"), 0
        return float(np.mean([x > 0 for x in samples])), float(np.median(samples)), len(samples)

    print(f"=== A 类快腿 event-study (20日动量 ±{args.mom:.0%} · 冷却{COOLDOWN}日) ===")
    print(f"事件 {len(ev_df)} 个(向上 {int((ev_df['arm'] == 'up').sum())} / 向下 "
          f"{int((ev_df['arm'] == 'dn').sum())}) · 映射股 {len(mapped_stocks)} · 池 {len(pool_close)}")
    rows_html = ("<tr><th style='text-align:left'>臂</th><th>窗口</th><th>n(事件×股)</th>"
                 "<th>胜率</th><th>超额中位</th></tr>")
    concl_rows = {}
    for arm, label in (("up", "向上(动量≥+)"), ("dn", "向下(动量≤−)"), (None, "全部事件")):
        for h in HORIZONS:
            wr, med, n = stats(excess_samples(None if arm is None else (arm,), h))
            b_wr, b_med, b_n = stats(base_samples[h])
            rows_html += (f"<tr><td style='text-align:left'>{label}</td><td>{h}日</td><td>{n}</td>"
                          f"<td>{wr:.0%} <span style='color:gray'>(基线 {b_wr:.0%})</span></td>"
                          f"<td>{med:+.2%} <span style='color:gray'>(基线 {b_med:+.2%})</span></td></tr>")
            if h == 20:
                concl_rows[arm or "all"] = (wr, med, b_wr, b_med, n)
    # 结论(20日窗口·判据预登记:中位差≥+1pp 且胜率差≥+3pp → 有信号价值)
    wr, med, b_wr, b_med, n = concl_rows["all"]
    has_edge = (med - b_med >= 0.01) and (wr - b_wr >= 0.03)
    verdict = "**有信号价值**(判据达标:20日超额中位差≥+1pp 且胜率差≥+3pp)" if has_edge \
        else "**温度计非开关**(20日超额中位差<+1pp 或胜率差<+3pp——快腿用于研究排队,不构成择时依据)"
    concl = (f"商品20日动量≥±{args.mom:.0%}(冷却{COOLDOWN}日,n={n} 事件×股)后映射个股 20 日超额 "
             f"中位 {med:+.2%}(胜率 {wr:.0%}) vs 无条件基线 {b_med:+.2%}({b_wr:.0%}) → {verdict}")

    # ---- 直方图:20日超额分布(双臂 vs 基线) ----
    fig = go.Figure()
    for arm, label, color in (("up", "向上臂", _PAL["up"]), ("dn", "向下臂", _PAL["dn"])):
        xs = excess_samples((arm,), 20)
        fig.add_trace(go.Histogram(x=[x * 100 for x in xs], name=f"{label} 20日超额",
                                   marker_color=color, opacity=0.55,
                                   hovertemplate="%{x:.1f}%: %{y}次<extra>" + label + "</extra>"))
    bx = [x * 100 for x in base_samples[20]]
    fig.add_trace(go.Histogram(x=bx, name="无条件基线", marker_color=_PAL["base"],
                               opacity=0.35, hovertemplate="%{x:.1f}%: %{y}次<extra>基线</extra>"))
    fig.update_layout(barmode="overlay", height=380,
                      title="事件后 20 日超额收益分布(vs 观察池等权):双臂 vs 无条件基线",
                      xaxis_title="20日超额(%)", yaxis_title="次数")
    fig_html = fig.to_html(full_html=False, include_plotlyjs=True)

    # ---- 事件明细表(最近 20 条) ----
    ev_sorted = ev_df.sort_values("date", ascending=False).head(20)
    detail_rows = ""
    for _, r in ev_sorted.iterrows():
        mean20 = "—" if r["stock_mean_20"] is None else f"{r['stock_mean_20']:+.1%}"
        arm_txt = "向上" if r["arm"] == "up" else "向下"
        arm_color = _PAL["up"] if r["arm"] == "up" else _PAL["dn"]
        detail_rows += (f"<tr><td>{r['date']}</td><td>{r['variety']}</td>"
                        f"<td style='color:{arm_color}'>{arm_txt} {r['momentum']:+.0%}</td>"
                        f"<td>{r['n_stocks']}</td><td>{mean20}</td></tr>")
    per_rows = "".join(f"<tr><td>{v}</td><td>{up}</td><td>{dn}</td></tr>" for v, up, dn in per_variety if up + dn)

    html = (f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>商品动量快腿 event-study</title><style>"
            f"body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px}}"
            f"h1{{font-size:21px}} h2{{font-size:16px;margin:18px 0 8px;color:#52514e}}"
            f".meta{{color:#52514e;font-size:13px}} .hint{{background:#f5f4f0;padding:10px;"
            f"border-radius:6px;font-size:13px;line-height:1.7}}"
            f"table{{border-collapse:collapse;width:100%;font-size:13px}}"
            f"th,td{{padding:6px 8px;border-bottom:1px solid #e1e0d9;text-align:center}}</style></head><body>"
            f"<h1>商品动量快腿 event-study</h1>"
            f"<div class='meta'>🚦 横幅快腿的实证 · 触发=20日动量≥±{args.mom:.0%} · 冷却 {COOLDOWN} 交易日 · "
            f"映射股 {len(mapped_stocks)} · 观察池等权基准 {len(pool_close)} 股</div>"
            f"<div class='hint'><b>结论(20日窗口):</b> {concl}</div>"
            f"<div class='hint'>事件=品种 20日动量过阈;每品种冷却 {COOLDOWN} 日防同一波行情重复计数。"
            f"超额=映射个股前向收益 − 观察池等权同窗收益(消市场β)。判据在跑数前预登记。</div>"
            f"<h2>臂 × 窗口 胜率/超额中位</h2><table><tr><th style='text-align:left'>臂</th>"
            f"<th>窗口</th><th>n</th><th>胜率</th><th>超额中位</th></tr>"
            f"{rows_html}</table>"
            f"<h2>20日超额分布</h2>{fig_html}"
            f"<h2>各品种事件数(向上/向下)</h2><table>{per_rows}</table>"
            f"<h2>最近事件(20 条 · 股均=映射股 20 日收益等权均值,未扣基线)</h2>"
            f"<table><tr><th>日期</th><th>品种</th><th>事件</th><th>映射股数</th><th>股均20日</th></tr>"
            f"{detail_rows}</table>"
            f"<p class='meta'>观察非信号(温度计非开关) · 永不喂交易引擎 · 生成于 "
            f"{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}</p></body></html>")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    st.set_meta("commodity_speed_conclusion", concl)
    print(f"\n{concl}")
    print(f"\n报告 -> {out}\n结论已写 meta(commodity_speed_conclusion) → 个股看板 🚦 横幅活注入")


if __name__ == "__main__":
    main()
