"""基差/期限结构 event-study(只读诊断,二期剩余·礼遇):极端基差与期限斜率后,商品本身涨不涨。

数据:commodity_basis(100ppi 生意社,2019 起,dom_basis_rate=主力(期货−现货)/现货;
term_slope=主力/近月−1 年化,正=contango 远月升水/负=backwardation 现货紧)。
结果 = 事件后 20/60 日**品种主连**收益(close-to-close,主连拼接未复权——换月跳空会污染,
读中位/胜率并如实标注);基线 = 同品种全部交易日抽样。
判据(跑数前预登记,同 speed-study):60日中位差 ≥ +1pp 且胜率差 ≥ +3pp → 该臂有信号价值;
否则温度计非开关。冷却 60 交易日/品种/族(基差族与期限族各自独立冷却)。

Usage:
  python scripts/validate_commodity_basis.py                # 默认分位臂 10%/90%
  python scripts/validate_commodity_basis.py --lo 0.05 --hi 0.95
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from stockagent.commodity import fundamentals as fund
from stockagent.config import get_config
from stockagent.data import Store, fetcher

HORIZONS = (20, 60)
COOLDOWN = 60
_PAL = {"back": "#1c5cab", "cont": "#d03b3b", "base": "#898781"}


def _arms(feat: pd.DataFrame, lo: float, hi: float) -> pd.DataFrame:
    """事件帧:基差族(深贴水/深升水)与期限族(贴水结构/深contango),族内共享冷却。"""
    rows = []
    fam_basis = (feat["basis_pct"] <= lo) | (feat["basis_pct"] >= hi)
    fam_basis = fund.cooldown_mask(fam_basis.fillna(False), COOLDOWN)
    fam_term = (feat["term_pct"] <= lo) | (feat["term_pct"] >= hi)
    fam_term = fund.cooldown_mask(fam_term.fillna(False), COOLDOWN)
    for d, r in feat.iterrows():
        if fam_basis.get(d, False) and r["basis_pct"] <= lo:
            rows.append({"date": d, "arm": "deep_back", "value": r["dom_basis_rate"]})
        elif fam_basis.get(d, False) and r["basis_pct"] >= hi:
            rows.append({"date": d, "arm": "deep_contango", "value": r["dom_basis_rate"]})
        if fam_term.get(d, False) and r["term_pct"] <= lo:
            rows.append({"date": d, "arm": "term_back", "value": r["term_slope"]})
        elif fam_term.get(d, False) and r["term_pct"] >= hi:
            rows.append({"date": d, "arm": "term_steep", "value": r["term_slope"]})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lo", type=float, default=0.10, help="下臂分位阈(默认 0.10)")
    ap.add_argument("--hi", type=float, default=0.90, help="上臂分位阈(默认 0.90)")
    ap.add_argument("--out", default="data/commodity_basis_study.html")
    args = ap.parse_args()

    st = Store(get_config().db_path)
    if not st.last_commodity_basis_date():
        print("commodity_basis 空:先 python scripts/backfill_commodity.py --basis")
        sys.exit(1)

    samples = {a: {h: [] for h in HORIZONS} for a in
               ("deep_back", "deep_contango", "term_back", "term_steep")}
    base_samples = {h: [] for h in HORIZONS}
    per_var: list[tuple[str, str, int]] = []
    n_events = {a: 0 for a in samples}

    for variety, code in fetcher.COMMODITY_CODES.items():
        bdf = st.get_commodity_basis(code)
        px = st.get_commodity_series(variety)
        if bdf is None or len(bdf) < 300 or px is None or len(px) < 300:
            continue
        feat = pd.DataFrame({
            "dom_basis_rate": pd.to_numeric(bdf["dom_basis_rate"], errors="coerce"),
            "term_slope": fund.term_slope_annualized(bdf),
        })
        feat["basis_pct"] = fund.expanding_pct(feat["dom_basis_rate"], 250)
        feat["term_pct"] = fund.expanding_pct(feat["term_slope"], 250)
        px = px.astype(float)
        pos_of = {d: i for i, d in enumerate(px.index)}

        def _fwd(date, h):
            t = pos_of.get(date)
            if t is None:
                cand = [p for d, p in pos_of.items() if d <= date]
                if not cand:
                    return None
                t = max(cand)
            if t + h >= len(px) or px.iloc[t] <= 0:
                return None
            return float(px.iloc[t + h]) / float(px.iloc[t]) - 1.0

        ev = _arms(feat.dropna(subset=["basis_pct", "term_pct"], how="all"), args.lo, args.hi)
        for _, r in ev.iterrows():
            for h in HORIZONS:
                v = _fwd(r["date"], h)
                if v is not None:
                    samples[r["arm"]][h].append(v)
            n_events[r["arm"]] += 1
        cnt = ev["arm"].value_counts() if len(ev) else {}
        for a, n in cnt.items():
            per_var.append((variety, a, int(n)))
        # 基线:同品种每 5 个基差日抽一天
        fdrop = feat.dropna(subset=["dom_basis_rate"])
        for i in range(0, len(fdrop), 5):
            d = fdrop.index[i]
            for h in HORIZONS:
                v = _fwd(d, h)
                if v is not None:
                    base_samples[h].append(v)

    def stats(xs):
        if not xs:
            return float("nan"), float("nan"), 0
        return float(np.mean([x > 0 for x in xs])), float(np.median(xs)), len(xs)

    arm_label = {"deep_back": "深贴水(基差分位≤lo)", "deep_contango": "深升水(≥hi)",
                 "term_back": "贴水结构(期限分位≤lo)", "term_steep": "深contango(≥hi)"}
    print(f"=== 基差/期限结构 event-study (臂分位 {args.lo:.0%}/{args.hi:.0%} · 冷却{COOLDOWN}日) ===")
    rows_html = ("<tr><th style='text-align:left'>臂</th><th>窗口</th><th>n</th><th>胜率</th>"
                 "<th>中位收益</th></tr>")
    concl_bits, best = [], None
    for a in samples:
        for h in HORIZONS:
            wr, med, n = stats(samples[a][h])
            b_wr, b_med, _ = stats(base_samples[h])
            if n:
                rows_html += (f"<tr><td style='text-align:left'>{arm_label[a]}</td><td>{h}日</td>"
                              f"<td>{n}</td><td>{wr:.0%} <span style='color:gray'>(基线 {b_wr:.0%})</span></td>"
                              f"<td>{med:+.2%} <span style='color:gray'>(基线 {b_med:+.2%})</span></td></tr>")
            if h == 60 and n:
                edge = med - b_med
                if best is None or edge > best[1]:
                    best = (a, edge, wr, med, b_wr, b_med, n)
                concl_bits.append(f"{arm_label[a]}60日中位{med:+.1%}(基线{b_med:+.1%},胜率{wr:.0%}vs{b_wr:.0%},n={n})")

    if best is None:
        print("无事件(分位臂未触发或历史不足 250 日)")
        sys.exit(1)
    a, edge, wr, med, b_wr, b_med, n = best
    has_edge = (edge >= 0.01) and (wr - b_wr >= 0.03)
    verdict = (f"**{arm_label[a]}臂有信号价值**(60日中位差{edge*100:+.1f}pp≥+1pp 且胜率差{wr-b_wr:+.0%}≥+3pp)"
               if has_edge else
               f"**温度计非开关**(最强臂={arm_label[a]},60日中位差{edge*100:+.1f}pp/胜率差{wr-b_wr:+.0%},未达预登记判据)")
    concl = (f"基差/期限结构极端臂(100ppi 2019起,expanding 分位防前视,冷却{COOLDOWN}日):"
             + ";".join(concl_bits) + f" → {verdict}")

    per_rows = "".join(f"<tr><td>{v}</td><td>{arm_label[a]}</td><td>{n}</td></tr>"
                       for v, a, n in per_var)
    html = (f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>基差/期限结构 event-study</title><style>"
            f"body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px}}"
            f"h1{{font-size:21px}} h2{{font-size:16px;margin:18px 0 8px;color:#52514e}}"
            f".meta{{color:#52514e;font-size:13px}} .hint{{background:#f5f4f0;padding:10px;"
            f"border-radius:6px;font-size:13px;line-height:1.7}}"
            f"table{{border-collapse:collapse;width:100%;font-size:13px}}"
            f"th,td{{padding:6px 8px;border-bottom:1px solid #e1e0d9;text-align:center}}</style></head><body>"
            f"<h1>基差/期限结构 event-study</h1>"
            f"<div class='meta'>二期剩余·礼遇 · 100ppi 基差表 2019 起 · 臂分位 {args.lo:.0%}/{args.hi:.0%} · "
            f"expanding 分位(无前视) · 冷却 {COOLDOWN} 交易日</div>"
            f"<div class='hint'><b>结论:</b> {concl}</div>"
            f"<div class='hint'>基差率=(主力期货−现货)/现货(正=升水);期限斜率=主力/近月−1 年化"
            f"(正=contango/负=backwardation)。结果=品种主连 20/60 日收益——<b>主连拼接未复权,"
            f"换月跳空会污染个别读数</b>,故看中位/胜率。判据跑数前预登记。</div>"
            f"<h2>臂 × 窗口</h2><table>{rows_html}</table>"
            f"<h2>各品种事件数</h2><table><tr><th>品种</th><th>臂</th><th>n</th></tr>{per_rows}</table>"
            f"<p class='meta'>观察非信号 · 永不喂交易引擎 · 生成于 "
            f"{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}</p></body></html>")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    st.set_meta("commodity_basis_conclusion", concl)
    print(f"\n{concl}")
    print(f"\n报告 -> {out}\n结论已写 meta(commodity_basis_conclusion) → 大宗商品看板 🔬 注入")


if __name__ == "__main__":
    main()
