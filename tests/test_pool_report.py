"""候选个股池 render 冒烟(合成 snapshot dict,无网络): section 序/锚点/sortable/浅色默认/
空数据降级/结论注入/徽标。照 test_research_report.py 的 HTML 标记断言惯例。"""
from stockagent.pool.report import render


def _full_snapshot() -> dict:
    return {
        "as_of": "2026-08-16", "period": "20260630",
        "cfg_note": {"trigger_pct": 0.05},
        "universe_stats": {"n": 2300, "n_typed": 1900, "n_cyclic": 400, "n_growth": 800,
                           "n_value": 700, "pct": 0.83},
        "price_freshness": {"fresh": 2280, "n": 2300},
        "spot_date": "20260816", "industry_snapshot": "2026-08-16", "n_snapshots": 4,
        "revision": {"rows": [{"code": "600519", "rev_pct": 5.2, "up": True, "down": False}],
                     "cold_start": None},
        "s1_rows": [{
            "code": "002460", "name": "赣锋锂业", "industry": "能源金属", "type": "cyclic",
            "dev_pct": 0.02, "cur_dev": -0.22, "stabilize": 1.0, "score": 88.0, "depth": 0.6,
            "guard_pass": True, "guard_flags": [], "suspect": False,
            "run_start": "2026-08-14", "days_in_run": 2,
        }, {
            "code": "600XXX", "name": "某股", "industry": "钢铁行业", "type": "cyclic",
            "dev_pct": 0.01, "cur_dev": -0.30, "stabilize": 0.6, "score": None, "depth": 0.8,
            "guard_pass": False, "guard_flags": ["预亏族(续亏)", "业绩变脸向下"],
            "suspect": True, "run_start": "2026-01-02", "days_in_run": 150,
        }],
        "s1_total": 2,
        "s2_rows": [{
            "code": "000630", "name": "铜陵有色", "industry": "有色金属", "type": "cyclic",
            "chip": "窗口A·预告落地", "days_to_formal": 15, "drawdown": -0.45,
            "recent_return": -0.03, "fierce": 0.75, "legs": "健康1.0×落后0.75",
            "confirmed": True, "variety": "铜",
        }],
        "s2_total": 1,
        "pead_rows": [{"code": "600519", "period": "20260630", "type": "预增",
                       "forecast_yoy": 30.0, "expected": 10.0, "surprise_pp": 20.0,
                       "leg": "A", "announce_date": "2026-07-10"}],
        "face_rows_up": [{"code": "300750", "name": "宁德时代", "industry": "电池",
                          "direction": "up", "kinds": ["turn_up"],
                          "detail": "同尾增速拐头 5%→20%(+15pp)",
                          "tail": [("20250630", 5.0), ("20260630", 20.0)]}],
        "face_rows_down": [{"code": "000XXX", "name": "某变脸股", "industry": "软件开发",
                            "direction": "down", "kinds": ["gear_down"],
                            "detail": "增速跳档 28%→5%(−23pp)",
                            "tail": [("20250630", 28.0), ("20260630", 5.0)]}],
        "conclusions": {"deviation": "偏离极值→20日前瞻: raw 52% | 企稳 61% | 基线 51% → 无 edge",
                        "pead": "PEAD 20日: 超预期 58% | 基线 51% → 温和分离"},
        "window_note": {"period": "20260930", "start": "2026-09-14", "end": "2026-09-30"},
        "davis_rows": [], "stage2_cards": [],
    }


# ---------- section 存在性与顺序 ----------
def test_sections_present_and_ordered():
    html = render(_full_snapshot())
    order = [html.find(x) for x in
             ("id='s1'", "id='s2'", "id='rev'", "id='pead'", "id='face'", "id='davis'", "id='stage2'")]
    assert all(o >= 0 for o in order)
    assert order == sorted(order)                       # 锚点 section 顺序
    assert "读图说明" in html and "候选个股池" in html


def test_anchor_nav_and_theme_toggle():
    html = render(_full_snapshot())
    assert "nav class='anchor'" in html
    assert "toggleTheme" in html and "pool-dark" in html


def test_sortable_markers():
    html = render(_full_snapshot())
    assert "table.sortable th[data-key]" in html           # CSS
    assert html.count("data-key=") >= 8                     # 表头排序键
    assert "th class='sortable'" not in html                # class 落在 table 上


# ---------- 数据行渲染 ----------
def test_s1_badges_and_guard_display():
    html = render(_full_snapshot())
    assert "badge-new" in html and ">新<" in html          # days_in_run=2 → 新徽标
    assert "badge-suspect" in html and "除权" in html
    assert "预亏族" in html                                  # 护栏 flags 显示
    assert "88" in html                                      # 复合分


def test_s2_window_chip_and_fierce():
    html = render(_full_snapshot())
    assert "窗口A·预告落地" in html and "窗口B" in html      # chip + 窗口B 注记
    assert "0.75" in html                                    # 猛分


def test_conclusions_injected():
    html = render(_full_snapshot())
    assert "无 edge" in html                                 # 策略1 实证结论
    assert "温和分离" in html                                 # PEAD 实证结论


def test_facechange_sections():
    html = render(_full_snapshot())
    assert "跳档" in html and "拐头" in html
    assert "▼ 向下" in html and "▲ 向上" in html


# ---------- 空数据降级 ----------
def test_empty_snapshot_degrades():
    html = render({"as_of": "2026-08-16", "period": "20260630"})
    assert "（无条目）" in html                              # 空表占位
    assert "未运行" in html                                   # validator 结论占位
    assert "累积中" not in html                               # 无 revision 键 → 无横幅


def test_revision_cold_start_banner():
    snap = _full_snapshot()
    snap["revision"] = {"rows": [], "cold_start": {"have": 1, "need": 4}}
    snap["n_snapshots"] = 1
    html = render(snap)
    assert "累积中 1/4" in html and "诚实降级" in html
