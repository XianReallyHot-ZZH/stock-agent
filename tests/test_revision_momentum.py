"""E4 修正动量: revision_momentum(差分/翻滚防护/门) + A5 告警规则."""
import math

import pandas as pd

from stockagent.research import earnings as er
from stockagent.tracker import alerts


def _snap(rows: list[tuple], fy: int = 2026) -> pd.DataFrame:
    """rows: (code, n_reports, eps_fy1) → 快照契约."""
    df = pd.DataFrame([r[1:] for r in rows],
                      columns=["n_reports", "eps_fy1"], index=[r[0] for r in rows])
    df.index.name = "code"
    df["fy1_year"], df["fy2_year"] = fy, fy + 1
    return df


def _hold(weights: dict) -> pd.DataFrame:
    return pd.DataFrame({"code": list(weights), "weight": list(weights.values())})


# ---------- revision_momentum ----------
def test_revision_basic_diff():
    h = _hold({"600001": 50, "600002": 30, "600003": 20})
    then = _snap([("600001", 10, 2.0), ("600002", 5, 1.0), ("600003", 4, 4.0)])
    now = _snap([("600001", 10, 2.2),          # +10% 上修
                 ("600002", 5, 0.9),           # −10% 下修
                 ("600003", 4, 4.0)])          # 0
    r = er.revision_momentum(now, then, h)
    assert r["n_names"] == 3
    assert math.isclose(r["weighted_rev"], (0.10 * 50 - 0.10 * 30 + 0.0 * 20) / 100)
    assert r["n_up"] == 1 and r["n_dn"] == 1   # ±1% 带宽外才算
    assert math.isclose(r["coverage"], 1.0)


def test_revision_year_rollover_guard():
    """两份快照 fy1_year 不一致(年末翻滚) → 空信号, 不产出假修正."""
    h = _hold({"600001": 100})
    then = _snap([("600001", 10, 2.0)], fy=2026)
    now = _snap([("600001", 10, 3.0)], fy=2027)      # 新财年列, 非上修
    r = er.revision_momentum(now, then, h)
    assert r["n_names"] == 0 and math.isnan(r["weighted_rev"])


def test_revision_gates():
    h = _hold({"600001": 60, "600002": 40})
    then = _snap([("600001", 10, 2.0), ("600002", 10, 1.0)])
    now = _snap([("600001", 10, 2.2), ("600002", 10, 1.2)])
    # 研报数门: 600002 现在只剩 2 家研报 → 剔除
    now.loc["600002", "n_reports"] = 2
    r = er.revision_momentum(now, then, h)
    assert r["n_names"] == 1 and math.isclose(r["coverage"], 0.6)
    assert math.isclose(r["weighted_rev"], 0.10)
    # 负基数(then 亏损) → 无意义剔除(重建未变异的 now)
    now2 = _snap([("600001", 10, 2.2), ("600002", 10, 1.2)])
    then2 = _snap([("600001", 10, -1.0), ("600002", 10, 1.0)])
    r2 = er.revision_momentum(now2, then2, h)
    assert r2["n_names"] == 1                       # 只剩 600002


def test_revision_empty_inputs():
    empty = er.revision_momentum(None, None, None)
    assert empty["n_names"] == 0 and math.isnan(empty["weighted_rev"])
    h = _hold({"600001": 10})
    assert er.revision_momentum(_snap([("600001", 5, 1.0)]), pd.DataFrame(), h)["n_names"] == 0


# ---------- A5 告警 ----------
def _snap_alert(rev_w, cov=0.6):
    return {"name": "测试ETF", "style": "growth",
            "revision_w": rev_w, "revision_cov": cov,
            "revision_up": 3, "revision_dn": 17}


def test_a5_fires_on_big_downward_revision():
    out = alerts.evaluate({"512800": _snap_alert(-0.05)})
    a5 = [a for a in out if a["rule"] == "A5"]
    assert len(a5) == 1 and a5[0]["level"] == "warn"


def test_a5_silent_within_threshold():
    out = alerts.evaluate({"512800": _snap_alert(-0.02)})       # −2% < 3% 阈值
    assert not [a for a in out if a["rule"] == "A5"]


def test_a5_silent_upward_or_low_coverage():
    assert not [a for a in alerts.evaluate({"512800": _snap_alert(+0.08)}) if a["rule"] == "A5"]
    assert not [a for a in alerts.evaluate({"512800": _snap_alert(-0.05, cov=0.30)}) if a["rule"] == "A5"]
    # 冷启动期(无 revision_w 键) → 不告警
    assert not [a for a in alerts.evaluate({"512800": {"name": "x", "revision_status": "累积中(1/4)"}}) if a["rule"] == "A5"]
