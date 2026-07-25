"""Tests for ai_eval_server — 点击实时生成的本地服务端点。

起一个真实 ThreadingHTTPServer(随机端口,daemon 线程)+ requests 调,验证 /ai-eval 和
/health 端点。mock 整条链(diagnose_stock_full / attribution_by_year / llm_client.chat),
不依赖真实 DB/LLM。镜像 test_stock_commentary 的 _diag。
"""
from __future__ import annotations

import socket
import threading
from http.server import ThreadingHTTPServer

import pytest
import requests

import scripts.ai_eval_server as srv


def _diag(sym: str = "600519", primary: str = "value") -> dict:
    return {
        "symbol": sym, "price_last": 1297.41, "date_last": "2026-07-24",
        "pe_ttm": 13.68, "pb": 1.44,
        "classification": {"primary": primary, "secondary": []},
        "valuation_zone": {"zone": "低位·便宜", "pe_pct": 0.03, "pb_pct": 0.30, "valid": True},
        "features": {"revenue_cagr": 0.15, "profit_cagr": 0.20, "profit_vol": 0.08,
                     "div_yield": 0.035, "cagr_years": 5, "vol_years": 5},
        "davis": {"type": "double_play_setup", "label": "双击买点", "valid": True,
                  "profit_yoy_latest": 0.20, "profit_yoy_prev": 0.15,
                  "pe_change": 0.05, "pe_pct": 0.03},
        "pitfalls": {
            "net_profit": {"yoy": 0.20, "cagr2": 0.18, "abnormal": False, "trustworthy": 0.20,
                           "valid": True, "base": 100, "prev_base": 80},
            "revenue": {"yoy": 0.15, "cagr2": 0.14, "abnormal": False, "valid": True,
                        "base": 90, "prev_base": 78},
            "disclosure": {"latest_period": "20260331", "deadline": "2026-04-30",
                           "disclosed_by_asof": True}},
        "forecast": {"valid": False, "latest": None, "latest_sentiment": None,
                     "a1_deceleration": False, "a2_turn_bearish": False},
        "price_timing": {"trend": {"above_ma": True, "ma_trend_up": True, "valid": True},
                         "deviation": {"cur_dev": 0.02, "pct": 0.40, "valid": True},
                         "cross": {"direction": "up", "date": "2026-07-20", "bars_ago": 3},
                         "choppy": False},
        "valid": True,
    }


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def server(monkeypatch):
    """起真实服务(daemon 线程)。mock 整条链,不依赖 DB/LLM。"""
    monkeypatch.setattr(srv.sd, "diagnose_stock_full",
                        lambda sym, store, cfg, asof=None: _diag(sym))
    monkeypatch.setattr(srv.sc.sd, "attribution_by_year",
                        lambda *a, **k: [{"year": "2024", "earnings": 0.2, "valuation": 0.05,
                                           "dividend": 0.03, "total": 0.28}])
    monkeypatch.setattr(srv.llm_client, "provider_name", lambda: "glm")
    srv.NAMES = {"600519": "贵州茅台"}
    srv.CFG = object()
    srv.STORE = None
    srv.INDEX_DIAG = None
    port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), srv._Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port
    httpd.shutdown()
    httpd.server_close()


def _url(port: int, path: str, **params) -> str:
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return f"http://127.0.0.1:{port}{path}" + (f"?{q}" if q else "")


def test_ai_eval_clean_llm(server, monkeypatch):
    monkeypatch.setattr(srv.llm_client, "llm_available", lambda: True)
    monkeypatch.setattr(srv.llm_client, "chat", lambda prompt, system=None, max_tokens=4000:
                        "【估值】x\n【业绩与归因】x\n【择时位置】x\n【风险与避坑】x\n【行动建议】观望")
    d = requests.get(_url(server, "/ai-eval", sym="600519"), timeout=15).json()
    assert d["ok"] is True
    assert d["sym"] == "600519" and d["name"] == "贵州茅台"
    assert "观望" in d["text"]


def test_ai_eval_no_key_falls_back_to_template(server, monkeypatch):
    """无 key → stock_eval 内部兜底规则模板,仍 ok:true(不报错)。"""
    monkeypatch.setattr(srv.llm_client, "llm_available", lambda: False)
    d = requests.get(_url(server, "/ai-eval", sym="600519"), timeout=15).json()
    assert d["ok"] is True
    assert "【行动建议】" in d["text"]      # 规则模板五段
    assert "不构成投资建议" in d["text"]


def test_ai_eval_banned_word_falls_back(server, monkeypatch):
    """模型漏纯涨跌预测 → stock_eval 守门丢弃 → 规则模板。"""
    monkeypatch.setattr(srv.llm_client, "llm_available", lambda: True)
    monkeypatch.setattr(srv.llm_client, "chat", lambda prompt, system=None, max_tokens=4000:
                        "该股将上涨30%,目标价2000元,看涨,全仓买入。")
    d = requests.get(_url(server, "/ai-eval", sym="600519"), timeout=15).json()
    assert d["ok"] is True                  # 兜底,非 ok:false
    assert "将上涨" not in d["text"]        # 守门生效
    assert "【行动建议】" in d["text"]


def test_health(server, monkeypatch):
    monkeypatch.setattr(srv.llm_client, "llm_available", lambda: True)
    d = requests.get(_url(server, "/health"), timeout=10).json()
    assert d["ok"] is True
    assert d["provider"] == "glm"
    assert "贵州茅台" in d["pool"]


def test_unknown_sym_returns_error(server):
    d = requests.get(_url(server, "/ai-eval", sym="999999"), timeout=10).json()
    assert d["ok"] is False
    assert "不在观察池" in d["error"]


def test_cors_header_present(server):
    """file:// 看板跨域调 localhost 必需 ACAO:*。"""
    r = requests.get(_url(server, "/health"), timeout=10)
    assert r.headers.get("Access-Control-Allow-Origin") == "*"


def test_eval_sym_helper(monkeypatch):
    """直接测 _eval_sym(不起 HTTP):装配 + stock_eval + 兜底,返回 (name, text)。"""
    monkeypatch.setattr(srv.sd, "diagnose_stock_full",
                        lambda sym, store, cfg, asof=None: _diag(sym))
    monkeypatch.setattr(srv.sc.sd, "attribution_by_year",
                        lambda *a, **k: [{"year": "2024", "earnings": 0.2, "valuation": 0.05,
                                           "dividend": 0.03, "total": 0.28}])
    monkeypatch.setattr(srv.llm_client, "llm_available", lambda: False)
    srv.NAMES = {"600519": "贵州茅台"}
    srv.CFG = object(); srv.STORE = None; srv.INDEX_DIAG = None
    name, text = srv._eval_sym("600519")
    assert name == "贵州茅台"
    assert "【行动建议】" in text
