"""候选个股池 render 冒烟(V8 高业绩池,合成 snapshot dict,无网络): section 序/锚点/
sortable/浅色默认/空数据降级/结论注入/黄旗/SOP/涌现簇。照 test_research_report.py 的
HTML 标记断言惯例。"""
from stockagent.pool.report import render


def _full_snapshot() -> dict:
    return {
        "as_of": "2026-08-16", "period": "20260630",
        "universe_stats": {"n": 5100, "n_typed": 4200, "n_cyclic": 900, "n_growth": 1500,
                           "n_value": 1800, "pct": 0.82},
        "spot_date": "2026-08-16", "industry_snapshot": "2026-08-01",
        "n_floor_pass": 412, "n_gated_pool": 2, "n_track_peg": 1, "n_track_pb": 1,
        "clock": {"period": "20260630", "formal_deadline": "2026-08-31",
                  "days_to_formal": 15, "forecast_open": "2026-07-01",
                  "ring_mix": {"forecast": 180, "express": 40, "actual": 192},
                  "theme_windows": [{"label": "商品周期兑现窗", "until": "2026-09-30"}]},
        "rows": [
            {"code": "002460", "name": "赣锋锂业", "industry": "能源金属", "type": "cyclic",
             "commodity_variety": "碳酸锂", "mktcap": 3.2e10, "spot_close": 8.0,
             "ring": "actual", "announce_date": "2026-08-20", "np_yoy_bulk": 60.0,
             "rev_yoy": 30.0, "np_axis": "reported", "flags": ["未精筛"],
             "track": "pb", "score": 0.04, "score_note": "", "pe_ttm": None,
             "pb_pct": 0.04, "rank": 2, "track_rank": 1.0,
             "red": [], "yellow": ["未精筛", "商誉激增(并购代理)"],
             "metrics": {"goodwill_jump": 0.6}},
            {"code": "300750", "name": "宁德时代", "industry": "电池", "type": "growth",
             "commodity_variety": None, "mktcap": 8.0e11, "spot_close": 200.0,
             "ring": "actual", "announce_date": "2026-08-24", "np_yoy_bulk": 75.0,
             "rev_yoy": 40.0, "np_axis": "deducted", "flags": [],
             "track": "peg", "score": 0.97, "score_note": "", "pe_ttm": 72.7,
             "peg": 0.97, "rank": 1, "track_rank": 1.0,
             "red": [], "yellow": [], "metrics": {}},
        ],
        "gaps": {"sina 未拉": 405, "红旗剔除(存贷双高(代理口径))": 3},
        "sina_needed": ["002460"],
        "composition": {"by_industry": [{"industry": "电池", "n": 1, "share": 0.5},
                                        {"industry": "能源金属", "n": 1, "share": 0.5}],
                        "n_industries": 2, "by_type": {"growth": 1, "cyclic": 1},
                        "n_total": 2, "n_untyped": 0},
        "emergent": None,
        "diff": {"entered": [{"code": "300750", "name": "宁德时代"}],
                 "exited": [{"code": "600519", "name": "贵州茅台"}]},
        "history": [{"asof": "2026-08-16", "n": 2, "period": "20260630"}],
        "conclusion": "高业绩池回放 18 窗: 跑赢全部宽基的窗口占比 56%;小样本 n=18,温度计非开关",
        "cfg_note": {"floor_np_yoy": 50.0, "floor_rev_yoy": 20.0, "top_n": 100,
                     "peg_max": 1.0, "pb_pct_max": 0.30, "mktcap_filter_on": False},
    }


def test_render_sections_and_anchors():
    html = render(_full_snapshot())
    # 六节 + 锚点
    for frag in ("id='pool'", "id='sector'", "id='risk'", "id='clock'",
                 "id='history'", "id='guide'"):
        assert frag in html, frag
    # 标题与主轴措辞
    assert "高业绩池" in html and "陈氏季度池" in html
    # 池行: 两只都在,黄旗渲染
    assert "赣锋锂业" in html and "宁德时代" in html
    assert "商誉激增(并购代理)" in html
    # 涌现簇空态横幅(50/50 仓位对照已删——用户裁定 2026-09-12)
    assert "无涌现簇" in html
    assert "50% 该簇" not in html        # 仓位配比对照措辞不出现
    # diff 与主题死线
    assert "商品周期兑现窗" in html and "2026-09-30" in html
    assert "新进 <b>1</b>" in html and "淘汰 <b>1</b>" in html
    # 结论注入
    assert "跑赢全部宽基的窗口占比 56%" in html
    # SOP 教学层
    assert "人工复审 SOP" in html and "洗大澡" in html
    # 只读围栏
    assert "永不喂交易引擎" in html
    # sortable 表 + 浅色默认
    assert "table.sortable" in html and "body.dark" in html


def test_render_empty_snapshot_degrades():
    snap = {"as_of": "2026-08-16", "period": "20260630",
            "universe_stats": {"n": 0, "n_typed": 0, "n_cyclic": 0, "n_growth": 0,
                               "n_value": 0, "pct": 0.0},
            "spot_date": "", "industry_snapshot": "",
            "n_floor_pass": 0, "n_gated_pool": 0, "n_track_peg": 0, "n_track_pb": 0,
            "clock": {"period": "20260630", "formal_deadline": "2026-08-31",
                      "days_to_formal": 15, "forecast_open": "2026-07-01",
                      "ring_mix": {}, "theme_windows": []},
            "rows": [], "gaps": {}, "sina_needed": [], "composition": {"by_industry": [],
            "by_type": {}, "n_total": 0, "n_untyped": 0},
            "emergent": None, "diff": {"entered": [], "exited": []},
            "history": [], "conclusion": None,
            "cfg_note": {"floor_np_yoy": 50.0, "floor_rev_yoy": 20.0, "top_n": 100,
                         "peg_max": 1.0, "pb_pct_max": 0.30, "mktcap_filter_on": False}}
    html = render(snap)
    assert "（无条目）" in html            # 空表占位
    assert "结论注入占位" in html           # 未跑验证器
    assert "无涌现簇" in html


def test_render_emergent_banner_when_majority():
    snap = _full_snapshot()
    snap["emergent"] = {"share": 0.6, "n": 60, "n_total": 100,
                        "industries": ["能源金属", "工业金属"]}
    html = render(snap)
    assert "涌现簇" in html and "60%" in html
