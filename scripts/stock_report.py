"""个股诊断报告 CLI — Phase 2 交付通道。

跑全套个股诊断(三类判定 + 估值zone + E3 + S07 归因 + S10 戴维斯 + S08 避坑 + 预告链)
→ 渲染交互式 HTML 看板(告警区 + 个股卡片) + 控制台摘要。--push-alerts 推送信号提醒到微信/飞书。
只读旁路,不碰交易引擎(同 research/tracker)。

Usage:
  python scripts/stock_report.py                         # 观察池 → data/stock_diagnose.html
  python scripts/stock_report.py --codes 600519,300750   # 指定个股
  python scripts/stock_report.py --as-of 2026-06-30
  python scripts/stock_report.py --push-alerts           # 生成 + 推送提醒(触发时)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.data.manager import DataManager
from stockagent.tracker import stock_diagnose as sd
from stockagent.tracker import stock_report as srep
from stockagent.tracker import alerts as talerts
from stockagent.utils.logging_setup import setup_logging

# 观察池显示名 → DataManager.STOCK_NAMES(server/scripts 共享;C1 迁 stock_pool.yaml 时带 name 字段)


def _fmt(v, signed=False):
    import math
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v*100:+.0f}%" if signed else f"{v*100:.0f}%"


def main():
    ap = argparse.ArgumentParser(description="个股诊断报告 (read-only)")
    ap.add_argument("--codes", type=str, default="", help="逗号分隔 6 位个股代码(默认 STOCK_WATCHLIST)")
    ap.add_argument("--as-of", default=None, help="评估日 YYYY-MM-DD(默认今天)")
    ap.add_argument("--output", default="data/stock_diagnose.html")
    ap.add_argument("--push-alerts", action="store_true", help="推送信号提醒到微信/飞书(触发时)")
    args = ap.parse_args()
    setup_logging()

    cfg = get_config()
    store = Store(cfg.db_path)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or DataManager.STOCK_WATCHLIST
    names = {c: DataManager.STOCK_NAMES.get(c, c) for c in codes}
    asof = args.as_of or datetime.now().strftime("%Y-%m-%d")

    # 全套诊断
    diagnoses = {sym: sd.diagnose_stock_full(sym, store, cfg, asof=asof) for sym in codes}

    # 指数层(E4 蓝筹vs成长 用;无指数数据则降级跳过)
    index_diag = None
    try:
        from stockagent.tracker import diagnose as tdiag
        index_diag = tdiag.diagnose_layer(store)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ 指数层诊断失败(E4 将不触发): {str(e)[:80]}")

    alerts_list = sd.collect_stock_alerts(codes, store, cfg, index_diag=index_diag,
                                           asof=asof, names=names)

    # 🤖 AI 评估不在此预计算 —— 看板生成只渲染卡片 + 🤖 按钮,用户点击时由前端 fetch
    # 本地 ai_eval_server 实时调 LLM 生成(按需、单股、用点击当下数据)。
    html = srep.render(diagnoses, alerts_list, as_of=asof, names=names, store=store)
    out = srep.write_html(html, args.output)

    # 推送
    if args.push_alerts and alerts_list:
        title, text = talerts.format_for_push(alerts_list, title_prefix="📡 个股信号提醒")
        if title:
            from stockagent.notify import broadcast
            res = broadcast(text, title=title)
            print(f"  提醒推送: {res}" if res else "  ⚠ 未配置通知渠道(.env WECOM_BOT_KEY 等)")

    # 控制台摘要
    print(f"\n📊 个股诊断看板 -> {out}")
    print(f"   as_of={asof}  {len(codes)} 只  提醒 {len(alerts_list)} 条\n")
    for sym in codes:
        d = diagnoses[sym]
        c = d.get("classification", {})
        dv = d.get("davis", {})
        vz = d.get("valuation_zone", {})
        sec = f"/{'+'.join(c.get('secondary') or [])}" if c.get("secondary") else ""
        flags = []
        if dv.get("type") not in ("neutral", None):
            flags.append(f"戴维斯:{dv.get('label','')}")
        if (d.get("pitfalls", {}).get("net_profit", {}) or {}).get("abnormal"):
            flags.append("⚠避坑")
        a_syms = [a for a in alerts_list if names.get(sym, sym) == a.get("scope") or sym == a.get("scope")]
        if a_syms:
            flags.append("提醒:" + ",".join(a["rule"] for a in a_syms))
        flag_s = f"  [{' | '.join(flags)}]" if flags else ""
        print(f"   {names[sym]:6} {sym}  {c.get('primary','?'):<7}{sec:<10} "
              f"PE分位{_fmt(vz.get('pe_pct'))}  zone={vz.get('zone','—')}{flag_s}")
    print(f"\n   open: file:///{out.resolve()}")


if __name__ == "__main__":
    main()
