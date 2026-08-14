"""Tests for stockagent.dashboard_home (四看板总入口壳页).

Covers nav_entries (mtime→as_of / 当日新鲜度 / 缺文件) and render_home
(壳页关键标记: iframe/导航 JSON/localStorage 键/深浅色)。模板布局本身不测。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from stockagent.dashboard_home import DASHBOARDS, nav_entries, render_home


def _touch(p: Path, dt: datetime) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    ts = dt.timestamp()
    os.utime(p, (ts, ts))


# ---------------- nav_entries ----------------

def test_nav_entries_missing_file(tmp_path):
    entries = nav_entries(tmp_path, now=datetime(2026, 8, 14, 18, 0))
    assert len(entries) == len(DASHBOARDS)
    for e in entries:
        assert e["exists"] is False
        assert e["asof"] == "未生成"
        assert e["asof_full"] == ""
        assert e["fresh"] is False
        assert e["cmd"].startswith("python scripts/")


def test_nav_entries_fresh_vs_stale(tmp_path):
    now = datetime(2026, 8, 14, 18, 0)
    _touch(tmp_path / "research_report.html", now - timedelta(hours=1))        # 今日 → fresh
    _touch(tmp_path / "index_timing.html", now - timedelta(days=2))            # 前日 → stale
    es = {e["key"]: e for e in nav_entries(tmp_path, now=now)}
    assert es["research"]["exists"] is True
    assert es["research"]["fresh"] is True
    assert es["research"]["asof"] == "08-14 17:00"
    assert es["research"]["asof_full"] == "2026-08-14 17:00"
    assert es["index_timing"]["fresh"] is False
    assert es["index_timing"]["asof"] == "08-12 18:00"
    assert es["stock"]["exists"] is False


def test_dashboards_keys_and_files_unique():
    keys = [k for k, *_ in DASHBOARDS]
    files = [f for _, f, *_ in DASHBOARDS]
    assert len(set(keys)) == len(keys)
    assert len(set(files)) == len(files)


# ---------------- render_home ----------------

def test_render_home_markers(tmp_path):
    now = datetime(2026, 8, 14, 18, 0)
    for _, fname, *_ in DASHBOARDS:
        _touch(tmp_path / fname, now)
    out = render_home(tmp_path)
    assert out == tmp_path / "index.html"
    html = out.read_text(encoding="utf-8")
    # 壳页关键结构: iframe + 导航数据 + 记忆键 + 深浅色默认浅色
    assert 'id="frame"' in html
    assert 'localStorage.getItem("sa_home_last")' in html or "sa_home_last" in html
    assert 'data-theme="light"' in html
    for _, fname, _, name, *_ in DASHBOARDS:
        assert fname in html
        assert name in html
    # 未注入占位符残留
    assert "__ITEMS_JSON__" not in html and "__GEN_AT__" not in html


def test_render_home_overwrites(tmp_path):
    a = render_home(tmp_path)
    b = render_home(tmp_path)
    assert a == b and b.exists()
