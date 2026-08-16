"""E2 一致预期聚合: aggregate_consensus(权重/门/负基数/buy_ratio) + consensus_score(带/门)."""
import math

import pandas as pd

from stockagent.research import earnings as er

PARAMS = {"research": {"earnings": {"consensus": {
    "min_reports": 3, "min_names": 5, "min_weight_cov": 0.40}}}}

_COLS = ["n_reports", "rating_buy", "rating_over", "rating_neutral",
         "rating_reduce", "rating_sell", "eps_fy1", "eps_fy2"]


def _cons(rows: list[tuple]) -> pd.DataFrame:
    """rows: (code, n_reports, buy, over, neutral, reduce, sell, eps_fy1, eps_fy2) → 快照契约.
    注意 code 必须是 6 位数字串(aggregate 会 zfill, 字母代码会被补零破坏匹配)."""
    df = pd.DataFrame([r[1:] for r in rows], columns=_COLS, index=[r[0] for r in rows])
    df.index.name = "code"
    df["fy1_year"], df["fy2_year"] = 2026, 2027
    return df


def _hold(weights: dict) -> pd.DataFrame:
    return pd.DataFrame({"code": list(weights), "weight": list(weights.values())})


# ---------- aggregate_consensus ----------
def test_aggregate_weighted_median_coverage():
    h = _hold({"600001": 40, "600002": 30, "600003": 20, "600004": 10})   # 600004 无覆盖
    s = _cons([("600001", 10, 8, 2, 0, 0, 0, 2.0, 2.4),        # g=+20%
               ("600002", 5, 4, 1, 0, 0, 0, 1.0, 1.1),         # g=+10%
               ("600003", 4, 2, 2, 0, 0, 0, 4.0, 3.6)])        # g=−10%
    sig = er.aggregate_consensus(h, s, min_reports=3)
    assert sig["n_names"] == 3 and sig["n_all"] == 4
    assert math.isclose(sig["coverage"], 90 / 100)             # 可用权重90 / 成分总权重100
    assert math.isclose(sig["weighted_g"], (0.20 * 40 + 0.10 * 30 - 0.10 * 20) / 90)
    assert math.isclose(sig["median_g"], 0.10)                  # median(20%,10%,−10%)
    assert math.isclose(sig["buy_ratio"], (8 * 40 + 4 * 30 + 2 * 20) / (10 * 40 + 5 * 30 + 4 * 20))
    assert (sig["fy1_year"], sig["fy2_year"]) == (2026, 2027)


def test_aggregate_min_reports_gate():
    """研报数 < min_reports 的名字剔除出可用集(覆盖偏差防护)."""
    h = _hold({"600001": 50, "600002": 50})
    s = _cons([("600001", 10, 8, 2, 0, 0, 0, 2.0, 2.4),
               ("600002", 2, 2, 0, 0, 0, 0, 1.0, 3.0)])         # 只有2家研报 → 剔除
    sig = er.aggregate_consensus(h, s, min_reports=3)
    assert sig["n_names"] == 1 and math.isclose(sig["coverage"], 0.5)
    assert math.isclose(sig["weighted_g"], 0.20) and math.isclose(sig["median_g"], 0.20)


def test_aggregate_loss_base_excluded():
    """eps_fy1 ≤ 0(亏损基数)剔除 — EPS 比值在负基数上无意义."""
    h = _hold({"600001": 50, "600002": 50})
    s = _cons([("600001", 10, 8, 2, 0, 0, 0, 2.0, 2.4),
               ("600002", 10, 8, 2, 0, 0, 0, -1.0, 1.0)])       # 扭亏股: 比值 −100% 无意义
    sig = er.aggregate_consensus(h, s)
    assert sig["n_names"] == 1 and math.isclose(sig["coverage"], 0.5)


def test_aggregate_missing_eps_excluded():
    h = _hold({"600001": 50, "600002": 50})
    s = _cons([("600001", 10, 8, 2, 0, 0, 0, 2.0, 2.4),
               ("600002", 10, 8, 2, 0, 0, 0, float("nan"), 1.0)])  # fy1 缺失
    sig = er.aggregate_consensus(h, s)
    assert sig["n_names"] == 1


def test_aggregate_empty_inputs():
    sig = er.aggregate_consensus(None, None)
    assert sig["n_names"] == 0 and sig["coverage"] == 0.0 and math.isnan(sig["weighted_g"])
    sig2 = er.aggregate_consensus(_hold({"600001": 1}), pd.DataFrame())
    assert sig2["n_names"] == 0 and sig2["n_all"] == 1


def test_aggregate_code_zfill_matches():
    """成分 code 未补零也能命中快照(csindex 原始数据偶有 int 型代码)."""
    h = pd.DataFrame({"code": ["600036"], "weight": [10.0]})
    s = _cons([("600036", 5, 4, 1, 0, 0, 0, 2.0, 2.4)])
    sig = er.aggregate_consensus(h, s)
    assert sig["n_names"] == 1


# ---------- consensus_score ----------
def test_score_bands():
    def _sig(med_g, cov=0.9, n=10):
        return {"median_g": med_g, "coverage": cov, "n_names": n}
    assert er.consensus_score(_sig(0.50), PARAMS)[1] == er.LABEL_C_HIGH       # 50+20=70
    assert er.consensus_score(_sig(0.25), PARAMS)[1] == er.LABEL_C_UP         # 50+10=60
    assert er.consensus_score(_sig(0.05), PARAMS)[1] == er.LABEL_C_FLAT       # 50+2=52
    assert er.consensus_score(_sig(-0.25), PARAMS)[1] == er.LABEL_C_DOWN      # 50-10=40
    assert er.consensus_score(_sig(-0.80), PARAMS)[1] == er.LABEL_C_CRASH     # 50-32=18


def test_score_insufficient_gates():
    assert er.consensus_score(None, PARAMS)[1] == er.LABEL_INSUFF
    low_cov = {"median_g": 0.5, "coverage": 0.39, "n_names": 10}
    assert er.consensus_score(low_cov, PARAMS)[1] == er.LABEL_INSUFF          # 覆盖权重门 40%
    few = {"median_g": 0.5, "coverage": 0.9, "n_names": 4}
    assert er.consensus_score(few, PARAMS)[1] == er.LABEL_INSUFF              # 成分数门 5


def test_score_default_params_when_missing():
    """params 无 consensus 键(旧配置) → .get 默认, 不崩."""
    sig = {"median_g": 0.25, "coverage": 0.9, "n_names": 10}
    score, label = er.consensus_score(sig, {})
    assert label == er.LABEL_C_UP and math.isclose(score, 60.0)


def test_score_clamped():
    sig = {"median_g": 5.0, "coverage": 0.9, "n_names": 10}                   # +500% 极端
    assert er.consensus_score(sig, PARAMS)[0] == 100.0
    sig2 = {"median_g": -5.0, "coverage": 0.9, "n_names": 10}
    assert er.consensus_score(sig2, PARAMS)[0] == 0.0
