"""政策日历纯函数(国内宏观看板 ③ · 只放事实,不做观点打分——那归 docs/CLAIMS_LEDGER.md)。

硬编码**典型时点**(历史惯例的近似,以官方公告为准):政治局经济会议 4/7/12 月、
中央经济工作会议 12 月中、央行货政报告季度、两会 3-05、LPR 每月 20 日、
金融统计数据(M2/社融)每月 10-15 日。给定今天,生成未来 ~13 个月的发生时点+倒计时。
"""
from __future__ import annotations

from datetime import date

# (名称, (月, 日) 或 (None, 日)=每月, 典型时点描述, 关注点)
POLICY_RULES: list[tuple] = [
    ("金融统计数据公布(M2/社融/信贷)", (None, 12), "每月 10-15 日(取 12 日近似)",
     "本看板①货币信用的数据腿;episode 状态机的输入"),
    ("LPR 报价", (None, 20), "每月 20 日(节假日顺延)",
     "贷款定价锚;5Y 关联按揭——政策利率的月度刻度"),
    ("两会·政府工作报告", (3, 5), "3 月 5 日",
     "GDP/赤字/就业目标 → 全年财政力度基凋"),
    ("央行货政报告(四季度)", (2, 15), "2 月中",
     "上季度货政执行报告;专栏=政策叙事风向"),
    ("政治局会议(经济)", (4, 30), "4 月末",
     "季度经济定调;政策转向第一现场"),
    ("央行货政报告(一季度)", (5, 15), "5 月中",
     "措辞变化(宽松/中性/克制)读法见 hint"),
    ("政治局会议(经济)", (7, 31), "7 月末",
     "年中定调;下半年政策基凋"),
    ("央行货政报告(二季度)", (8, 15), "8 月中",
     "每年最重篇幅之一(含下半年展望)"),
    ("央行货政报告(三季度)", (11, 15), "11 月中",
     "中央经济工作会议前哨"),
    ("政治局会议(经济)", (12, 5), "12 月初",
     "为中央经济工作会议铺垫定调"),
    ("中央经济工作会议", (12, 12), "12 月中",
     "次年全国经济定调——「M2 定大盘」叙事的『12月会议』原型"),
]


def _occurrences(rule: tuple, year: int) -> list[date]:
    name, (m, d), _, _ = rule
    if m is None:  # 每月规则
        return [date(year, mm, d) for mm in range(1, 13)]
    return [date(year, m, d)]


def policy_calendar(today: str | date, max_events: int = 12) -> list[dict]:
    """未来政策事件(纯):每条规则的**下一次时点**,按 date 升序前 max_events 个。
    Returns [{name, date 'YYYY-MM-DD', typical, note, days_left}]。
    硬编码典型时点,以官方公告为准;只放事实,观点结算归 CLAIMS_LEDGER。"""
    t = today if isinstance(today, date) else date(*map(int, str(today).split("-")))
    events: list[dict] = []
    for rule in POLICY_RULES:
        name, _, typical, note = rule
        occ = []
        for y in (t.year, t.year + 1):
            occ.extend(_occurrences(rule, y))
        occ = [d for d in occ if d >= t]
        if not occ:
            continue
        d = min(occ)
        events.append({"name": name, "date": d.isoformat(),
                       "typical": typical, "note": note,
                       "days_left": (d - t).days})
    events.sort(key=lambda e: (e["date"], e["name"]))
    return events[:max_events]
