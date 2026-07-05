"""Step 0 probe — verify akshare endpoints for the research module (akshare 1.18.64).

Run: python scripts/probe_research_sources.py
Re-verifies the 4 data sources the research module depends on. Kept in the repo as
a living record of which endpoints work + their exact signatures/columns.

VERDICT (confirmed 2026-07):
  1. ETF NAV     -> fund_etf_fund_info_em(fund, start_date, end_date)   [单位净值/累计净值]
  2. SSE shares  -> fund_etf_scale_sse(date)                              [existing]
  3. SZSE shares -> fund_etf_scale_szse(date)  or  fund_scale_daily_szse  [gap CLOSED]
  4. Industry PE -> stock_industry_pe_ratio_cninfo(symbol="证监会行业分类", date) [per-date snapshot]

DEAD ENDS (don't use):
  - stock_index_pe_lg: full history BUT only major indices (上证系列/中证1000);
    rejects sector names (中证银行/中证医药...) and is flaky under rate-limiting.
  - stock_zh_index_value_csindex: only ~25 days of recent daily PE — not history.
  - fund_aum_hist_em / fund_scale_change_em: market-wide aggregates, not per-fund.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import akshare as ak
import pandas as pd

pd.set_option("display.width", 200)


def _show(label, df, n=2):
    print(f"\n--- {label} ---")
    if df is None or not isinstance(df, pd.DataFrame) or not len(df):
        print(f"  empty/None ({type(df)})"); return
    print(f"  shape={df.shape}  cols={list(df.columns)}")
    print(df.tail(n).to_string())


def probe_nav():
    print("=" * 60, "\n[1] ETF NAV — fund_etf_fund_info_em\n", "=" * 60)
    df = ak.fund_etf_fund_info_em(fund="512800", start_date="20240101", end_date="20240110")
    _show("512800 NAV", df)  # cols: 净值日期/单位净值/累计净值/日增长率/...


def probe_szse_shares():
    print("\n", "=" * 60, "\n[2] SZSE shares — fund_etf_scale_szse(date)\n", "=" * 60)
    df = ak.fund_etf_scale_szse(date="20240105")
    # filter to a pool SZSE ETF to confirm coverage
    if "基金代码" in df.columns:
        hit = df[df["基金代码"].astype(str).isin(("159915", "159941"))]
        _show("159915/159941 rows", hit)  # cols include 基金份额 + 净值


def probe_industry_pe():
    print("\n", "=" * 60, "\n[3] Industry PE — stock_industry_pe_ratio_cninfo\n", "=" * 60)
    df = ak.stock_industry_pe_ratio_cninfo(symbol="证监会行业分类", date="20240105")
    _show("all industries 2024-01-05", df)
    # confirm the 5 v1 sectors resolve to a 证监会行业 row
    want = ("货币金融服务", "资本市场服务", "计算机、通信和其他电子设备制造业",
            "酒、饮料和精制茶制造业", "医药制造业")
    if "行业名称" in df.columns:
        hits = df[df["行业名称"].isin(want)][["行业名称", "静态市盈率-加权平均", "静态市盈率-中位数"]]
        print("\n  v1 sector mapping check:")
        print(hits.to_string())


def main():
    print(f"akshare {ak.__version__}")
    probe_nav()
    probe_szse_shares()
    probe_industry_pe()
    print("\n" + "=" * 60, "\nDONE — see VERDICT in module docstring.\n", "=" * 60)


if __name__ == "__main__":
    main()
