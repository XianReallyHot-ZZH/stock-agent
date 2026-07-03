"""Logging setup: console + rotating file under logs/."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import PROJECT_ROOT

_LOG_DIR = PROJECT_ROOT / "logs"
_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Windows console defaults to gbk and crashes on emoji/CJK in log messages.
    # Force UTF-8 so reports (📊, Chinese) print without UnicodeEncodeError.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    # console
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)
    # rotating file
    fh = RotatingFileHandler(_LOG_DIR / "stockagent.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    # quiet noisy libs
    for noisy in ("urllib3", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True
