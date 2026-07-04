from stockagent.config import get_config
from stockagent.report import compose_report

SYN = {
    "decision_date": "2026-07-02",
    "regime": "risk_on",
    "benchmark": {"symbol": "510300", "last": 4.85, "ma": 4.77, "above_ma": True},
    "ranking": [],
    "top_k": ["512480", "562500", "512880"],
    "target": {"512480": 1 / 3, "562500": 1 / 3, "512880": 1 / 3},
    "cash_weight": 0.0,
    "stopped": [],
    "actions": [
        {"type": "buy", "symbol": "512480", "weight": 1 / 3, "reason": "rotation_top_k"},
        {"type": "buy", "symbol": "562500", "weight": 1 / 3, "reason": "rotation_top_k"},
        {"type": "buy", "symbol": "512880", "weight": 1 / 3, "reason": "rotation_top_k"},
    ],
    "warnings": {"near_stop": [], "near_regime_line": False},
}


def test_report_contains_action_and_names():
    cfg = get_config()
    txt = compose_report(SYN, cfg, use_llm=False)
    assert "今日动作" in txt
    assert "半导体ETF" in txt  # name resolved from symbol
    assert "风险开" in txt
    assert "数据截至 2026-07-02" in txt


def test_report_zero_prediction():
    cfg = get_config()
    txt = compose_report(SYN, cfg, use_llm=False)
    for forbidden in ("预计", "有望", "看好", "后市"):
        assert forbidden not in txt


def test_report_no_action_when_all_hold():
    cfg = get_config()
    syn = dict(SYN)
    syn["actions"] = [{"type": "hold", "symbol": "512480", "weight": 1 / 3}]
    txt = compose_report(syn, cfg, use_llm=False)
    assert "无变动" in txt


def test_report_risk_off():
    cfg = get_config()
    syn = dict(SYN)
    syn["regime"] = "risk_off"
    syn["target"] = {"511990": 1.0}
    syn["actions"] = [{"type": "to_cash", "symbol": "511990", "weight": 1.0}]
    txt = compose_report(syn, cfg, use_llm=False)
    assert "风险关" in txt or "货币ETF避险" in txt


def test_strength_board_shown_for_reversion():
    cfg = get_config()
    syn = dict(SYN)
    syn["signal_name"] = "reversion"
    syn["strength_board"] = [
        {"symbol": "512480", "score": 0.8, "summary": "..."},
        {"symbol": "159819", "score": 0.7, "summary": "..."},
    ]
    txt = compose_report(syn, cfg, use_llm=False)
    assert "强势榜" in txt
    assert "非买卖信号" in txt
    assert "半导体ETF" in txt  # name resolved in the strength board


def test_strength_board_hidden_for_momentum():
    cfg = get_config()
    syn = dict(SYN)
    syn["signal_name"] = "momentum"
    syn["strength_board"] = [{"symbol": "512480", "score": 0.8, "summary": "..."}]
    txt = compose_report(syn, cfg, use_llm=False)
    assert "强势榜" not in txt  # dedup: candidate board already shows the same ranking
