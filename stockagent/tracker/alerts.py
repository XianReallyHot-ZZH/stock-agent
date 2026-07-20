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


# 筹码相位 → D 告警(重远 6 相位)。注意:位置决定"加仓"含义 ——
# 低位加仓=机会(底部建仓),高位加仓=风险(拉高出货/接盘,易被误读成利好)。
_D_BULL = {"见底", "低位加仓", "加仓"}
_D_PHASE_MSG = {
    "兑现中段":   "机构在中部兑现 → 看空(无性价比)",
    "见顶预警":   "高位+机构停滞 → 见顶风险",
    "高位加仓":   "高位+机构仍在买 → 警惕拉高出货/接盘(不是机会!)",
    "高位减仓":   "高位+机构抛 → 明确出货",
    "见底":       "深底+卖盘枯竭 → 见底(看多)",
    "低位加仓":   "深底+聪明钱进场 → 最强看多(机会)",
    "加仓":       "中部+机构加仓 → 偏多",
    "下跌末段":   "深底+机构仍在抛 → 未确认(可能见底前最后抛,也可能继续跌)",
    # "观望"(mid_stable)= 中部+机构停滞 → 中性、无强信号,不触发告警(alerts 只推 actionable)
}
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
        # D: 筹码相位 × 估值分位交叉(综合筹码+价格才准)
        # 只看筹码会遗漏价格:高位筹码+低估值=好资产被错杀(不是风险!)
        phase = snap.get("chip_phase", "")
        if phase in _D_PHASE_MSG:
            pe_pct = snap.get("pe_percentile")
            has_pe = pe_pct is not None and not _nan(pe_pct)
            chip_high = phase in ("高位加仓", "高位减仓", "见顶预警")
            chip_low = phase in ("低位加仓", "见底", "下跌末段")
            if has_pe and (chip_high or chip_low):
                val_low, val_high = pe_pct < 0.30, pe_pct > 0.70
                if chip_high and val_low:
                    alerts.append({"level": "info", "scope": nm, "rule": "D",
                        "msg": f"筹码高位(机构重仓)+估值低位({pe_pct:.0%})→ 好资产被错杀,潜在反弹"})
                elif chip_high and val_high:
                    alerts.append({"level": "warn", "scope": nm, "rule": "D",
                        "msg": f"筹码高位+估值高位({pe_pct:.0%})→ 拉高出货/见顶风险"})
                elif chip_low and val_low:
                    alerts.append({"level": "info", "scope": nm, "rule": "D",
                        "msg": f"筹码低位+估值低位({pe_pct:.0%})→ 见底信号(机构可能重新进场)"})
                elif chip_low and val_high:
                    alerts.append({"level": "warn", "scope": nm, "rule": "D",
                        "msg": f"筹码低位+估值高位({pe_pct:.0%})→ 机构已跑+估值贵(危险)"})
                else:  # 筹码极端但估值中部 → 按筹码单维
                    level = "info" if phase in _D_BULL else "warn"
                    alerts.append({"level": level, "scope": nm, "rule": "D",
                        "msg": f"筹码「{phase}」+估值中位({pe_pct:.0%})→ {_D_PHASE_MSG[phase]}"})
            else:  # 无PE(cyclic/宽基)或筹码中部 → 按筹码单维
                level = "info" if phase in _D_BULL else "warn"
                alerts.append({"level": level, "scope": nm, "rule": "D",
                    "msg": f"筹码相位「{phase}」→ {_D_PHASE_MSG[phase]}"})
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
