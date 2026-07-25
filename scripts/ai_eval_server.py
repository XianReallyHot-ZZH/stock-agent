"""个股 🤖 AI 评估本地服务(项目首个长驻进程)。

看板是纯静态 HTML,浏览器点 🤖 时无法直接调本地 Python、也读不到 .env 的 LLM key ——
故需要一个本地后端服务持 key 代为调 LLM。本服务用标准库 http.server(零新依赖),绑
127.0.0.1:8765(只本机、不对外,因 .env 有 LLM key)。前端 fetch /ai-eval?sym=<code>
实时生成单股评估(按需、单股、用点击当下的最新数据)。

用法:
  python scripts/ai_eval_server.py              # 起 http://127.0.0.1:8765(端口可 .env AI_EVAL_PORT 配)
  # 另开终端跑 stock_report.py 生成看板 → 打开 HTML → 点 🤖 实时生成(~10-30s)

端点:
  GET /ai-eval?sym=600519   → {ok, sym, name, text}  单股实时 AI 评估(调 LLM)
  GET /health               → {ok, provider, llm_available, pool}  探活 + provider 诊断
  OPTIONS *                 → 204 + CORS 头(预检)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.data.manager import DataManager
from stockagent.report import llm_client
from stockagent.tracker import stock_diagnose as sd
from stockagent.tracker import stock_commentary as sc
from stockagent.tracker import alerts
from stockagent.utils.logging_setup import setup_logging

log = logging.getLogger(__name__)

# 启动时初始化(main 里赋值,handler 只读 → 多线程 ThreadingHTTPServer 安全)
CFG = None
STORE = None
INDEX_DIAG = None
NAMES: dict = {}

_DEFAULT_PORT = 8765


def _eval_sym(sym: str) -> tuple[str, str]:
    """单股实时评估:diagnose_stock_full + evaluate_stocks(复用已算子结构,不重复 IO)+ stock_eval。

    返回 (name, 评估文本)。LLM 失败/无 key/含禁词 由 stock_eval 内部兜底规则模板,不抛。
    """
    asof = datetime.now().strftime("%Y-%m-%d")
    name = NAMES.get(sym, sym)
    d = sd.diagnose_stock_full(sym, STORE, CFG, asof=asof)
    single = {sym: {"name": name,
                    "forecast": d.get("forecast"),
                    "pitfalls": d.get("pitfalls"),
                    "price_timing": d.get("price_timing")}}
    alerts_list = alerts.evaluate_stocks(single, index_diag=INDEX_DIAG, asof=asof)
    text = sc.stock_eval({sym: d}, alerts_list, {sym: name}, STORE, use_llm=True).get(sym, "")
    return name, text


class _Handler(BaseHTTPRequestHandler):
    server_version = "ai-eval/1.0"

    def _cors(self):
        # 看板是 file:// 打开 → fetch localhost 跨域;origin 为 null,故回 *
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def _json(self, obj: dict, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._json({"ok": True,
                        "provider": llm_client.provider_name(),
                        "llm_available": llm_client.llm_available(),
                        "pool": list(NAMES.values())})
            return
        if path == "/ai-eval":
            sym = (parse_qs(parsed.query).get("sym") or [""])[0].strip()
            if sym not in NAMES:
                self._json({"ok": False,
                            "error": f"sym '{sym}' 不在观察池({','.join(NAMES.keys())})"})
                return
            try:
                name, text = _eval_sym(sym)
            except Exception as e:  # noqa: BLE001
                log.warning("/ai-eval %s 失败: %s", sym, str(e)[:160])
                self._json({"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"})
                return
            if not text:
                self._json({"ok": False, "error": "评估文本为空(LLM 无 key/失败 且规则模板也未生成)"})
                return
            self._json({"ok": True, "sym": sym, "name": name, "text": text})
            return
        self._json({"ok": False, "error": f"未知路径 '{path}'(可用: /ai-eval, /health)"}, code=404)

    def log_message(self, fmt, *args):
        # BaseHTTPRequestHandler 默认每请求打一行 stderr;改走 logging INFO(受 setup_logging 控级别)
        log.info("%s %s", self.address_string(), fmt % args)


def main():
    global CFG, STORE, INDEX_DIAG, NAMES
    setup_logging()
    CFG = get_config()
    STORE = Store(CFG.db_path)
    NAMES = {sym: DataManager.STOCK_NAMES.get(sym, sym) for sym in DataManager.STOCK_WATCHLIST}

    # 指数层诊断(E4 蓝筹vs成长 用)算一次缓存:大盘风格服务运行期不变,每天重启刷新。
    INDEX_DIAG = None
    try:
        from stockagent.tracker import diagnose as tdiag
        INDEX_DIAG = tdiag.diagnose_layer(STORE)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ 指数层诊断失败(E4 将不触发): {str(e)[:80]}")

    port = int(os.environ.get("AI_EVAL_PORT", str(_DEFAULT_PORT)))
    host = "127.0.0.1"
    srv = ThreadingHTTPServer((host, port), _Handler)
    print("=" * 58)
    print("  🤖 个股 AI 评估本地服务 (项目首个长驻进程)")
    print("=" * 58)
    print(f"  监听 http://{host}:{port}   (绑定 localhost,不对外;端口可 .env AI_EVAL_PORT 改)")
    print(f"  LLM provider={llm_client.provider_name()}  available={llm_client.llm_available()}")
    if not llm_client.llm_available():
        print("  ⚠ 未检测到 LLM key(.env 的 GLM/DEEPSEEK/OPENAI _API_KEY),🤖 将走规则模板兜底")
    print(f"  观察池 {len(NAMES)} 只: {', '.join(NAMES.values())}")
    print(f"  端点: GET /ai-eval?sym=<code>  |  GET /health")
    print(f"  看板 🤖 按钮需此服务在线;Ctrl+C 停止。")
    print("-" * 58)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止服务。")
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    main()
