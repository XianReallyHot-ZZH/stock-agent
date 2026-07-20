"""信号提醒规则库(九条 MVP)— Phase 1-A。

双通道:看板顶部告警区 + 微信推送(同源 evaluate 输出)。
evaluate 接收 ETF snapshots + 指数层 diagnose,返回 alert 列表。

九条(PRD §8.2):
  D   筹码相位转兑现中段/见顶预警(空)、见底/低位加仓(多)
  E1  60日线有效突破 → 右侧买点(震荡市抑制)
  E2  60日线有效跌破 → 止盈止损
  C1/C2 周期 PB 触底/顶 —— 当前跳过:板块 PB 无数据源(cyclic 不估值),待 Phase 2
  B1  价值股息率偏高(>5%)→ 买入窗口
  A1/A2 业绩预告承压/恶化 → 抱着颗雷/戴维斯双杀前兆
  F1  沪深300 在60日线下且均线向下 → 风险开关
"""
from __future__ import annotations

import math

_DIV_THRESHOLD = 0.05  # B1: 股息率 > 5% 视为偏高(买入窗口)


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


# 筹码相位 → D 告警(重远 6 相位)
_D_BEAR = {"兑现中段", "见顶预警", "高位加仓", "高位减仓"}   # 机构兑现/见顶 → 看空
_D_BULL = {"见底", "低位加仓", "加仓"}                      # 见底/加仓 → 看多
# 业绩预告 → A1/A2 告警
_EARN_BEAR = {"业绩承压", "业绩恶化"}


def evaluate(etf_snapshots: dict, index_diag: dict | None = None) -> list[dict]:
    """评估九条规则,返回 alert 列表 [{level, scope, rule, msg}]。
    level: 'info'(看多/机会)/ 'warn'(看空/风险)。"""
    alerts: list[dict] = []

    # ---- 指数层(E1/E2/F1)----
    if index_diag:
        for sym, info in index_diag.get("indices", {}).items():
            if not info.get("valid"):
                continue
            dg = info["diagnosis"]; bo = dg["breakout"]
            # E1: 有效突破(震荡市抑制 — 趋势信号是噪音)
            if bo["direction"] == "up" and bo["grade"] >= 2 and not dg["choppy"]:
                alerts.append({"level": "info", "scope": f"指数·{info['name']}", "rule": "E1",
                               "msg": f"{info['name']} 60日线有效突破(grade{bo['grade']})→ 右侧买点"})
            # E2: 有效跌破(震荡市仍提示,但标注谨慎)
            elif bo["direction"] == "down" and bo["grade"] >= 2:
                chop = "(震荡市,信号谨慎)" if dg["choppy"] else ""
                alerts.append({"level": "warn", "scope": f"指数·{info['name']}", "rule": "E2",
                               "msg": f"{info['name']} 60日线有效跌破(grade{bo['grade']})→ 止盈止损{chop}"})
        # F1: 沪深300 大盘风险开关
        hs = index_diag.get("indices", {}).get("000300", {})
        if hs.get("valid"):
            t = hs["diagnosis"]["trend"]
            if t["above_ma"] is False and t["ma_trend_up"] is False:
                alerts.append({"level": "warn", "scope": "大盘", "rule": "F1",
                               "msg": "沪深300 在60日线下且均线向下 → 风险开关(趋势信号谨慎)"})

    # ---- ETF 层(D/B1/A1A2)----
    for sym, snap in etf_snapshots.items():
        nm = snap.get("name", sym)
        # D: 筹码相位
        phase = snap.get("chip_phase", "")
        if phase in _D_BEAR:
            alerts.append({"level": "warn", "scope": nm, "rule": "D",
                           "msg": f"筹码相位「{phase}」→ 机构兑现/见顶"})
        elif phase in _D_BULL:
            alerts.append({"level": "info", "scope": nm, "rule": "D",
                           "msg": f"筹码相位「{phase}」→ 见底/低位加仓"})
        # B1: 价值股息率偏高
        if snap.get("style") == "value":
            dy = snap.get("dividend_yield")
            if not _nan(dy) and dy > _DIV_THRESHOLD:
                alerts.append({"level": "info", "scope": nm, "rule": "B1",
                               "msg": f"股息率 {dy:.1%} 偏高(>{_DIV_THRESHOLD:.0%})→ 买入窗口"})
        # A1/A2: 业绩预告承压/恶化
        elabel = snap.get("earnings_label", "")
        if elabel in _EARN_BEAR:
            yoy = snap.get("earnings_yoy")
            yoy_s = f"(yoy {yoy:+.0f}%)" if not _nan(yoy) else ""
            alerts.append({"level": "warn", "scope": nm, "rule": "A1/A2",
                           "msg": f"业绩预告「{elabel}」{yoy_s} → 抱着颗雷/戴维斯双杀前兆"})

    return alerts


def format_for_push(alerts_list: list, title_prefix: str = "📡 信号提醒") -> tuple[str, str]:
    """把 alerts 渲染成推送友好文本(双通道之微信)。返回 (title, text)。
    无触发时返回空 title(调用方可跳过推送)。"""
    if not alerts_list:
        return ("", "")
    warns = [a for a in alerts_list if a["level"] == "warn"]
    infos = [a for a in alerts_list if a["level"] == "info"]
    title = f"{title_prefix}(⚠{len(warns)} 💡{len(infos)})"
    lines = [f"**{title}**", ""]
    for a in alerts_list:
        icon = "⚠" if a["level"] == "warn" else "💡"
        lines.append(f"{icon} **[{a['rule']}] {a['scope']}**\n{a['msg']}")
    return (title, "\n".join(lines))
