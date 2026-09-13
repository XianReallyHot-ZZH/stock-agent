"""库存(交割仓单)event-study(只读诊断,二期剩余·礼遇):库存极位/去化累库后,商品本身涨不涨。

覆盖(批次2.3 扩界·2026-09-13,边界显式不静默混口径):
  - CZCE 三品种(玻璃FG/纯碱SA/尿素UR)=郑商所日报「总计」口径(张),周采样 2021 起,akshare 源
    (2026-09-13 修 3× 小计/总计高估——分位/环比比例不变,本验证结论不受影响);
  - WSR 八品种(铜CU/铝AL/锌ZN/螺纹钢RB=SHFE·原油SC=INE·金AU/银AG=SHFE·碳酸锂LC=GFEX)
    =tushare fut_wsr 仓库和(吨/千克/桶/手随品种,铜含完税+保税两段),周采样 2019 起(LC 2024 起)。
  单位随品种但**分析为品种内自身分位/环比**,无跨品种量纲比较;DCE 品种(铁矿/焦煤/豆粕/玉米/生猪/LPG)无源。
臂(预登记):库存分位 ≤10%(低库存)/ ≥90%(高库存);4周库存变化 ≤−8%(去化)/ ≥+8%(累库)。
分位 expanding(周频 min_history=100≈2年,无前视);冷却 8 周;结果=品种主连 20/60 日收益 vs 全日抽样基线
——批次2.4(2026-09-13)起前向收益用**展期调整指数**(fut_mapping 换月映射+逐合约收盘,
换月日收益按旧合约计;无映射/回退日诚实注记)。
判据(同 speed/basis 礼遇):60日中位差 ≥ +1pp 且胜率差 ≥ +3pp → 有信号价值;否则温度计非开关。

Usage:
  python scripts/validate_commodity_inventory.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from stockagent.commodity import fundamentals as fund
from stockagent.commodity import panel
from stockagent.config import get_config
from stockagent.data import Store, fetcher

HORIZONS = (20, 60)
COOLDOWN_WEEKS = 8
MIN_HIST = 100            # 周频 ~2 年
W4_TH = 0.08              # 4 周库存变化臂阈(预登记)
ARMS = ("inv_low", "inv_high", "drawdown", "buildup")
ARM_LABEL = {"inv_low": "低库存(分位≤10%)", "inv_high": "高库存(≥90%)",
             "drawdown": "去化(4周≤−8%)", "buildup": "累库(4周≥+8%)"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/commodity_inventory_study.html")
    args = ap.parse_args()

    st = Store(get_config().db_path)
    code2var = {c: v for v, c in fetcher.COMMODITY_CODES.items()}
    samples = {a: {h: [] for h in HORIZONS} for a in ARMS}
    base_samples = {h: [] for h in HORIZONS}
    per_var: list[tuple[str, str, int]] = []
    n_events = {a: 0 for a in ARMS}
    n_roll_fallback: list = []     # 批次2.4:换月日旧约收盘缺→回退主连原生收益的次数(按品种)

    for code in list(fetcher.CZCE_INVENTORY_SYMBOLS) + list(fetcher.WSR_INVENTORY_SYMBOLS):
        variety = code2var.get(code)
        inv = st.get_commodity_inventory(code)
        # 批次2.4 展期口径:前向收益用换月调整指数(无映射回退主连原生,n_fb 聚合进注记)
        px, n_fb = (panel.roll_adjusted_from_store(st, variety, code) if variety
                    else (None, None))
        n_roll_fallback.append(n_fb)
        if inv is None or len(inv) < MIN_HIST + 20 or px is None or len(px) < 300:
            continue
        inv = inv.astype(float)
        inv.index = pd.to_datetime(inv.index).strftime("%Y-%m-%d")
        inv = inv.sort_index()
        feat = pd.DataFrame({"volume": inv})
        feat["inv_pct"] = fund.expanding_pct(inv, MIN_HIST)
        feat["w4"] = inv.pct_change(4)
        px = px.astype(float)
        pos_of = {d: i for i, d in enumerate(px.index)}

        def _fwd(date, h):
            cand = [p for d, p in pos_of.items() if d <= date]
            if not cand:
                return None
            t = max(cand)
            if t + h >= len(px) or px.iloc[t] <= 0:
                return None
            return float(px.iloc[t + h]) / float(px.iloc[t]) - 1.0

        # 两族各自冷却(分位族/动量族)
        fam_pct = ((feat["inv_pct"] <= 0.10) | (feat["inv_pct"] >= 0.90)).fillna(False)
        fam_pct = fund.cooldown_mask(fam_pct, COOLDOWN_WEEKS)
        fam_w4 = ((feat["w4"] <= -W4_TH) | (feat["w4"] >= W4_TH)).fillna(False)
        fam_w4 = fund.cooldown_mask(fam_w4, COOLDOWN_WEEKS)
        ev_rows = []
        for d, r in feat.iterrows():
            if fam_pct.get(d, False) and not pd.isna(r["inv_pct"]):
                if r["inv_pct"] <= 0.10:
                    ev_rows.append({"date": d, "arm": "inv_low"})
                elif r["inv_pct"] >= 0.90:
                    ev_rows.append({"date": d, "arm": "inv_high"})
            if fam_w4.get(d, False) and not pd.isna(r["w4"]):
                if r["w4"] <= -W4_TH:
                    ev_rows.append({"date": d, "arm": "drawdown"})
                elif r["w4"] >= W4_TH:
                    ev_rows.append({"date": d, "arm": "buildup"})
        for r in ev_rows:
            for h in HORIZONS:
                v = _fwd(r["date"], h)
                if v is not None:
                    samples[r["arm"]][h].append(v)
            n_events[r["arm"]] += 1
        cnt = pd.Series([r["arm"] for r in ev_rows]).value_counts() if ev_rows else {}
        for a, n in cnt.items():
            per_var.append((variety, a, int(n)))
        for i in range(0, len(feat), 2):        # 基线:每 2 周抽一天
            for h in HORIZONS:
                v = _fwd(feat.index[i], h)
                if v is not None:
                    base_samples[h].append(v)

    def stats(xs):
        if not xs:
            return float("nan"), float("nan"), 0
        return float(np.mean([x > 0 for x in xs])), float(np.median(xs)), len(xs)

    if not any(n_events.values()):
        print("无事件(库存史不足或臂未触发):先 backfill_commodity.py --inv 攒史")
        sys.exit(1)

    print(f"=== 库存 event-study (CZCE 3 品种总计口径 + fut_wsr 8 品种仓库和 · 周采样 · 冷却{COOLDOWN_WEEKS}周 · 4周阈±{W4_TH:.0%}) ===")
    print("事件数:", {ARM_LABEL[a]: n for a, n in n_events.items()})
    rows_html = ("<tr><th style='text-align:left'>臂</th><th>窗口</th><th>n</th><th>胜率</th>"
                 "<th>中位收益</th></tr>")
    concl_bits, best = [], None
    for a in ARMS:
        for h in HORIZONS:
            wr, med, n = stats(samples[a][h])
            b_wr, b_med, _ = stats(base_samples[h])
            if n:
                rows_html += (f"<tr><td style='text-align:left'>{ARM_LABEL[a]}</td><td>{h}日</td>"
                              f"<td>{n}</td><td>{wr:.0%} <span style='color:gray'>(基线 {b_wr:.0%})</span></td>"
                              f"<td>{med:+.2%} <span style='color:gray'>(基线 {b_med:+.2%})</span></td></tr>")
            if h == 60 and n:
                edge = med - b_med
                if best is None or edge > best[1]:
                    best = (a, edge, wr, med, b_wr, b_med, n)
                concl_bits.append(f"{ARM_LABEL[a]}60日中位{med:+.1%}(基线{b_med:+.1%},胜率{wr:.0%}vs{b_wr:.0%},n={n})")

    a, edge, wr, med, b_wr, b_med, n = best
    has_edge = (edge >= 0.01) and (wr - b_wr >= 0.03)
    verdict = (f"**{ARM_LABEL[a]}臂有信号价值**(60日中位差{edge*100:+.1f}pp≥+1pp 且胜率差{wr-b_wr:+.0%}≥+3pp)"
               if has_edge else
               f"**温度计非开关**(最强臂={ARM_LABEL[a]},60日中位差{edge*100:+.1f}pp/胜率差{wr-b_wr:+.0%},未达判据)")
    n_fb_total = sum(x for x in n_roll_fallback if x)
    n_nomap = sum(1 for x in n_roll_fallback if x is None)
    concl = (f"库存臂(CZCE 3品种总计口径2021起+fut_wsr 8品种仓库和2019起·碳酸锂2024,expanding分位防前视,冷却{COOLDOWN_WEEKS}周"
             f";前向收益=展期调整指数(批次2.4,回退日{n_fb_total}·无映射品种{n_nomap}):"
             + ";".join(concl_bits) + f" → {verdict}(DCE 品种无源;SHFE 含完税+保税两段;结论外推需谨慎)")

    per_rows = "".join(f"<tr><td>{v}</td><td>{ARM_LABEL[a]}</td><td>{n}</td></tr>" for v, a, n in per_var)
    html = (f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>库存 event-study</title><style>"
            f"body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px}}"
            f"h1{{font-size:21px}} h2{{font-size:16px;margin:18px 0 8px;color:#52514e}}"
            f".meta{{color:#52514e;font-size:13px}} .hint{{background:#f5f4f0;padding:10px;"
            f"border-radius:6px;font-size:13px;line-height:1.7}}"
            f"table{{border-collapse:collapse;width:100%;font-size:13px}}"
            f"th,td{{padding:6px 8px;border-bottom:1px solid #e1e0d9;text-align:center}}</style></head><body>"
            f"<h1>库存(交割仓单)event-study</h1>"
            f"<div class='meta'>二期剩余·礼遇 · CZCE 3 品种(总计口径,2021 起)+ fut_wsr 8 品种(仓库和,2019 起)· 周采样 · "
            f"expanding 分位(无前视) · 冷却 {COOLDOWN_WEEKS} 周 · 4周变化臂阈 ±{W4_TH:.0%}</div>"
            f"<div class='hint'><b>结论:</b> {concl}</div>"
            f"<div class='hint'>仓单=交易所交割仓库口径(≠社会总库存,只反映可交割边际);"
            f"结果=品种主连 20/60 日收益(拼接未复权,看中位/胜率)。口径边界(批次2.3):CZCE=郑商所日报「总计」"
            f"(2026-09-13 前历史曾被小计/总计行 3× 高估,已重灌修正;比例不变故本验证历次结论可比);"
            f"SHFE/INE/GFEX=tushare fut_wsr 仓库和(单位随品种,SHFE 含完税+保税两段,品种内自身分位无跨品种比较);"
            f"DCE 品种(铁矿/焦煤/豆粕/玉米/生猪/LPG)无源。判据跑数前预登记。</div>"
            f"<h2>臂 × 窗口</h2><table>{rows_html}</table>"
            f"<h2>各品种事件数</h2><table><tr><th>品种</th><th>臂</th><th>n</th></tr>{per_rows}</table>"
            f"<p class='meta'>观察非信号 · 永不喂交易引擎 · 生成于 "
            f"{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}</p></body></html>")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    st.set_meta("commodity_inventory_conclusion", concl)
    print(f"\n{concl}")
    print(f"\n报告 -> {out}\n结论已写 meta(commodity_inventory_conclusion) → 大宗商品看板 🔬 注入")


if __name__ == "__main__":
    main()
