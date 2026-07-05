"""Research commentary — LLM interpretation of the per-ETF 性价比 snapshot.

Same principle as report.llm_writer: numbers come from the scoring rules; the LLM only
EXPLAINS already-computed scores in 2-3 sentences, with a hard no-prediction constraint
(banned: 预计/有望/看好/后市/将会/预测). Falls back to a rule-based template if no LLM key
or if the model leaks a banned word.
"""
from __future__ import annotations

import json
import math
from typing import Optional

from ..report import llm_client

# Hard guard: if the model emits any of these, discard its output and use the template.
# Note: "预测" is intentionally NOT banned — the template's own disclaimer says "不含涨跌预测".
_BANNED = ("预计", "有望", "看好", "后市", "将会", "或涨", "或跌", "看涨", "看跌")

_SYSTEM = (
    "你是A股ETF行业研究的报告撰写助手。你只能根据给定的结构化评分事实写2-3句解读，"
    "用中文，说人话。绝对禁止预测涨跌、禁止使用'预计/有望/看好/后市/将会/预测'等判断词，"
    "只能解释'这三个因子为什么得出这个性价比分、机构筹码处于什么相位、意味着什么'。"
    "只引用给定数字与相位标签，不得编造任何数字，不要给出买卖建议。"
)


def _is_nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _fmt(v, pct: bool = False) -> str:
    if _is_nan(v):
        return "NA"
    return f"{v * 100:.0f}%" if pct else f"{v:.0f}"


def _facts_for(snap: dict, name: str, symbol: str) -> dict:
    return {
        "name": name,
        "symbol": symbol,
        "composite": _fmt(snap.get("composite")),
        "valuation_score": _fmt(snap.get("valuation")),
        "pe_percentile": _fmt(snap.get("pe_percentile"), pct=True),  # 0=最便宜
        "chip_phase": snap.get("chip_phase"),
        "reduction_from_peak": _fmt(snap.get("reduction_from_peak"), pct=True),
        "trend_score": _fmt(snap.get("trend")),
    }


def _template(snap: dict, name: str) -> str:
    """Rule-based fallback — always safe (no prediction), mirrors llm_writer style."""
    phase = snap.get("chip_phase") or "NA"
    pe = snap.get("pe_percentile")
    red = snap.get("reduction_from_peak")
    comp = snap.get("composite")
    pe_s = _fmt(pe, pct=True)
    red_s = _fmt(red, pct=True)
    comp_s = _fmt(comp)
    return (
        f"{name} 估值PE处5年{pe_s}分位（越低越便宜）、机构筹码相位「{phase}」"
        f"（距份额峰值{red_s}）、综合性价比{comp_s}。以上为规则输出，不含涨跌预测。"
    )


def commentary_llm(snapshots: dict, meta: dict) -> dict:
    """Per-symbol 2-3 sentence LLM interpretation. Returns {symbol: text}; empty if no key."""
    if not llm_client.llm_available():
        return {}
    out: dict[str, str] = {}
    for sym, snap in snapshots.items():
        name = meta.get(sym, {}).get("name", sym)
        facts = _facts_for(snap, name, sym)
        prompt = (
            "基于以下ETF行业研究评分写2-3句解读（解释三因子为什么得出这个性价比分、"
            "机构筹码相位的含义；不要复述全部数字、不要预测涨跌、不要买卖建议）：\n"
            + json.dumps(facts, ensure_ascii=False, indent=2)
        )
        try:
            txt = llm_client.chat(prompt, system=_SYSTEM)
        except Exception:  # noqa: BLE001
            txt = None
        if txt:
            txt = txt.strip()
            if any(b in txt for b in _BANNED):  # hard guard: model leaked a prediction
                txt = _template(snap, name)
            out[sym] = txt
    return out


def commentary(snapshots: dict, meta: dict, use_llm: bool = True) -> dict:
    """Returns {symbol: text}. LLM first; rule template fills any symbol the LLM skipped."""
    out = commentary_llm(snapshots, meta) if use_llm else {}
    for sym, snap in snapshots.items():
        if sym not in out:
            out[sym] = _template(snap, meta.get(sym, {}).get("name", sym))
    return out


def has_banned_word(text: str) -> bool:
    """Public so tests can assert the guard using the same banned list."""
    return any(b in (text or "") for b in _BANNED)


# ---------------- cross-pool summary (the LLM's real value-add) ----------------
def _num(x):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _pool_facts(snapshots: dict, meta: dict) -> dict:
    """Structured pool-level facts for the summary LLM. All grounded in computed scores."""
    ranked = [(s, sn) for s, sn in snapshots.items() if sn.get("data_sufficient", True)]
    excluded = [meta.get(s, {}).get("name", s) for s, sn in snapshots.items()
                if not sn.get("data_sufficient", True)]

    from collections import Counter
    phases = Counter(sn.get("chip_phase", "NA") for _, sn in ranked)

    def _pe(sn):
        return _num(sn.get("pe_percentile"))

    pe_vals = [p for _, sn in ranked if (p := _pe(sn)) is not None]
    cheap = sum(1 for p in pe_vals if p < 0.20)
    expensive = sum(1 for p in pe_vals if p > 0.80)
    mid_pe = len(pe_vals) - cheap - expensive
    no_pe = len(ranked) - len(pe_vals)

    def _row(sym, sn):
        return {
            "name": meta.get(sym, {}).get("name", sym),
            "composite": round(_num(sn.get("composite")), 0) if _num(sn.get("composite")) else None,
            "pe_pct": round(_pe(sn) * 100) if _pe(sn) is not None else None,
            "phase": sn.get("chip_phase"),
        }

    by_comp = sorted(ranked, key=lambda kv: (_num(kv[1].get("composite")) or -1), reverse=True)
    return {
        "n_participating": len(ranked),
        "n_excluded": len(excluded),
        "excluded_names": excluded,
        "phase_counts": dict(phases),
        "valuation": {"cheap_lt20pct": cheap, "mid": mid_pe, "expensive_gt80pct": expensive, "no_pe": no_pe},
        "top3": [_row(s, sn) for s, sn in by_comp[:3]],
        "bottom3": [_row(s, sn) for s, sn in by_comp[-3:]],
    }


_SUMMARY_SYSTEM = (
    "你是A股ETF行业研究的分析助手。基于给定的全池结构化评分事实，写一段3-5句的「全池格局」综合，"
    "用中文，说人话。要求：(1)只综合已算出的事实（相位集中度、估值分布、top/bottom），不得编造任何数字；"
    "(2)可指出跨标的的结构性特征（如'高估值被减、低估值被加'）；"
    "(3)绝对禁止预测涨跌、禁止'预计/有望/看好/后市/将会/看涨/看跌'等词，禁止任何买卖建议；"
    "(4)不要逐只复述，要归纳。"
)


def pool_summary_llm(snapshots: dict, meta: dict) -> str | None:
    """One LLM call synthesizing the whole pool. Returns text, or None if no key/fails guard."""
    if not llm_client.llm_available():
        return None
    facts = _pool_facts(snapshots, meta)
    prompt = ("基于以下全池评分事实写一段「全池格局」综合（3-5句，归纳跨标的特征，不预测、不建议、不逐只复述）：\n"
              + json.dumps(facts, ensure_ascii=False, indent=2))
    try:
        # Chinese tokenizes ~2 tokens/char; a rich 3-5 sentence summary can hit ~700 tokens,
        # so allow headroom to avoid mid-sentence truncation.
        txt = llm_client.chat(prompt, system=_SUMMARY_SYSTEM, max_tokens=1500)
    except Exception:  # noqa: BLE001
        return None
    if not txt:
        return None
    txt = txt.strip()
    if has_banned_word(txt):  # hard guard
        return None
    return txt


def _pool_template(snapshots: dict, meta: dict) -> str:
    """Deterministic fallback summary — grounded, no prediction."""
    f = _pool_facts(snapshots, meta)
    pc = f["phase_counts"]
    top = "、".join(f"{r['name']}{r['composite']:.0f}" for r in f["top3"] if r["composite"])
    bot = "、".join(f"{r['name']}{r['composite']:.0f}" for r in f["bottom3"] if r["composite"])
    dom = max(((k, v) for k, v in pc.items() if k != "数据不足"),
              key=lambda kv: kv[1], default=(None, 0))
    v = f["valuation"]
    excl = f"（{f['n_excluded']}只数据不足未参与）" if f["n_excluded"] else ""
    dom_str = f"筹码相位集中于「{dom[0]}」（{dom[1]}只）；" if dom[0] else "筹码相位分散；"
    return (f"本池 {f['n_participating']} 只参与排名{excl}。{dom_str}"
            f"估值分布：便宜(<20%分位){v['cheap_lt20pct']}只、贵(>80%){v['expensive_gt80pct']}只、"
            f"无PE {v['no_pe']}只。性价比前三 {top}；后三 {bot}。以上为规则输出，不含涨跌预测。")


def pool_summary(snapshots: dict, meta: dict, use_llm: bool = True) -> str:
    """Cross-pool格局综合。LLM first；失败/无key/含禁词 → 规则模板。"""
    if use_llm:
        txt = pool_summary_llm(snapshots, meta)
        if txt:
            return txt
    return _pool_template(snapshots, meta)
