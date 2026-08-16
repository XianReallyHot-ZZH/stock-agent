"""PEAD 预告超预期漂移(纯)——surprise = 预告 yoy − 公告时点隐含预期。

双腿口径(全 point-in-time,无前视):
  leg C(consensus-implied, 仅年报期): expected = 公告日快照 eps_fy1 ÷ 公告日前一年快照
        同列 − 1(两份快照均 ≤ announce_date,由装配层取,本函数只收数)
  leg A(actual-implied, 回退/历史主源): expected = 上年同期 stock_report_actual 的 np_yoy
        (公告日早于本公告的行;E0 未积累的历史期自然落此腿)
surprise_pp = forecast_yoy − expected(百分点)。>0 超预期 → 公告后漂移窗口跟踪;
漂移是否存在 edge 由 validate_pead.py event-study 裁决(读图说明注入结论,可能无 edge)。
"""
from __future__ import annotations

import math


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def pead_surprise(forecast_yoy, period: str, eps_now=None, eps_prior=None,
                  fy1_year=None, prior_actual_yoy=None) -> dict:
    """单条预告的超预期落差(纯)。

      forecast_yoy     预告归母净利同比(%)
      period           报告期 YYYYMMDD
      eps_now/eps_prior/fy1_year  leg C 材料(公告日/公告日−1年 的一致预期快照同列;
                        fy1_year==period 年才成立——公告时市场预期已对齐该财年)
      prior_actual_yoy leg A 材料(上年同期正式报 np_yoy,%)
    Returns {valid, surprise_pp, expected, leg}: leg='C'|'A';两腿皆缺 → valid=False。
    """
    if _nan(forecast_yoy):
        return {"valid": False, "surprise_pp": None, "expected": None, "leg": None}
    expected = None
    leg = None
    # leg C: 年报期且财年对齐(中报/季报的 eps_fy1 是全年预期,除上年 EPS 得不出中报预期)
    is_annual = isinstance(period, str) and len(period) == 8 and period[4:] == "1231"
    if (is_annual and not _nan(eps_now) and not _nan(eps_prior)
            and not _nan(fy1_year) and float(eps_prior) > 0
            and int(fy1_year) == int(period[:4])):
        expected = (float(eps_now) / float(eps_prior) - 1.0) * 100.0
        leg = "C"
    elif not _nan(prior_actual_yoy):
        expected = float(prior_actual_yoy)
        leg = "A"
    if expected is None:
        return {"valid": False, "surprise_pp": None, "expected": None, "leg": None}
    return {"valid": True, "surprise_pp": float(forecast_yoy) - expected,
            "expected": expected, "leg": leg}


def pead_rank(events: list[dict], surprise_min_pp: float = 10.0,
              top_n: int = 50) -> list[dict]:
    """PEAD 事件表排序(纯): |surprise| ≥ surprise_min_pp 才入表,按 surprise 降序取 top_n。
    events = [{code, name, period, type, forecast_yoy, announce_date, surprise_pp, expected, leg}]。"""
    rows = [e for e in events if e.get("surprise_pp") is not None
            and abs(float(e["surprise_pp"])) >= surprise_min_pp]
    rows.sort(key=lambda e: -float(e["surprise_pp"]))
    return rows[:top_n]
