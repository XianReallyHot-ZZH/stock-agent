"""偏离度极值反弹 event study(只读诊断):实证"深度超卖(偏离度 pct ≤ 阈值)→反弹"的胜率与收益。

触发: 指数 close/MA60 − 1 的 *expanding-window* 历史分位 ≤ 阈值。
  expanding 分位 = 当天偏离度在"当天及之前"所有偏离度中的秩分位(0=最负/超卖, 1=最正/超买)。
  ⚠ 防前视: 不复用看板的全历史分位(deviation_extremes), 回测每天都只用当时及之前的数据。
出场: 固定持有期 forward(5/10/20/40/60 交易日, T收盘→T+N收盘, 与 validate_volume_bottom.py 同口径)。
对照: 任意时点买入的同持有期胜率/收益 = 基准, 信号 − 基准 = edge(地量研究即用此尺)。

Usage:
  python scripts/backtest_deviation_extreme.py                 # 默认创业板指 399006
  python scripts/backtest_deviation_extreme.py --index 000300  # 换沪深300
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from stockagent.config import get_config
from stockagent.data.store import Store
from stockagent.tracker import indicators as ti

MA = ti.MA_PERIOD            # 60
FORWARD = (5, 10, 20, 40, 60)
THRESHOLDS = (0.02, 0.05, 0.10, 0.20)
EXIT_PCT = 0.50              # 独立事件去重: pct 回升过 0.5 算事件结束

_NAMES = {"399006": "创业板指", "000300": "沪深300", "000001": "上证综指",
          "000016": "上证50", "000905": "中证500", "000688": "科创50"}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """胜率 k/n 的 95% Wilson 区间 → (lo, hi)。"""
    if n <= 0:
        return (np.nan, np.nan)
    p = k / n
    denom = 1 + z * z / n
    c = (p + z * z / (2 * n)) / denom
    h = z * np.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return (max(0.0, c - h), min(1.0, c + h))


def expanding_dev_pct(dev: np.ndarray) -> np.ndarray:
    """每天 dev 在「此前含当天」序列中的分位(0=最负/超卖, 1=最正/超买)。防前视核心。"""
    out = np.full(len(dev), np.nan)
    for i in range(len(dev)):
        v = dev[i]
        if np.isnan(v):
            continue
        seen = dev[:i + 1]
        m = ~np.isnan(seen)
        out[i] = float((seen[m] < v).sum()) / m.sum()
    return out


def build(px: pd.Series) -> pd.DataFrame:
    close = px.astype(float)
    ma = close.rolling(MA).mean()
    dev = (close / ma - 1.0).to_numpy()
    pct = expanding_dev_pct(dev)
    df = pd.DataFrame({"close": close.to_numpy(), "ma": ma.to_numpy(),
                       "dev": dev, "pct": pct},
                      index=pd.to_datetime(close.index))
    df["above_ma"] = df["close"] >= df["ma"]
    for n in FORWARD:
        df[f"fwd_{n}"] = df["close"].shift(-n) / df["close"] - 1.0
    return df


def stats_of(sub: pd.DataFrame) -> dict:
    """每组样本各 horizon 的 胜率/n/Wilson CI/均值/中位。"""
    out = {}
    for n in FORWARD:
        s = sub[f"fwd_{n}"].dropna()
        k_total = len(s)
        if k_total == 0:
            out[n] = dict(n=0, win=np.nan, lo=np.nan, hi=np.nan, mean=np.nan, med=np.nan)
            continue
        k = int((s > 0).sum())
        lo, hi = wilson(k, k_total)
        out[n] = dict(n=k_total, win=k / k_total, lo=lo, hi=hi,
                      mean=s.mean(), med=s.median())
    return out


def indep_events(df: pd.DataFrame, thresh: float) -> list:
    """相邻 pct≤thresh 聚成一段, 取每段首根(须有完整 forward)为一次独立事件。"""
    maxf = max(FORWARD)
    events, inside = [], False
    for d, row in df.iterrows():
        if pd.isna(row[f"fwd_{maxf}"]):
            continue
        if row["pct"] <= thresh and not inside:
            events.append(d)
            inside = True
        elif inside and row["pct"] > EXIT_PCT:
            inside = False
    return events


def _pct(x) -> str:
    return "—" if (x is None or (isinstance(x, float) and np.isnan(x))) else f"{x * 100:.0f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="399006")
    args = ap.parse_args()

    st = Store(get_config().db_path)
    name = _NAMES.get(args.index, args.index)
    px = st.get_index_daily_series(args.index)["close"]
    df = build(px).dropna(subset=["pct"])
    usable = df[df[f"fwd_{max(FORWARD)}"].notna()].copy()

    today = df.iloc[-1]
    print(f"=== 偏离度极值反弹 event-study ({name} {args.index}) ===")
    print(f"口径: 触发=expanding 偏离度 pct≤阈值 · T收盘→T+N收盘 forward · "
          f"独立事件去重=pct 回升>{EXIT_PCT:.0%}")
    print(f"数据: {df.index[0]:%Y-%m-%d}..{df.index[-1]:%Y-%m-%d} · 可用样本 {len(usable)} 根")
    print(f"当前: close={today['close']:.0f} MA{MA}={today['ma']:.0f} "
          f"dev={today['dev']:+.1%} expanding_pct={today['pct']:.1%}")
    trig_above = int(((df["above_ma"]) & (df["pct"] <= 0.02)).sum())
    trig_total = int((df["pct"] <= 0.02).sum())
    print(f"注: pct≤2% 触发 {trig_total} 根中, 处于 MA{MA} 线上 {trig_above} 根"
          f"(超卖几乎全在线下, 符合预期)")

    # —— [A] 全样本胜率(含重叠) + 基准 ——
    base = stats_of(usable)
    print(f"\n—— [A] 全样本胜率(含重叠) · 95% Wilson CI · n=样本 ——")
    print(f"{'阈值':<9}│" + "".join(f"{'%4d日' % n:^20}│" for n in FORWARD))
    print("─" * (9 + 21 * len(FORWARD)))
    for th in THRESHOLDS:
        stt = stats_of(usable[usable["pct"] <= th])
        cells = "".join(
            f" {_pct(stt[n]['win']):>4}[{stt[n]['lo']*100:>2.0f}-{stt[n]['hi']*100:>2.0f}] n={stt[n]['n']:<4}│"
            for n in FORWARD)
        print(f"{'≤%d%%' % (th*100):<9}│{cells}")
    cells = "".join(f" {_pct(base[n]['win']):>4}              n={base[n]['n']:<4}│" for n in FORWARD)
    print(f"{'基准(任意)':<9}│{cells}")

    # —— [B] 全样本 均值收益 / 中位 ——
    print(f"\n—— [B] 全样本 均值收益/中位(%)(对照基准) ——")
    print(f"{'阈值':<9}│" + "".join(f"{'%4d日' % n:^20}│" for n in FORWARD))
    print("─" * (9 + 21 * len(FORWARD)))
    for th in THRESHOLDS:
        stt = stats_of(usable[usable["pct"] <= th])
        cells = "".join(f" {stt[n]['mean']*100:+6.1f}/{stt[n]['med']*100:+6.1f}    │" for n in FORWARD)
        print(f"{'≤%d%%' % (th*100):<9}│{cells}")
    cells = "".join(f" {base[n]['mean']*100:+6.1f}/{base[n]['med']*100:+6.1f}    │" for n in FORWARD)
    print(f"{'基准(任意)':<9}│{cells}")

    # —— [C] edge = 信号胜率 − 基准胜率 ——
    print(f"\n—— [C] edge = 信号胜率 − 基准胜率(pp, 正=有 edge) ——")
    print(f"{'阈值':<9}│" + "".join(f"{'%4d日' % n:^12}│" for n in FORWARD))
    print("─" * (9 + 13 * len(FORWARD)))
    for th in THRESHOLDS:
        stt = stats_of(usable[usable["pct"] <= th])
        cells = ""
        for n in FORWARD:
            if stt[n]["n"] == 0:
                cells += f"{'—':^12}│"
            else:
                diff = (stt[n]["win"] - base[n]["win"]) * 100
                cells += f" {diff:+6.1f}pp    │"
        print(f"{'≤%d%%' % (th*100):<9}│{cells}")

    # —— [D] 独立事件(去重) ——
    print(f"\n—— [D] 独立事件(去重:入阈值→pct回升>{EXIT_PCT:.0%} 为一次, 取首日) ——")
    print(f"{'阈值':<9}│" + "".join(f"{'%4d日' % n:^14}│" for n in FORWARD) + " 独立事件数")
    print("─" * (9 + 15 * len(FORWARD) + 8))
    for th in THRESHOLDS:
        ev = [d for d in indep_events(df, th) if d in usable.index]
        stt = stats_of(usable.loc[ev])
        cells = "".join(f" {_pct(stt[n]['win']):>4} n={stt[n]['n']:<4}│" for n in FORWARD)
        print(f"{'≤%d%%' % (th*100):<9}│{cells} {len(ev)}")

    # —— [E] 近 10 年子段 ——
    cut = df.index[-1] - pd.Timedelta(days=10 * 365)
    seg = usable[usable.index >= cut]
    base10 = stats_of(seg)
    print(f"\n—— [E] 近 10 年全样本胜率({cut:%Y-%m-%d}..{df.index[-1]:%Y-%m-%d}, 基准=近10年任意) ——")
    print(f"{'阈值':<9}│" + "".join(f"{'%4d日' % n:^20}│" for n in FORWARD))
    print("─" * (9 + 21 * len(FORWARD)))
    for th in THRESHOLDS:
        stt = stats_of(seg[seg["pct"] <= th])
        cells = "".join(
            f" {_pct(stt[n]['win']):>4}[{stt[n]['lo']*100:>2.0f}-{stt[n]['hi']*100:>2.0f}] n={stt[n]['n']:<4}│"
            for n in FORWARD)
        print(f"{'≤%d%%' % (th*100):<9}│{cells}")
    cells = "".join(f" {_pct(base10[n]['win']):>4}              n={base10[n]['n']:<4}│" for n in FORWARD)
    print(f"{'基准(任意)':<9}│{cells}")

    print(f"\n读法: 胜率=forward 收益>0 占比 · [lo-hi]=95% Wilson CI · n=样本数")
    print(f"      样本<30 时 CI 很宽, 结论统计意义弱 · edge>0 才算真优势(地量研究用此尺得'无 edge')")


if __name__ == "__main__":
    main()
