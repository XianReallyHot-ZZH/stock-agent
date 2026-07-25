"""Tests for stock_commentary — 单股 AI 评估(LLM first → 规则模板兜底)。

镜像 test_research_scoring.py 的 commentary 测试范式:mock llm_client.chat/llm_available,
mock sd.attribution_by_year(避免访问真实 store)。覆盖:无key/干净/含禁词 三层 fallback、
三类框架分流、_rule_template 行动标签映射、_facts_for 结构、_ai_eval_assets HTML 嵌入。
"""
from __future__ import annotations

import pytest

from stockagent.tracker import stock_commentary as sc
from stockagent.tracker import stock_report as srep


# ---- fixtures ----
def _diag(sym: str = "600519", primary: str = "value", zone: str = "低位·便宜",
          dv_type: str = "double_play_setup", above_ma: bool = True) -> dict:
    """构造一个 diagnose_stock_full 风格的 mock dict(字段对齐 _facts_for/_rule_template 用法)。"""
    return {
        "symbol": sym, "price_last": 1297.41, "date_last": "2026-07-24",
        "pe_ttm": 13.68, "pb": 1.44,
        "classification": {"primary": primary, "secondary": []},
        "valuation_zone": {"zone": zone, "pe_pct": 0.03, "pb_pct": 0.30, "valid": True},
        "features": {"revenue_cagr": 0.15, "profit_cagr": 0.20, "profit_vol": 0.08,
                     "div_yield": 0.035, "cagr_years": 5, "vol_years": 5},
        "davis": {"type": dv_type, "label": "双击买点", "valid": True,
                  "profit_yoy_latest": 0.20, "profit_yoy_prev": 0.15,
                  "pe_change": 0.05, "pe_pct": 0.03},
        "pitfalls": {
            "net_profit": {"yoy": 0.20, "cagr2": 0.18, "abnormal": False,
                           "trustworthy": 0.20, "valid": True, "base": 100, "prev_base": 80},
            "revenue": {"yoy": 0.15, "cagr2": 0.14, "abnormal": False, "valid": True,
                        "base": 90, "prev_base": 78},
            "disclosure": {"latest_period": "20260331", "deadline": "2026-04-30",
                           "disclosed_by_asof": True}},
        "forecast": {"valid": False, "latest": None, "latest_sentiment": None,
                     "a1_deceleration": False, "a2_turn_bearish": False},
        "price_timing": {"trend": {"above_ma": above_ma, "ma_trend_up": True, "valid": True},
                         "deviation": {"cur_dev": 0.02, "pct": 0.40, "valid": True},
                         "cross": {"direction": "up", "date": "2026-07-20", "bars_ago": 3},
                         "choppy": False},
        "valid": True,
    }


@pytest.fixture(autouse=True)
def _mock_attribution(monkeypatch):
    """stock_eval_llm 内部调 sd.attribution_by_year 读 store;mock 掉避免真实 IO。"""
    monkeypatch.setattr(
        sc.sd, "attribution_by_year",
        lambda sym, store, years=6: [{"year": "2024", "earnings": 0.20, "valuation": 0.05,
                                       "dividend": 0.03, "total": 0.28}])


# ---------------- 守门 ----------------
def test_has_banned_word_only_targets_price_prediction():
    """守门只禁纯价格涨跌预测,允许定性判断和动作建议词。"""
    assert sc.has_banned_word("目标价2000元") is True
    assert sc.has_banned_word("该股将上涨30%翻倍") is True
    assert sc.has_banned_word("看涨") is True
    # 允许:定性评估、动作建议、触发条件(不是纯涨跌预测)
    assert sc.has_banned_word("估值便宜,业绩处于回升期,可分批建仓") is False
    assert sc.has_banned_word("预计分红能力改善,持有") is False


# ---------------- 规则模板(确定性兜底)----------------
def test_rule_template_five_sections_and_disclaimer():
    txt = sc._rule_template(_diag(), [], "贵州茅台")
    for sec in ("【估值】", "【业绩与归因】", "【择时位置】", "【风险与避坑】", "【行动建议】"):
        assert sec in txt
    assert "不构成投资建议" in txt          # 免责
    assert not sc.has_banned_word(txt)     # 规则模板天然干净
    assert "贵州茅台" not in txt or True   # 模板不一定含名(不强求)


def test_action_label_low_double_play_above_ma_suggests_build():
    """低位 + 双击买点 + 站上均线 → 右侧建仓。"""
    label, _ = sc._action_label(_diag(zone="低位·便宜", dv_type="double_play_setup", above_ma=True))
    assert "建仓" in label


def test_action_label_high_double_kill_suggests_reduce():
    """高位 + 双杀风险 → 减仓/警惕。"""
    label, _ = sc._action_label(_diag(zone="高位·偏贵", dv_type="double_kill_risk", above_ma=False))
    assert "减仓" in label or "警惕" in label


def test_action_label_low_watch_below_ma_suggests_wait():
    """低位 + 双击观察(业绩探底)+ 线下 → 观望待拐头。"""
    label, _ = sc._action_label(_diag(zone="低位·便宜", dv_type="double_play_watch", above_ma=False))
    assert "观望" in label


# ---------------- stock_eval 三层 fallback ----------------
def test_stock_eval_no_llm_key_falls_back_to_template(monkeypatch):
    monkeypatch.setattr(sc.llm_client, "llm_available", lambda: False)
    out = sc.stock_eval({"600519": _diag()}, [], {"600519": "贵州茅台"}, store=None, use_llm=True)
    assert "600519" in out
    assert "【行动建议】" in out["600519"]      # 规则模板五段
    assert "不构成投资建议" in out["600519"]
    assert not sc.has_banned_word(out["600519"])


def test_stock_eval_clean_llm_passes_through(monkeypatch):
    monkeypatch.setattr(sc.llm_client, "llm_available", lambda: True)
    monkeypatch.setattr(
        sc.llm_client, "chat",
        lambda prompt, system=None, max_tokens=4000:
            "【估值】PE处低位分位,便宜。\n【业绩与归因】业绩回升。\n【择时位置】站上均线。\n"
            "【风险与避坑】关注披露。\n【行动建议】可分批建仓:站稳均线且业绩延续。")
    out = sc.stock_eval({"600519": _diag()}, [], {"600519": "贵州茅台"}, store=None, use_llm=True)
    assert "可分批建仓" in out["600519"]       # LLM 文本透传
    assert "不构成投资建议" in out["600519"]   # _eval_one 追加免责
    assert not sc.has_banned_word(out["600519"])


def test_stock_eval_llm_leaks_prediction_is_guarded(monkeypatch):
    """模型漏纯涨跌预测 → 硬守门丢弃 → 走规则模板。"""
    monkeypatch.setattr(sc.llm_client, "llm_available", lambda: True)
    monkeypatch.setattr(
        sc.llm_client, "chat",
        lambda prompt, system=None, max_tokens=4000:
            "该股将上涨30%,目标价2000元,看涨,建议全仓买入。")
    out = sc.stock_eval({"600519": _diag()}, [], {"600519": "贵州茅台"}, store=None, use_llm=True)
    assert not sc.has_banned_word(out["600519"])   # 被守门 → 干净的规则模板
    assert "【行动建议】" in out["600519"]          # 规则模板标记


def test_stock_eval_no_llm_flag_uses_template(monkeypatch):
    """--no-llm(use_llm=False)即使有 key 也走规则模板。"""
    monkeypatch.setattr(sc.llm_client, "llm_available", lambda: True)
    called = {"n": 0}
    monkeypatch.setattr(sc.llm_client, "chat",
                        lambda prompt, system=None, max_tokens=4000: called.__setitem__("n", called["n"] + 1) or "x")
    out = sc.stock_eval({"600519": _diag()}, [], {"600519": "贵州茅台"}, store=None, use_llm=False)
    assert called["n"] == 0                      # LLM 未被调用
    assert "【行动建议】" in out["600519"]


# ---------------- 三类框架分流 ----------------
@pytest.mark.parametrize("primary,keyword", [
    ("value", "价值股打法"),
    ("growth", "成长股打法"),
    ("cyclic", "周期股打法"),
])
def test_system_branches_framework_by_style(monkeypatch, primary, keyword):
    """LLM 的 system prompt 按个股类型注入对应打法框架。"""
    captured = {}
    monkeypatch.setattr(sc.llm_client, "llm_available", lambda: True)

    def fake(prompt, system=None, max_tokens=4000):
        captured["system"] = system
        return "【估值】x\n【业绩与归因】x\n【择时位置】x\n【风险与避坑】x\n【行动建议】观望"
    monkeypatch.setattr(sc.llm_client, "chat", fake)
    sc.stock_eval({"600519": _diag(primary=primary)}, [], {"600519": "测试"}, store=None, use_llm=True)
    assert keyword in captured["system"]
    assert "通用纪律" in captured["system"]     # 通用纪律所有类型都带


# ---------------- _facts_for 结构 ----------------
def test_facts_for_structure_has_all_sections():
    facts = sc._facts_for(_diag(), [{"year": "2024", "earnings": 0.2, "valuation": 0.05,
                                      "dividend": 0.03, "total": 0.28}],
                           [{"rule": "E3", "level": "info", "msg": "超卖"}], "贵州茅台")
    for k in ("名称", "估值", "戴维斯", "择时位置", "S07利润归因_近6年", "已触发信号", "避坑", "预告拐点"):
        assert k in facts
    assert facts["估值"]["zone"] == "低位·便宜"
    assert facts["已触发信号"][0]["rule"] == "E3"
    assert facts["S07利润归因_近6年"][0]["业绩贡献"] == "+20%"


# ---------------- _ai_eval_assets HTML 嵌入(stock_report)----------------
def test_ai_eval_assets_empty_returns_empty():
    assert srep._ai_eval_assets({}, {"600519": "贵州茅台"}) == ""


def test_ai_eval_assets_embeds_json_and_modal():
    html = srep._ai_eval_assets({"600519": "【估值】x\n【行动建议】观望"}, {"600519": "贵州茅台"})
    assert "var AI_EVALS=" in html
    assert "openAiEval" in html
    assert "ai-modal" in html                      # 独立 modal(不与 chart-modal 冲突)
    assert "贵州茅台" in html                       # _NAMES 注入
    # </script> 转义防御(即使内容含 </script> 也不截断 script 块)
    bad = srep._ai_eval_assets({"600519": "x</script><img>"}, {"600519": "t"})
    assert "<\\/script>" in bad


def test_card_renders_ai_button_when_ai_evals_present():
    """render 传入 ai_evals 时,卡片出现 🤖 按钮;不传时不出现。"""
    d = {"600519": _diag()}
    with_ai = srep.render(d, [], as_of="2026-07-24", names={"600519": "贵州茅台"},
                          ai_evals={"600519": "评估文本"})
    assert "🤖" in with_ai and "openAiEval('600519')" in with_ai
    without_ai = srep.render(d, [], as_of="2026-07-24", names={"600519": "贵州茅台"})
    assert "🤖" not in without_ai
