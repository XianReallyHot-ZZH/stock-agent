"""预告行业选股 event-study——行业预喜率过门 → 榜内 Top-5 → 持有到法定披露截止(近似)。

实验(设计讨论锁定 2026-08-24): 最早信息=业绩预告;逐日 as-of 行业聚合(东财板块包含回滚
到一级根),预喜率 ≥70% 且 ≥5 条 → 行业过门;榜内 yoy Top-5 次日开盘入场,持有到法定截止日
后首交易日开盘出场。臂(嵌套 strat ⊂ b2 ⊂ b1)+ 逐笔同窗市场等权基线,诚实报告含无 edge。

Usage:
  python scripts/validate_forecast_industry.py    # 全 8 期预告, ~2-5min → data/forecast_industry_study.md
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from stockagent.config import get_config
from stockagent.data.store import Store
from stockagent.pool.forecast_industry import (
    BULL_TYPES, build_trades, industry_rollup, matched_baseline, ret_summary,
    season_filter, trade_returns)
from stockagent.pool.prices import dividend_adjusted_open

MIN_FORECASTS = 5      # 行业过门最小预告样本
BULL_RATE_MIN = 0.70   # 行业过门预喜率阈值
TOP_N = 5              # 榜内个股数(等权)
ENTRY_SLACK = 5        # 入场窗(触发日后市场日数)
FFILL_LIMIT = 10       # 基线矩阵停牌 ffill 上限
MAIN_TAILS = ("1231", "0630")   # 主样本=年报+半年报;Q1/Q3 预告稀疏只做附注
STOCK_PREFIX = ("60", "00", "30", "68")   # 排北交所/B股

ARMS = [  # (名称, 行过滤器, 腿)
    ("strat  行业门+Top5", lambda t: t["in_strat"], "ind"),
    ("ex_tk  同上·排扭亏", lambda t: t["in_ex_tk"], "ind"),
    ("b2     行业门·不选股", lambda t: t["in_b2"], "ind"),
    ("b1     全预喜·无行业门", lambda t: pd.Series(True, index=t.index), "own"),
]


def _pct(x: float) -> str:
    return "—" if pd.isna(x) else f"{x * 100:+.1f}%"


def _pct0(x: float) -> str:
    return "—" if pd.isna(x) else f"{x * 100:.0f}%"


def _arm_frame(trades: pd.DataFrame, mask, leg: str) -> pd.DataFrame:
    sub = trades[mask(trades) & (trades[f"status_{leg}"] == "closed")].copy()
    return sub


def _stats_line(sub: pd.DataFrame, base_col: str, ret_col: str) -> str:
    if len(sub) == 0:
        return "n=0"
    s = ret_summary(sub[ret_col])
    ex = sub[ret_col].astype(float) - sub[base_col].astype(float)
    es = ret_summary(ex)
    hd = sub[f"hold_{ret_col.split('_')[1]}"].dropna()
    hold = f"持{hd.median():.0f}日" if len(hd) else ""
    return (f"n={s['n']} 胜率{_pct0(s['win_rate'])} 中位{_pct(s['median'])} "
            f"均值{_pct(s['mean'])} | 超额: 胜率{_pct0(es['win_rate'])} "
            f"中位{_pct(es['median'])} 均值{_pct(es['mean'])} {hold}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/forecast_industry_study.md")
    args = ap.parse_args()

    store = Store(get_config().db_path)

    # ---- 1. 预告全期 ----
    frames = []
    for period, _n in store.forecast_period_counts():
        fc = store.get_stock_forecast_period(period).reset_index()
        fc["report_period"] = period
        frames.append(fc)
    fc_all = pd.concat(frames, ignore_index=True)
    n_raw = len(fc_all)

    # ---- 2. 宇宙滤: 代码段 + ST(最新 spot 名,point-in-time 近似) ----
    spot = store.latest_stock_spot()
    st_codes = {c for c, r in spot.iterrows() if "ST" in str(r.get("name", ""))}
    fc_all = fc_all[[str(c)[:2] in STOCK_PREFIX for c in fc_all["code"]]]
    n_prefix_kept = len(fc_all)
    fc_all = fc_all[[c not in st_codes for c in fc_all["code"]]]

    # ---- 3. 窗口滤 + 行业回滚 ----
    fc_win, n_dropped = season_filter(fc_all)
    members = store.industry_members()
    industry_of, _parent = industry_rollup(members)

    # ---- 4. 事件构建 ----
    trades = build_trades(fc_win, industry_of, min_forecasts=MIN_FORECASTS,
                          bull_rate_min=BULL_RATE_MIN, top_n=TOP_N)
    print(f"预告 {n_raw} 行(代码段滤后 {n_prefix_kept},窗口滤剔 {n_dropped});"
          f"预喜事件 {len(trades)} 笔,其中行业门内 {int(trades['in_b2'].sum())}、"
          f"Top{TOP_N} {int(trades['in_strat'].sum())}")

    # ---- 5. 前复权 opens(事件股 + 基线宇宙共用) ----
    syms = sorted({str(s) for s in store.symbols()
                   if str(s)[:2] in STOCK_PREFIX})
    need = set(trades["code"].astype(str)) | set(syms)
    opens: dict[str, pd.Series] = {}
    for k, code in enumerate(need):
        price = store.get_series(code)
        if len(price) < 30:
            continue
        div = store.get_stock_dividend_series(code)
        opens[code] = dividend_adjusted_open(
            price["open"], price["close"], div if len(div) else None)
        if k % 300 == 0:
            print(f"  opens {k}/{len(need)}...")
    trading_days = sorted({d for s in opens.values() for d in s.index})

    # ---- 6. 定价 + 基线 ----
    trades = trade_returns(trades, opens, trading_days, entry_slack=ENTRY_SLACK)
    mat = pd.DataFrame({c: s for c, s in opens.items() if c in set(syms)}).ffill(
        limit=FFILL_LIMIT) if len(syms) else pd.DataFrame()
    trades["base_own"] = matched_baseline(
        mat, trades["entry_own"], trades["exit_own"])
    trades["base_ind"] = matched_baseline(
        mat, trades["entry_ind"], trades["exit_ind"])

    # ---- 7. 报告 ----
    main_periods = sorted(p for p in trades["period"].unique()
                          if p[4:] in MAIN_TAILS)
    annex_periods = sorted(p for p in trades["period"].unique()
                           if p[4:] not in MAIN_TAILS)
    lines: list[str] = []
    lines.append("# 预告行业选股 event-study")
    lines.append("")
    lines.append(f"生成 {datetime.now().strftime('%Y-%m-%d')} · 预告 {n_raw} 行 → 宇宙滤(代码段+ST) "
                 f"{n_prefix_kept} → 窗口滤后 {len(fc_win)}(剔 {n_dropped}) · "
                 f"预喜事件 {len(trades)} 笔")
    lines.append("")
    lines.append(f"口径: 行业门=预喜率≥{BULL_RATE_MIN:.0%} 且 ≥{MIN_FORECASTS} 条(as-of 逐日,"
                 f"东财板块包含回滚到一级根) · 个股=榜内 yoy Top{TOP_N} 等权 · "
                 f"入场=触发日次日开盘(停牌顺延≤{ENTRY_SLACK}市场日) · "
                 "出场=法定截止日后首交易日开盘(**近似**: 正式报披露日年报期不可用,"
                 "真实披露散布截止日前 → 持有期系统性偏长) · 前复权 open · "
                 "b1=自身公告日入场,其余臂=过门日或其后自身公告日")
    lines.append("")
    lines.append("## 主样本(年报+半年报) 合并")
    lines.append("")
    tm = trades[trades["period"].isin(main_periods)]
    for name, mask, leg in ARMS:
        sub = _arm_frame(tm, mask, leg)
        lines.append(f"- **{name}**: {_stats_line(sub, f'base_{leg}', f'ret_{leg}')}")
    lines.append("")
    lines.append("## 分期")
    lines.append("")
    for p in main_periods + annex_periods:
        tag = "" if p[4:] in MAIN_TAILS else "(附注·预告稀疏)"
        lines.append(f"### {p} {tag}")
        lines.append("")
        tp = trades[trades["period"] == p]
        for name, mask, leg in ARMS:
            sub = _arm_frame(tp, mask, leg)
            lines.append(f"- {name}: {_stats_line(sub, f'base_{leg}', f'ret_{leg}')}")
        lines.append("")

    # 状态计数 + Top 交易
    def _cnt(col):
        return trades[col].value_counts().to_dict()

    lines.append("## 数据脚注(诚实)")
    lines.append("")
    st_i, st_o = _cnt("status_ind"), _cnt("status_own")
    lines.append(f"- 状态计数 ind 腿: {st_i} · own 腿: {st_o}")
    bull_all = fc_win[fc_win["type"].isin(BULL_TYPES)]
    cov = bull_all["code"].astype(str).isin(opens).mean()
    lines.append(f"- 预喜股价格覆盖 {cov:.0%}(pool 价格腿 ~1559 只,预告宇宙是全市场——"
                 "覆盖偏差偏大票;未覆盖股自动落 no_price 不入统计)")
    lines.append("- 存活偏差: spot/行业/价格都是最新快照,窗口内退市股缺席(把最差表现者"
                 "系统性排除,收益偏乐观);ST 滤同样用最新名非 point-in-time")
    lines.append("- 预告 announce_date=最新修正公告日(端点 dedupe keep=last),修正会推迟"
                 "入场日;FY2025 期前脏日期(2024-09-06 类)已被窗口滤剔除")
    lines.append("- 行业映射=快照回滚非 point-in-time;东财树缺基础化工/有色金属/"
                 "纺织服饰顶节点,其子板块各自为根(~55 组)")
    lines.append("- 出场为截止日近似,同期间所有笔同日出场;未平仓(open)=期未走完"
                 "(如 2026H1 截止 8/31)或长停未复牌,不入统计")

    top = trades[trades["in_strat"] & (trades["status_ind"] == "closed")].nlargest(
        15, "ret_ind")
    if len(top):
        lines.append("")
        lines.append("## 策略臂 Top15(按 ret_ind)")
        lines.append("")
        lines.append("|期间|代码|行业|类型|yoy|触发|入场|出场|收益|超额|")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for _, r in top.iterrows():
            lines.append(
                f"|{r['period']}|{r['code']}|{r['industry']}|{r['type']}|"
                f"{r['yoy']:+.0f}%|{r['trigger_ind']}|{r['entry_ind']}|"
                f"{r['exit_ind']}|{_pct(r['ret_ind'])}|"
                f"{_pct(r['ret_ind'] - r['base_ind'])}|")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n报告 -> {out}")
    for line in lines[:20]:
        print(line)


if __name__ == "__main__":
    main()
