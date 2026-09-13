"""tushare 迁移对账 (docs/EXECUTION_PLAN-tushare迁移.md · 交付物3 · 对账容差表).

每腿一个子命令: 新旧源重叠区间逐期 diff, 按族判定——精确族零容差、高精度族
|Δ|≤0.001、口径差族只量化不拦(出报告人工放行)。产物落 data/recon/。

用法:
  python scripts/recon_tushare.py valuation [code ...]   # 1.1 个股估值 baidu vs daily_basic
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import pandas as pd  # noqa: E402

from stockagent.config import get_config  # noqa: E402
from stockagent.data import fetcher  # noqa: E402

RECON_DIR = ROOT / "data" / "recon"

# 与 baidu 可比的 4 指标(pcf 不迁; ps_ttm/dv_ratio/turnover/circ_mv 为 tushare 新增无对照)
BAIDU_COMMON = ["pe_ttm", "pe_static", "pb", "market_cap"]


def _diff_stats(baidu: pd.Series, ts: pd.Series) -> dict:
    """重叠期 diff 统计(口径差族: 量化不拦)。同日对齐, baidu 稀疏半月级→重叠=其全部日期。"""
    both = pd.concat([baidu.rename("baidu"), ts.rename("ts")], axis=1, join="inner")
    if not len(both):
        return {"n_overlap": 0}
    d = (both["baidu"] - both["ts"]).abs()
    rel = d / both["ts"].abs().where(both["ts"].abs() > 1e-6)
    return {
        "n_overlap": len(both),
        "mean_abs": float(d.mean()),
        "p95_abs": float(d.quantile(0.95)),
        "max_abs": float(d.max()),
        "median_rel": float(rel.median()) if rel.notna().any() else float("nan"),
        "max_rel": float(rel.max()) if rel.notna().any() else float("nan"),
    }


def recon_valuation(codes: list[str] | None = None) -> Path:
    """1.1 估值腿: DB 里的 baidu 历史(旧源) vs tushare daily_basic 实拉(新源) 重叠对账。"""
    from stockagent.data.manager import DataManager
    cfg = get_config()
    codes = codes or DataManager.STOCK_WATCHLIST
    conn = sqlite3.connect(cfg.db_path)
    lines = ["# tushare 迁移对账 · 1.1 个股估值 (口径差族: 量化不拦, 人工放行)", "",
             f"- 旧源: baidu(稀疏半月级, DB 存量) vs 新源: daily_basic(日频全史)",
             f"- 可比指标: {BAIDU_COMMON}(pcf 无消费方不迁; ps_ttm/dv_ratio/turnover/circ_mv 新增无对照)",
             "- 单位: market_cap 两源统一为亿元(tushare total_mv 万元 ×1e-4 对齐 baidu 存量)", ""]
    n_rows = n_pairs = 0
    for code in codes:
        try:
            frames = fetcher.fetch_valuation_tushare(code)
        except Exception as e:  # noqa: BLE001
            lines.append(f"- {code}: tushare 拉取失败 — {str(e)[:90]}")
            continue
        for ind in BAIDU_COMMON:
            baidu = pd.read_sql_query(
                "SELECT date,value FROM stock_valuation WHERE symbol=? AND indicator=? "
                "AND source='baidu' ORDER BY date", conn, params=(code, ind))
            if not len(baidu) or ind not in frames:
                continue
            b = baidu.set_index("date")["value"]
            t = frames[ind]["value"]
            st = _diff_stats(b, t)
            if st["n_overlap"] == 0:
                continue
            n_pairs += 1
            n_rows += st["n_overlap"]
            lines.append(
                f"- {code} {ind}: 重叠 {st['n_overlap']} 期 · |Δ| 均值 {st['mean_abs']:.3f}"
                f" / p95 {st['p95_abs']:.3f} / 最大 {st['max_abs']:.3f}"
                f" · 相对Δ中位 {st['median_rel']*100:.1f}% / 最大 {st['max_rel']*100:.1f}%"
                f" · 新源覆盖 {len(t)} 期(至 {t.index[-1]})")
        lines.append(f"- {code}: tushare 指标 {sorted(frames)}")
    conn.close()
    lines += ["", f"合计: {n_pairs} 组指标对照 · {n_rows} 期重叠",
              "> 放行判据(口径差族): PE 族相对Δ中位 <2%、PB <2%、总市值 <1% 即视为口径一致可切换;",
              "> 超限则先查加权口径差异(静/TTM、归母口径)再决定。"]
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    out = RECON_DIR / "tushare_valuation.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[written] {out}")
    return out


SUBS = {"valuation": recon_valuation}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in SUBS:
        print(__doc__)
        sys.exit(2)
    SUBS[sys.argv[1]](sys.argv[2:] or None)


if __name__ == "__main__":
    main()
