"""两轨估值尺(纯)——非周期 PEG 轨 / 周期 PB 分位轨(V8 高业绩池, 2026-09)。

方法论对齐: 非周期按扣非净利 PEG 排名(阿里帖原文);「业绩没出股价已上天」的用 PEG 上限
筛掉;**周期股看 PB 不看利润**——周期底利润崩、PE 爆表会被 PE 门误杀,而 PB 低=资产便宜
恰是买点。两轨各自排名后合并取 Top-N,池内不设行业配额(行业暴露从池里涌现,仓位层的事)。

口径(Q10 锁定: C 为主尺,一把尺子量当前与回放):
  PE_ttm = 市值 / TTM归母净利;TTM = 上年报 + 本期累计 − 上年同期累计(报告期累计口径);
  市值_t = 股本(现市值/现价反推,股本缓变近似) × price_t —— 回放期近似,读图说明注明;
  PEG = PE_ttm / 扣非g(%数值);PB = raw价 / 每股净资产(报告期阶梯,公告日 point-in-time)。
spot 市盈率-动态作交叉核对列,不进判定(口径不同源)。
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def ttm_net_profit(np_abs: dict[str, float], period: str) -> Optional[float]:
    """TTM 归母净利(纯): np_abs = {report_period: 累计净利润绝对值}(report_actual 扩列)。
    年报期(1231) TTM=年报本身;季报期 TTM = 上年年报 + 本期YTD − 上年同期YTD。
    任一块缺失 → None(早期回放窗诚实缺省,消融臂处理,不外推)。"""
    y = int(period[:4])
    fy_prev = f"{y - 1}1231"
    if period[4:] == "1231":
        return _num(np_abs.get(period))
    prev_same = f"{y - 1}{period[4:]}"
    a, b, c = _num(np_abs.get(fy_prev)), _num(np_abs.get(period)), _num(np_abs.get(prev_same))
    if a is None or b is None or c is None:
        return None
    return a + b - c


def implied_shares(mktcap_now: Optional[float], price_now: Optional[float]) -> Optional[float]:
    """股本反推(纯): 现总市值/现价。mktcap 缺(spot 降级日/旧快照)或价 ≤0 → None。"""
    m, p = _num(mktcap_now), _num(price_now)
    if m is None or p is None or p <= 0 or m <= 0:
        return None
    return m / p


def pe_ttm(price: Optional[float], shares: Optional[float],
           ttm_profit: Optional[float]) -> Optional[float]:
    """PE_ttm(纯) = price × shares / TTM净利。任一缺/TTM≤0(亏损,PE 无意义) → None。
    亏损股过不了地板本来也少,但 TTM 偶发转负时诚实返回 None(不入 PEG 排名)。"""
    p, s, t = _num(price), _num(shares), _num(ttm_profit)
    if p is None or s is None or t is None or t <= 0:
        return None
    return p * s / t


def peg(pe: Optional[float], growth_pct: Optional[float],
        growth_cap: float = 300.0) -> Optional[float]:
    """PEG(纯) = PE / g(百分点数值;g=扣非yoy)。g 巨大时 PEG→0 会假性登顶,封顶 growth_cap
    (300%以上的增长差别对估值排序无信息)。g≤0(过地板后不可能,防御)或 PE 缺 → None。"""
    pe_v, g = _num(pe), _num(growth_pct)
    if pe_v is None or pe_v <= 0 or g is None or g <= 0:
        return None
    return pe_v / min(g, growth_cap)


def pb_series(price: pd.Series, bvps_events: list[tuple[str, str, float]]) -> pd.Series:
    """PB 时序(纯,point-in-time): bvps_events = [(announce_date, period, bvps)] 升序,
    每个公告日之后用该期每股净资产(阶梯函数)。价格=raw close(分子水平值口径正确)。
    首个公告日之前无 PB(NaN)——诚实缺省,不回填 hindsight。Returns PB Series(同名 index)。"""
    if price is None or len(price) == 0 or not bvps_events:
        return pd.Series(dtype=float)
    events = sorted((str(a), float(b)) for a, _p, b in bvps_events
                    if a and b is not None and not math.isnan(b) and b > 0)
    if not events:
        return pd.Series(dtype=float)
    out = pd.Series(float("nan"), index=price.index, dtype=float)
    idx_str = price.index.astype(str)
    for ann, bv in events:
        mask = idx_str >= ann
        out.loc[mask] = price[mask] / bv
    return out.dropna()


def pb_percentile(pb: pd.Series, min_history: int = 120) -> Optional[float]:
    """PB 自身历史分位(纯, expanding 防前视): 最新值在其之前全部历史中的百分位。
    样本 <min_history(约半年) → None(分位不可靠,启动期留白——与估值档回放同款纪律)。"""
    if pb is None or len(pb) < min_history:
        return None
    cur = float(pb.iloc[-1])
    hist = pb.iloc[:-1].astype(float)
    if math.isnan(cur) or len(hist) == 0:
        return None
    return float((hist <= cur).mean())
