"""披露日历 v2(纯)——正式报截止 + 窗口A/B 状态机。

research.earnings._DISCLOSURE_WINDOWS 只编码了**预告**窗(开窗/截止);本模块补上正式报
法定截止(A股上市规则: 年报→次年4/30, 一季报→4/30, 中报→8/31, 三季报→10/31;快报无统一
法定截止,深市年报惯例 2 月底——只展示提示不门控),构成:

  窗口A = 预告/快报落地后 → 该期正式报截止前(信息已揭示,博漂移;财报验证后介入)
  窗口B = 上期正式报截止+grace → 下期预告开窗前(季中预估埋伏;仅周期——商品可观测)
注: Q1 期窗口B 按构造为空(年报截止 4/30+grace 晚于 Q1 预告开窗 4/15)——诚实口径,
非 bug;窗口B 实际存在于 H1(5月中→6月底)/Q3(9月中→9月底)/年报(11月中→12月底)。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..research.earnings import _DISCLOSURE_WINDOWS

# 正式报(定期报告)法定截止: period_tail → (月, 日);年报(1231)在次年
FORMAL_DEADLINES = {"1231": (4, 30), "0331": (4, 30), "0630": (8, 31), "0930": (10, 31)}

# 预告窗宽度(开窗→截止): 1231→31天 / 0331→16天 / 0630→15天 / 0930→15天;disclosure chip 分界用
_FORECAST_SPAN_DAYS = 31


def formal_deadline(period: str) -> date:
    """某报告期正式报的法定截止日(纯)。畸形期 → ValueError(window_dates 同风格)。"""
    if not isinstance(period, str) or len(period) != 8 or not period.isdigit():
        raise ValueError(f"bad report_period {period!r}")
    y, tail = int(period[:4]), period[4:]
    if tail not in FORMAL_DEADLINES:
        raise ValueError(f"unknown period tail {tail!r}")
    m, d = FORMAL_DEADLINES[tail]
    return date(y + 1, m, d) if tail == "1231" else date(y, m, d)


# 预告窗宽度(开窗→截止,天): 1231→31 / 0331→16 / 0630→15 / 0930→15(池阶段判定用)
_FORECAST_SPANS = {"1231": 31, "0331": 16, "0630": 15, "0930": 15}


def pool_phase(now) -> dict:
    """高业绩池的披露阶段(纯)——①池总览开头横幅用,回答「这池子是什么时候构成的、
    下次什么时候变」。管道期 P=current_period(截止未过的最老一期),今天相对 P 的
    预告开窗 fo / 窗宽 span / 正式报截止 dl 的位置:
      gap             披露间隙(静默期): d < fo——成员基本冻结,只有估值漂移;下事件=预告开窗
      forecast_window 预告窗(换血中): fo ≤ d ≤ fo+span;下事件=窗收尾
      report_season   正式报季(滚动重判): 窗后 ~ dl;下事件=正式报截止(池切换完成)
    Returns {phase, label, next_label, next_date(ISO), days_to_next, period}。"""
    d = now.date() if isinstance(now, datetime) else now
    period = current_period(d)
    tail = period[4:]
    fo = forecast_open(period)
    dl = formal_deadline(period)
    fe = fo + timedelta(days=_FORECAST_SPANS[tail])
    if d < fo:
        return {"phase": "gap", "label": "披露间隙·静默期(成员基本冻结,仅估值漂移)",
                "next_label": "预告开窗(换血启动)", "next_date": fo.isoformat(),
                "days_to_next": (fo - d).days, "period": period}
    if d <= fe:
        return {"phase": "forecast_window", "label": "预告窗·换血中(高增长预告落地即入池)",
                "next_label": "预告窗收尾", "next_date": fe.isoformat(),
                "days_to_next": (fe - d).days, "period": period}
    return {"phase": "report_season", "label": "正式报季·滚动重判(逐家落地逐家重判+扣非精筛)",
            "next_label": "正式报截止(池切换完成)", "next_date": dl.isoformat(),
            "days_to_next": (dl - d).days, "period": period}


def current_period(now) -> str:
    """当前披露周期(纯): 正式报截止 ≥ 今天的最**早**报告期(管道仍在飞行的最老一期)——
    窗口A/B 的锚定期。e.g. 8月中 → 20260630(截止8/31); 6月初 → 20260630(Q1 已收窗);
    4月末 → 上年年报(年报与 Q1 截止同为 4/30,取更老的年报;4/15-4/30 双窗重叠是 A 股
    日历固有,埋伏决策以年报管道为主,诚实口径)。"""
    d = now.date() if isinstance(now, datetime) else now
    y = d.year
    cands = [f"{y}{t}" for t in FORMAL_DEADLINES] + [f"{y - 1}{t}" for t in FORMAL_DEADLINES]
    live = [p for p in cands if formal_deadline(p) >= d]
    return min(live)  # 最早未过期者(同日截止算仍在窗,闭区间)


def forecast_open(period: str) -> date:
    """某期预告开窗日(纯;research._DISCLOSURE_WINDOWS 口径)。"""
    tail = period[4:]
    (om, od), _ = _DISCLOSURE_WINDOWS[tail]
    return date(int(period[:4]) + 1, om, od) if tail == "1231" else date(int(period[:4]), om, od)


def window_b_period(now, grace_days: int = 14) -> dict | None:
    """窗口B(季中预估埋伏窗,纯): 上期正式报截止+grace ≤ 今天 < 下期预告开窗。

    Returns {period, start, end}(下期报告期) or None。Q1 期天然为空(见模块 docstring)。"""
    d = now.date() if isinstance(now, datetime) else now
    y = d.year
    cands = sorted({f"{y}{t}" for t in FORMAL_DEADLINES} | {f"{y + 1}{t}" for t in FORMAL_DEADLINES})
    # 下期 P 的上期 = 年内序 P 的前一报告期(0331←上年1231, 0630←0331, 0930←0630, 1231←0930)
    tails = ["0331", "0630", "0930", "1231"]
    for p in cands:
        tail = p[4:]
        prev_tail = tails[tails.index(tail) - 1]  # Python 负索引: 0331 的上期=上年1231 ✔
        prev_y = int(p[:4]) - (1 if tail == "0331" else 0)
        start = formal_deadline(f"{prev_y}{prev_tail}") + timedelta(days=grace_days)
        end = forecast_open(p) - timedelta(days=1)
        if start <= d <= end:
            return {"period": p, "start": start, "end": end}
    return None


def per_stock_window(now, forecast_date: str | None, express_date: str | None,
                     actual_date: str | None, stock_type: str | None = None,
                     grace_days: int = 14) -> dict:
    """单股披露窗口状态机(纯)。forecast/express/actual = 当前周期(current_period)三环的
    公告日(str YYYY-MM-DD 或 None)。

    Returns {state, period, chip, days_to_formal}:
      closed      正式报已出(落袋;下一轮从年报/下期重新开始)
      windowA     预告或快报已落地(公告 ≤ 今天)且正式报未出 → chip=窗口A·预告落地/窗口A·快报落地
      windowB     季中预估窗内(仅 cyclic;非周期 idle——成长无信息优势不预估)
      disclosure  预告窗口内但该股预告未落地 → 披露季·等预告
      idle        其余(窗口间隙)
    """
    d = now.date() if isinstance(now, datetime) else now
    period = current_period(d)
    dl = formal_deadline(period)

    def _le(s: str | None) -> bool:
        return s is not None and str(s)[:10] <= d.isoformat()

    if _le(actual_date):
        return {"state": "closed", "period": period, "chip": "正式报已出",
                "days_to_formal": (dl - d).days}
    if _le(forecast_date) or _le(express_date):
        chip = "窗口A·快报落地" if _le(express_date) else "窗口A·预告落地"
        return {"state": "windowA", "period": period, "chip": chip,
                "days_to_formal": (dl - d).days}
    if forecast_open(period) <= d <= dl:
        # 预告截止(窗收)前 = 等预告落地;已过预告截止仍无预告 = 条件披露未触发,等正式报
        cutoff = forecast_open(period) + timedelta(days=_FORECAST_SPAN_DAYS)
        chip = "披露季·等预告" if d <= cutoff else "无预告·等正式报"
        return {"state": "disclosure", "period": period, "chip": chip,
                "days_to_formal": (dl - d).days}
    wb = window_b_period(d, grace_days)
    if wb and wb["period"] == period:
        if stock_type == "cyclic":
            return {"state": "windowB", "period": period, "chip": "窗口B·季中预估",
                    "days_to_formal": (dl - d).days}
        return {"state": "idle", "period": period, "chip": "季中·非周期不预估",
                "days_to_formal": (dl - d).days}
    return {"state": "idle", "period": period, "chip": "窗口间隙",
            "days_to_formal": (dl - d).days}
