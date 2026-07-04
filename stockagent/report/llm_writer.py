"""Morning report writer (Q12/Q13): engine signal dict -> three-section text.

Critical design: action numbers/state are produced by a DETERMINISTIC template
(never the LLM). The LLM, when available, only writes the 🔍 commentary section,
strictly grounded in the provided facts, with a hard "no prediction" instruction.
No LLM key -> template commentary. Report never predicts price direction.
"""
from __future__ import annotations

from typing import Optional

from ..config import Config
from . import llm_client

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _name(meta: dict, symbol: str) -> str:
    return meta.get(symbol, {}).get("name", symbol)


def _action_line(signal: dict, meta: dict) -> tuple[str, str]:
    """Return (headline, reason) for the action section."""
    acts = signal.get("actions", [])
    buys = [a for a in acts if a["type"] == "buy"]
    sells = [a for a in acts if a["type"] in ("sell", "to_cash")]
    stopped = set(signal.get("stopped", []))

    if signal.get("regime") == "risk_off" and any(a["type"] in ("sell", "to_cash") for a in acts):
        return "避险：全部转入货币ETF", "沪深300 跌破120日半年线，按规则空仓避险"

    sig = signal.get("signal_name", "momentum")
    parts = []
    if sells:
        names = "、".join(_name(meta, a["symbol"]) for a in sells)
        why = "触发止损" if all(a["symbol"] in stopped for a in sells) else "得分掉出前列/轮动换出"
        parts.append(f"卖出 {names}（{why}）")
    if buys:
        names = "、".join(_name(meta, a["symbol"]) for a in buys)
        parts.append(f"买入 {names}（新进{sig}前列）")
    if not parts:
        target = signal.get("target", {})
        risk_off_sym = signal.get("risk_off_symbol")
        if signal.get("regime") == "risk_on" and not any(s != risk_off_sym for s in target):
            gate = "close>250日线 且 RSI<30" if sig == "reversion" else "close>60日线"
            return "无变动，维持空仓", f"风险开但无板块同时满足{sig}门槛（{gate}）→ 空仓观望"
        return "无变动，维持持有", "今日无任何触发（未轮动、未止损、未避险）"
    return "；".join(parts), "按规则执行，无需主观判断"


def _state_line(signal: dict, meta: dict, cfg: Config) -> str:
    regime = signal.get("regime")
    target = signal.get("target", {})
    risk_off = cfg.params["regime"]["risk_off_symbol"]
    only_cash = (len(target) == 0) or (len(target) == 1 and risk_off in target)
    if regime == "risk_off":
        return "风险关 · 大盘破位避险（100% 货币ETF）"
    if only_cash:
        return "风险开 · 无合格标的·空仓观望（100% 货币ETF）"
    holds = [f"{_name(meta, s)}({round(w*100)}%)" for s, w in target.items() if s != risk_off]
    cash = signal.get("cash_weight", 0)
    line = f"风险开 · 持仓 {' '.join(holds)}" if holds else "风险开 · 空仓"
    if cash > 0.01:
        line += f" | 现金{round(cash*100)}%"
    return line


def _bench_line(signal: dict) -> str:
    b = signal.get("benchmark", {})
    last = b.get("last"); ma = b.get("ma"); above = b.get("above_ma")
    if last is None or ma is None:
        return "大盘数据不足"
    pos = "在" if above else "跌破"
    status = "正常" if above else "避险中"
    dist = b.get("distance_pct")
    dist_str = f"（偏离{dist:+.1%}）" if dist is not None else ""
    near = signal.get("warnings", {}).get("near_regime_line", False)
    flag = " ⚠逼近半年线" if near else ""
    return f"沪深300 {last} {pos}120日线({ma}){dist_str}，{status}{flag}"


def _holdings_line(signal: dict, meta: dict) -> str:
    hd = signal.get("holdings_detail") or []
    if not hd:
        return "（当前空仓，无持仓）"
    out = []
    for h in hd:
        nm = _name(meta, h["symbol"])
        w = round(h.get("weight", 0) * 100)
        dd = h.get("drawdown_from_peak")
        dd_str = f" | 自高点 {round(dd*100)}%" if dd is not None else ""
        out.append(f"  • {nm}({h['symbol']}) {w}% | {h.get('summary','')}{dd_str}")
    return "\n".join(out)


def _candidates_line(signal: dict, meta: dict) -> str:
    det = signal.get("details") or []
    if not det:
        return "（无候选数据）"
    out = []
    for d in det:
        nm = _name(meta, d["symbol"])
        sc = d.get("score")
        sc_str = f"{sc:+.2f}" if isinstance(sc, (int, float)) else "NA"
        mark = "✓达标" if d.get("eligible") else "✗未达标"
        out.append(f"  • {nm}({d['symbol']}) 得分{sc_str} | {d.get('summary','')} [{mark}]")
    return "\n".join(out)


def _strength_line(signal: dict, meta: dict) -> str:
    """Market-observation strength board (always momentum). NOT a trade signal."""
    sb = signal.get("strength_board") or []
    if not sb:
        return "（无数据）"
    out = []
    for i, d in enumerate(sb, 1):
        nm = _name(meta, d["symbol"])
        sc = d.get("score")
        sc_str = f"{sc:+.2f}" if isinstance(sc, (int, float)) else "NA"
        out.append(f"  {i}. {nm}({d['symbol']}) 得分{sc_str} | {d.get('summary','')}")
    return "\n".join(out)


def _watch_line(signal: dict, meta: dict) -> str:
    ns = signal.get("warnings", {}).get("near_stop", [])
    if not ns:
        return "无持仓逼近止损线"
    return "；".join(f"{_name(meta, w['symbol'])} 距高点 {round(w['drawdown_from_peak']*100)}% 逼近止损" for w in ns)


def _commentary_llm(signal: dict, meta: dict, cfg: Config) -> Optional[str]:
    """Ask GLM to write 2-3 sentences of commentary, grounded + no-prediction."""
    top = signal.get("top_k", [])
    sig = signal.get("signal_name", "momentum")
    gate = ("close>250日线 且 RSI<30（长期上升+短期超跌）" if sig == "reversion"
            else "close>60日线 且 动量分领先")
    facts = {
        "decision_date": signal.get("decision_date"),
        "signal": sig,
        "buy_rule": gate,
        "regime": signal.get("regime"),
        "benchmark": signal.get("benchmark"),
        "top_k": [_name(meta, s) for s in top],
        "stopped": [_name(meta, s) for s in signal.get("stopped", [])],
        "actions": signal.get("actions"),
    }
    import json

    system = (
        "你是A股板块轮动策略的报告撰写助手。你只能根据给定的结构化事实写2-3句简评，"
        "用中文，说人话。绝对禁止预测涨跌、禁止'预计/有望/看好/后市'等判断，"
        "只能解释'基于规则、发生了什么、因此今天该做什么'。只引用给定数字，不得编造。"
    )
    prompt = (
        "基于以下引擎输出写简评（2-3句，不要复述全部数字，抓重点解释为什么是这个动作）：\n"
        + json.dumps(facts, ensure_ascii=False, indent=2)
    )
    return llm_client.chat(prompt, system=system)


def _commentary_template(signal: dict, meta: dict) -> str:
    top = signal.get("top_k", [])
    regime = signal.get("regime")
    stopped = signal.get("stopped", [])
    sig = signal.get("signal_name", "momentum")
    is_rev = (sig == "reversion")
    if regime == "risk_off":
        return "大盘处于下行趋势，系统按规则空仓避险，等待沪深300重回120日线上方再恢复轮动。"
    if not top:
        return ("无板块同时满足'长期上升(>250日线)且短期超跌(RSI<30)'，系统空仓观望，避免在下跌中接飞刀。"
                if is_rev else
                "无板块站上趋势确认门槛，系统空仓观望，避免在弱势中硬选。")
    names = "、".join(_name(meta, s) for s in top)
    pick = "长期上升中短期超跌、等待反弹" if is_rev else "近期涨幅领先"
    s = f"系统按{'均值回归' if is_rev else '动量'}选中 {names}（{pick}）作为当前持仓。"
    if stopped:
        s += f"其中 {'、'.join(_name(meta, x) for x in stopped)} 触发止损已剔除，资金暂存货币池。"
    s += "以上为规则输出，不含任何涨跌预测。"
    return s


def compose_report(signal: dict, cfg: Config, use_llm: bool = True) -> str:
    meta = cfg.symbol_meta()
    d = signal.get("decision_date", "")
    wd = WEEKDAY_CN[__import__("datetime").datetime.strptime(d, "%Y-%m-%d").weekday()] if d else ""

    headline, reason = _action_line(signal, meta)
    commentary = None
    if use_llm and llm_client.llm_available():
        commentary = _commentary_llm(signal, meta, cfg)
    if not commentary:
        commentary = _commentary_template(signal, meta)

    adh = signal.get("adherence") or {}
    adh_line = ""
    if adh.get("available"):
        adh_line = f"\n🧭 自律度：{adh['adherence_pct']}%（实际持仓 vs 目标偏离 {adh['total_drift']}）"

    sig_name = signal.get("signal_name", "momentum")
    n_det = len(signal.get("details") or [])
    # Strength board is hidden when the active signal is momentum (candidate board
    # already shows the same ranking) to avoid redundancy.
    strength_block: list[str] = []
    if sig_name != "momentum":
        strength_block = [
            "━━━━━━━━━━━━",
            "🔥 强势榜（仅市场观察·非买卖信号，按动量排名前6）：",
            _strength_line(signal, meta),
        ]
    lines = [
        f"📊 {d} {wd} 早盘报告 [{sig_name}]",
        "━━━━━━━━━━━━",
        f"🟢 今日动作：{headline}",
        f"   理由：{reason}",
        "━━━━━━━━━━━━",
        f"📊 组合：{_state_line(signal, meta, cfg)}",
        f"📈 大盘：{_bench_line(signal)}",
        f"⚠️ 盯防：{_watch_line(signal, meta)}" + adh_line,
        "━━━━━━━━━━━━",
        "📋 持仓明细：",
        _holdings_line(signal, meta),
        f"🏆 候选榜（{sig_name} 得分前{n_det}）：",
        _candidates_line(signal, meta),
        *strength_block,
        "━━━━━━━━━━━━",
        f"🔍 简评：{commentary}",
        f"（数据截至 {d} 收盘 · 信号={sig_name} · 源：{('LLM' if (use_llm and llm_client.llm_available()) else '规则模板')}）",
    ]
    return "\n".join(lines)
