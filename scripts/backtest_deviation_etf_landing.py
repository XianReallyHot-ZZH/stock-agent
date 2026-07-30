"""偏离度极值反弹: 指数信号 → ETF执行 落地验证(只读诊断)。

信号仍用创业板指(399006)的 expanding 偏离度 pct(与 backtest_deviation_extreme.py 同口径),
执行标的换成可交易的创业板ETF(159915)。三口径对比看跟踪误差 + 费用对 edge 的稀释:
  指数(理论) → ETF毛(含跟踪误差, 未扣费) → ETF净(再扣双边交易成本)

口径: 信号 T 收盘触发 → T+N 收盘 forward · expanding pct 防前视(用指数全历史算分位,
不在对齐区间截断) · ETF 用未复权市价(分红<1%/年未加回, 几十日持有影响可忽略)。

Usage:
  python scripts/backtest_deviation_etf_landing.py                       # 默认 399006→159915, 双边cost=0.3%
  python scripts/backtest_deviation_etf_landing.py --cost 0              # cost=0 看纯跟踪误差
  python scripts/backtest_deviation_etf_landing.py --etf 510300 --index 000300
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))            # import 兄弟脚本
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from backtest_deviation_extreme import (wilson, expanding_dev_pct, stats_of,
                                        _pct, FORWARD, THRESHOLDS, EXIT_PCT, MA)
from stockagent.config import get_config
from stockagent.data.store import Store

_NAMES = {"399006": "创业板指", "000300": "沪深300", "000905": "中证500"}
_ETF_NAMES = {"159915": "创业板ETF", "510300": "沪深300ETF", "510500": "中证500ETF"}


def load_pair(st: Store, idx_sym: str, etf_sym: str) -> pd.DataFrame:
    """expanding pct 用指数全历史算(分位依赖指数过去), 再对齐到 ETF 有数据的交易日。"""
    idx = st.get_index_daily_series(idx_sym)
    idx.index = pd.to_datetime(idx.index)
    idx["close"] = idx["close"].astype(float)
    idx["dev"] = idx["close"] / idx["close"].rolling(MA).mean() - 1.0
    idx["pct"] = expanding_dev_pct(idx["dev"].to_numpy())

    etf = st.get_series(etf_sym)
    etf.index = pd.to_datetime(etf.index)
    etf["close"] = etf["close"].astype(float)

    m = (idx[["close", "dev", "pct"]].rename(columns={"close": "close_idx"})
         .join(etf[["close"]].rename(columns={"close": "close_etf"}), how="inner"))
    m.index.name = "date"
    return m.dropna(subset=["pct", "close_idx", "close_etf"])


def add_forwards(m: pd.DataFrame, cost: float) -> pd.DataFrame:
    maxf = max(FORWARD)
    for n in FORWARD:
        m[f"idx_{n}"] = m["close_idx"].shift(-n) / m["close_idx"] - 1.0
        m[f"etf_{n}"] = m["close_etf"].shift(-n) / m["close_etf"] - 1.0
        m[f"net_{n}"] = m[f"etf_{n}"] - cost
    m["usable"] = m[f"idx_{maxf}"].notna() & m[f"etf_{maxf}"].notna()
    return m


def view(sub: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """把 {prefix}_{n} 列重命名为 fwd_n, 复用 stats_of。"""
    return sub[[f"{prefix}_{n}" for n in FORWARD]].rename(
        columns={f"{prefix}_{n}": f"fwd_{n}" for n in FORWARD})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="399006")
    ap.add_argument("--etf", default="159915")
    ap.add_argument("--cost", type=float, default=0.003, help="双边交易成本(默认 0.3%%)")
    args = ap.parse_args()

    st = Store(get_config().db_path)
    name = _NAMES.get(args.index, args.index)
    ename = _ETF_NAMES.get(args.etf, args.etf)
    m = add_forwards(load_pair(st, args.index, args.etf), args.cost)
    usable = m[m["usable"]].copy()

    today = m.iloc[-1]
    print(f"=== 偏离度极值反弹: 指数信号→ETF执行 落地验证 ===")
    print(f"信号: {name}({args.index}) expanding pct≤阈值  ·  执行: {ename}({args.etf}) 市价")
    print(f"口径: T收盘→T+N收盘 forward · 双边cost={args.cost*100:.2f}% · "
          f"expanding pct 用指数全历史算(防前视)")
    print(f"对齐区间: {usable.index[0]:%Y-%m-%d}..{usable.index[-1]:%Y-%m-%d} · "
          f"可用样本 {len(usable)} 根")
    print(f"当前: 指数={today['close_idx']:.0f}(dev={today['dev']:+.1%} pct={today['pct']:.1%}) "
          f"ETF={today['close_etf']:.3f}")

    base_idx = stats_of(view(usable, "idx"))
    base_etf = stats_of(view(usable, "etf"))
    base_net = stats_of(view(usable, "net"))

    # —— [A] 三口径胜率对比(指/毛/净) ——
    print(f"\n—— [A] 胜率对比(指数 / ETF毛 / ETF净) · n=触发样本(三口径同日同数) ——")
    print(f"{'阈值':<9}│" + "".join(f"{'%2d日' % n:^26}│" for n in FORWARD))
    print("─" * (9 + 27 * len(FORWARD)))
    for th in THRESHOLDS:
        sub = usable[usable["pct"] <= th]
        si, se, sn = stats_of(view(sub, "idx")), stats_of(view(sub, "etf")), stats_of(view(sub, "net"))
        cells = ""
        for n in FORWARD:
            cells += (f" {_pct(si[n]['win']):>3}/{_pct(se[n]['win']):>3}/{_pct(sn[n]['win']):>3}"
                      f" n={si[n]['n']:<3}│")
        print(f"{'≤%d%%' % (th*100):<9}│{cells}")
    cells = "".join(f" {_pct(base_idx[n]['win']):>3}/{_pct(base_etf[n]['win']):>3}/{_pct(base_net[n]['win']):>3}"
                    f" n={base_idx[n]['n']:<3}│" for n in FORWARD)
    print(f"{'基准(任意)':<9}│{cells}")

    # —— [B] edge 对比(各自减自身基准, pp) ——
    print(f"\n—— [B] edge 对比(指数 / ETF毛 / ETF净, 各自减自身基准, pp) ——")
    print(f"{'阈值':<9}│" + "".join(f"{'%2d日' % n:^22}│" for n in FORWARD))
    print("─" * (9 + 23 * len(FORWARD)))
    for th in THRESHOLDS:
        sub = usable[usable["pct"] <= th]
        si, se, sn = stats_of(view(sub, "idx")), stats_of(view(sub, "etf")), stats_of(view(sub, "net"))
        cells = ""
        for n in FORWARD:
            ei = (si[n]["win"] - base_idx[n]["win"]) * 100
            ee = (se[n]["win"] - base_etf[n]["win"]) * 100
            en = (sn[n]["win"] - base_net[n]["win"]) * 100
            cells += f" {ei:+5.0f}/{ee:+5.0f}/{en:+5.0f}pp│"
        print(f"{'≤%d%%' % (th*100):<9}│{cells}")

    # —— [C] 收益均值对比(指数 / ETF净, %) ——
    print(f"\n—— [C] 收益均值对比(指数 / ETF净, %) ——")
    print(f"{'阈值':<9}│" + "".join(f"{'%2d日' % n:^20}│" for n in FORWARD))
    print("─" * (9 + 21 * len(FORWARD)))
    for th in THRESHOLDS:
        sub = usable[usable["pct"] <= th]
        si, sn = stats_of(view(sub, "idx")), stats_of(view(sub, "net"))
        cells = "".join(f" {si[n]['mean']*100:+5.1f}/{sn[n]['mean']*100:+5.1f}  │" for n in FORWARD)
        print(f"{'≤%d%%' % (th*100):<9}│{cells}")
    cells = "".join(f" {base_idx[n]['mean']*100:+5.1f}/{base_net[n]['mean']*100:+5.1f}  │" for n in FORWARD)
    print(f"{'基准(任意)':<9}│{cells}")

    # —— [D] 跟踪误差: ETF毛 − 指数(事件期均值) ——
    print(f"\n—— [D] 跟踪误差(ETF毛收益 − 指数收益, 事件期均值, %; 负=ETF跑输) ——")
    print(f"{'阈值':<9}│" + "".join(f"{'%2d日' % n:^10}│" for n in FORWARD))
    print("─" * (9 + 11 * len(FORWARD)))
    for th in THRESHOLDS:
        sub = usable[usable["pct"] <= th]
        cells = ""
        for n in FORWARD:
            te = (sub[f"etf_{n}"] - sub[f"idx_{n}"]).mean() * 100
            cells += f" {te:+6.2f}  │" if not np.isnan(te) else f" {'—':^8}│"
        print(f"{'≤%d%%' % (th*100):<9}│{cells}")

    print(f"\n读法: [A] 三口径胜率逐级下降=跟踪误差+费用在吃 edge · "
          f"[B] edge 三列看优势被稀释多少 · [D] 负值=ETF系统性跑输指数(跟踪误差)")
    print(f"注: ETF 含重叠样本(同一次行情多次触发), 绝对数值偏乐观; 独立事件更少(见 backtest_deviation_extreme [D])。")


if __name__ == "__main__":
    main()
