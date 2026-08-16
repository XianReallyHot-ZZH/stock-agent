"""信号提醒规则库(九条 MVP)— Phase 1-A。

双通道:看板顶部告警区 + 微信推送(同源 evaluate 输出)。
evaluate 接收 ETF snapshots + 指数层 diagnose,返回 alert 列表。

九条(PRD §8.2):
  D   筹码相位转兑现中段/见顶预警(空)、见底/低位加仓(多)
  E1  60日线有效突破 → 右侧买点(震荡市抑制)
  E2  60日线有效跌破 → 止盈止损
  E5  ⑦相对周期极点(创业板vs上证 点差处5年包络上/下沿)→ 相对回归方向(info,非绝对买卖)
  V1  ⑧成交量地量(两市成交额/MA250≤0.6)→ 量底信号(info;经验上价底~1月内,短期胜率≈50%)
  C1/C2 周期 PB 触底/顶 —— 当前跳过:板块 PB 无数据源(cyclic 不估值),待 Phase 2
  B1  价值股息率偏高(>5%)→ 买入窗口
  A1/A2 业绩预告承压/恶化 → 抱着颗雷/戴维斯双杀前兆
  A5  一致预期4周下修(E4修正动量·超3%且覆盖≥40%) → 分析师集体转谨慎(温度计·非买卖信号)
  F1  沪深300 在60日线下且均线向下 → 风险开关
"""
from __future__ import annotations

import math
from datetime import datetime

from . import indicators as ti

_DIV_THRESHOLD = 0.05  # B1: 股息率 > 5% 视为偏高(买入窗口)

# ---- C2 个股提醒阈值(Phase 2)----
_A3_DECEL_MARGIN = 0.05      # A3: 营收增速下滑 >5pp 触发(滤噪音)
_G1_RECENT_DAYS = 90         # G1: 预告公告在 90 天内 = 披露窗口刚开
_G1_PRICED_IN = 0.20         # G1: 近60日涨≥20% = 股价已透支(预告转"利好出尽")
_G2_DEADLINE_NEAR_DAYS = 45  # G2: 法定披露截止日在 45 天内 = 临近
_E3_LOW, _E3_HIGH = 0.05, 0.95  # E3: 偏离极值套利阈值(≤5% 超卖买点 / ≥95% 超买卖点)
_E3_KNIFE_RECENT = -0.15        # E3: 近60日跌超此 = 飞刀(超卖不作买点,改 ⚠)
_E5_LOW, _E5_HIGH = 0.20, 0.80  # E5: ⑦相对周期包络位置阈值(≤20% 下沿 / ≥80% 上沿)
# Q1 个股业绩含金量(一次性利润/纸面富贵)—— 判定在 diagnose_earnings_quality,params.yaml stock.earnings_quality
_Q1_NON_RECURRING = 0.30   # 一次性占比 ≥30% → low_quality(展示用,实际阈值以 diagnose 为准)
_Q1_DEVIATION = 0.30       # 归母-扣非增速背离 ≥30pp 且归母正增 → low_quality
# R1 周期反转候选(cyclic 高业绩×深回撤×新鲜财报)—— 综合分由 research.cyclical 算
_R1_SCORE_MIN = 60.0      # 综合分 ≥60 触发
_R1_FRESH_DAYS = 150      # 财报窗口 150 天内才发(时效兑现要求;预期放缓后失效)
_P1_SCORE_MIN = 40.0      # P1: 提前埋伏分 ≥40 触发(领先×含金量×估值×时效)


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


# 筹码相位 → D 告警(重远 6 相位)。注意:位置决定"加仓"含义 ——
# 低位加仓=机会(底部建仓),高位加仓=风险(拉高出货/接盘,易被误读成利好)。
_D_BULL = {"见底", "低位加仓", "加仓"}
_D_PHASE_MSG = {
    "兑现中段":   "机构在中部兑现 → 看空(无性价比)",
    "见顶预警":   "高位+机构停滞 → 见顶风险",
    "高位加仓":   "高位+机构仍在买 → 警惕拉高出货/接盘(不是机会!)",
    "高位减仓":   "高位+机构抛 → 明确出货",
    "见底":       "深底+卖盘枯竭 → 见底(看多)",
    "低位加仓":   "深底+聪明钱进场 → 最强看多(机会)",
    "加仓":       "中部+机构加仓 → 偏多",
    "下跌末段":   "深底+机构仍在抛 → 未确认(可能见底前最后抛,也可能继续跌)",
    # "观望"(mid_stable)= 中部+机构停滞 → 中性、无强信号,不触发告警(alerts 只推 actionable)
}
# 业绩预告 → A1/A2 告警
_EARN_BEAR = {"业绩承压", "业绩恶化"}
_A5_DROP_PCT = 3.0  # A5: 一致预期4周加权下修超此%告警(对齐 params research.earnings.revision.alert_drop_pct; 改动两处同步)


def evaluate(etf_snapshots: dict, index_diag: dict | None = None) -> list[dict]:
    """评估九条规则,返回 alert 列表 [{level, scope, rule, msg}]。
    level: 'info'(看多/机会)/ 'warn'(看空/风险)。"""
    alerts: list[dict] = []

    # ---- 指数层(E1/E2/F1)----
    if index_diag:
        for sym, info in index_diag.get("indices", {}).items():
            if not info.get("valid"):
                continue
            dg = info["diagnosis"]; bo = dg["breakout"]
            # 有效突破/跌破 = 近期真穿越(fresh_cross_direction) + 偏离≥2%(grade≥2),非「在线上」
            fresh = ti.fresh_cross_direction(dg.get("cross"))
            # E1: 有效突破(震荡市抑制 — 趋势信号是噪音)
            if bo["direction"] == "up" and bo["grade"] >= 2 and fresh == "up" and not dg["choppy"]:
                alerts.append({"level": "info", "scope": f"指数·{info['name']}", "rule": "E1",
                               "msg": f"{info['name']} 60日线有效突破(grade{bo['grade']},近期穿越)→ 右侧买点"})
            # E2: 有效跌破(震荡市仍提示,但标注谨慎)
            elif bo["direction"] == "down" and bo["grade"] >= 2 and fresh == "down":
                chop = "(震荡市,信号谨慎)" if dg["choppy"] else ""
                alerts.append({"level": "warn", "scope": f"指数·{info['name']}", "rule": "E2",
                               "msg": f"{info['name']} 60日线有效跌破(grade{bo['grade']},近期穿越)→ 止盈止损{chop}"})
        # F1: 沪深300 大盘风险开关
        hs = index_diag.get("indices", {}).get("000300", {})
        if hs.get("valid"):
            t = hs["diagnosis"]["trend"]
            if t["above_ma"] is False and t["ma_trend_up"] is False:
                alerts.append({"level": "warn", "scope": "大盘", "rule": "F1",
                               "msg": "沪深300 在60日线下且均线向下 → 风险开关(趋势信号谨慎)"})
        # E5: ⑦相对周期极点(创业板 vs 上证 点差在5年包络的上/下沿)→ 相对回归方向
        # level=info(相对回归信号,绝不写成绝对买卖);中枢(env_pos 20-80%)不发。仅经 research/stock
        # 推送通路触发(index_timing_report.py 不推送)。上沿=上证相对过强→回归利创业板;下沿反之。
        rc = index_diag.get("relative_cycle") or {}
        if rc.get("valid") and not _nan(rc.get("env_pos")):
            if rc["env_pos"] >= _E5_HIGH:
                alerts.append({"level": "info", "scope": "大盘·相对周期", "rule": "E5",
                               "msg": f"上证−创业板点差处5年包络上沿(位置{rc['env_pos']:.0%})→ 回归方向:创业板相对跑盈"})
            elif rc["env_pos"] <= _E5_LOW:
                alerts.append({"level": "info", "scope": "大盘·相对周期", "rule": "E5",
                               "msg": f"上证−创业板点差处5年包络下沿(位置{rc['env_pos']:.0%})→ 回归方向:上证相对跑盈"})
        # V1: ⑧成交量地量(两市成交额/MA250≤0.6)→ 量底信号(info;经验上价底~1月内,但短期胜率≈50%)
        tv = index_diag.get("turnover") or {}
        if tv.get("valid") and tv.get("is_dry"):
            alerts.append({"level": "info", "scope": "大盘·量能", "rule": "V1",
                           "msg": f"两市成交额/MA250={tv['ratio_now']:.2f}(≤0.6 地量,{tv['turnover_yi']:.0f}亿)"
                                  f"→ 经验上价底~1月内临近(但各horizon胜率~50%非高胜率,样本{tv['sample']};提示时机非买点)"})

    # ---- ETF 层(D/B1/A1A2)----
    for sym, snap in etf_snapshots.items():
        nm = snap.get("name", sym)
        # D: 筹码相位 × 估值分位交叉(综合筹码+价格才准)
        # 只看筹码会遗漏价格:高位筹码+低估值=好资产被错杀(不是风险!)
        phase = snap.get("chip_phase", "")
        if phase in _D_PHASE_MSG:
            pe_pct = snap.get("pe_percentile")
            has_pe = pe_pct is not None and not _nan(pe_pct)
            chip_high = phase in ("高位加仓", "高位减仓", "见顶预警")
            chip_low = phase in ("低位加仓", "见底", "下跌末段")
            if has_pe and (chip_high or chip_low):
                val_low, val_high = pe_pct < 0.30, pe_pct > 0.70
                if chip_high and val_low:
                    alerts.append({"level": "info", "scope": nm, "rule": "D",
                        "msg": f"筹码高位(机构重仓)+估值低位({pe_pct:.0%})→ 好资产被错杀,潜在反弹"})
                elif chip_high and val_high:
                    alerts.append({"level": "warn", "scope": nm, "rule": "D",
                        "msg": f"筹码高位+估值高位({pe_pct:.0%})→ 拉高出货/见顶风险"})
                elif chip_low and val_low:
                    alerts.append({"level": "info", "scope": nm, "rule": "D",
                        "msg": f"筹码低位+估值低位({pe_pct:.0%})→ 见底信号(机构可能重新进场)"})
                elif chip_low and val_high:
                    alerts.append({"level": "warn", "scope": nm, "rule": "D",
                        "msg": f"筹码低位+估值高位({pe_pct:.0%})→ 机构已跑+估值贵(危险)"})
                else:  # 筹码极端但估值中部 → 按筹码单维
                    level = "info" if phase in _D_BULL else "warn"
                    alerts.append({"level": level, "scope": nm, "rule": "D",
                        "msg": f"筹码「{phase}」+估值中位({pe_pct:.0%})→ {_D_PHASE_MSG[phase]}"})
            else:  # 无PE(cyclic/宽基)或筹码中部 → 按筹码单维
                level = "info" if phase in _D_BULL else "warn"
                alerts.append({"level": level, "scope": nm, "rule": "D",
                    "msg": f"筹码相位「{phase}」→ {_D_PHASE_MSG[phase]}"})
        # B1: 价值股息率偏高
        if snap.get("style") == "value":
            dy = snap.get("dividend_yield")
            if not _nan(dy) and dy > _DIV_THRESHOLD:
                alerts.append({"level": "info", "scope": nm, "rule": "B1",
                               "msg": f"股息率 {dy:.1%} 偏高(>{_DIV_THRESHOLD:.0%})→ 买入窗口"})
        # A1/A2: 业绩预告承压/恶化
        elabel = snap.get("earnings_label", "")
        if elabel in _EARN_BEAR:
            yoy = snap.get("earnings_yoy")
            yoy_s = f"(yoy {yoy:+.0f}%)" if not _nan(yoy) else ""
            alerts.append({"level": "warn", "scope": nm, "rule": "A1/A2",
                           "msg": f"业绩预告「{elabel}」{yoy_s} → 抱着颗雷/戴维斯双杀前兆"})
        # A5: 一致预期下修 (E4·修正动量): 4周加权 forward EPS 下修超阈值且覆盖达标。
        # 只提醒不交易(温度计非开关); 下调的信息量大于上调(调研§2.1.4 Womack 1996)。
        rev = snap.get("revision_w")
        if (isinstance(rev, (int, float)) and not _nan(rev)
                and rev * 100 < -_A5_DROP_PCT
                and (snap.get("revision_cov") or 0) >= 0.40):
            alerts.append({"level": "warn", "scope": nm, "rule": "A5",
                           "msg": f"一致预期4周下修 {rev:+.1%}"
                                  f"(上调{snap.get('revision_up', 0)}家/下调{snap.get('revision_dn', 0)}家)"
                                  f" → 分析师集体转谨慎; 与预告/偏离度交叉看·温度计非买卖信号"})
        # R1: 周期反转候选(cyclic 高业绩×深回撤×新鲜财报 → 综合分;下个业绩窗口前须兑现)
        if snap.get("style") == "cyclic":
            rsc = snap.get("reversal_score")
            rdays = snap.get("days_since_report")
            if (not _nan(rsc) and rsc >= _R1_SCORE_MIN
                    and not _nan(rdays) and rdays <= _R1_FRESH_DAYS):
                ey = snap.get("earnings_yoy")
                dd = snap.get("drawdown")
                ey_s = f"{ey:+.0f}%" if not _nan(ey) else "—"      # earnings_yoy 已是百分数
                dd_s = f"{abs(dd) * 100:.0f}%" if not _nan(dd) else "—"  # drawdown 是分数
                alerts.append({"level": "info", "scope": nm, "rule": "R1",
                               "msg": f"周期反转候选:业绩YoY {ey_s} × 250日回撤 {dd_s} × 报告期时效"
                                      f" → 综合分 {rsc:.0f}(下个业绩窗口前须兑现)"})

    return alerts


# ---------- C2 个股提醒(Phase 2)----------
def _days(a, b) -> int | None:
    """两个 YYYY-MM-DD 相差天数(a − b)。解析失败 → None。"""
    try:
        da = datetime.strptime(str(a)[:10], "%Y-%m-%d")
        db = datetime.strptime(str(b)[:10], "%Y-%m-%d")
        return (da - db).days
    except Exception:  # noqa: BLE001
        return None


def evaluate_stocks(stocks: dict, index_diag: dict | None = None,
                    asof: str | None = None) -> list[dict]:
    """评估个股级提醒(PRD §8 二阶段: A1/A2/A3/G1/G2/E3 + 市场 E4),返回 alert 列表。

    stocks[symbol] 期望键(name 可选, 余为 diagnose 输出):
      forecast      diagnose_forecast_chain 输出(A1/A2/G1 用)
      pitfalls      diagnose_pitfalls 输出(A3 营收 + G2 披露截止 用)
      price_timing  diagnose_stock/diagnose_price_timing 输出(E3 偏离极值 用)
    index_diag 的 style.blue_up/growth_up → E4 蓝筹vs成长背离(市场级)。
    返回形状同 evaluate:[{level, scope, rule, msg}],可直接喂 format_for_push。"""
    alerts: list[dict] = []
    asof = asof or datetime.now().strftime("%Y-%m-%d")
    from .stock_diagnose import (positioning_score as _positioning_score,
                                 earnings_turn_factor as _eturn, stabilize_factor as _stab)  # lazy: 防循环

    for sym, s in stocks.items():
        nm = s.get("name", sym)

        # A1/A2 + G1: 业绩预告链
        fc = s.get("forecast") or {}
        if fc.get("valid"):
            L = fc.get("latest") or {}
            yoy = L.get("yoy")
            ys = f"(yoy {yoy:+.0f}%)" if not _nan(yoy) else ""
            if fc.get("a2_turn_bearish"):
                alerts.append({"level": "warn", "scope": nm, "rule": "A2",
                               "msg": f"业绩预告「{L.get('type', '?')}」{ys}转空 → 戴维斯双杀前兆"})
            elif fc.get("a1_deceleration"):
                alerts.append({"level": "warn", "scope": nm, "rule": "A1",
                               "msg": f"业绩预告增速下滑{ys} → 抱着颗雷(拐点)"})
            ann = L.get("announce_date")
            if ann:
                d = _days(asof, ann)  # asof − ann;0..N = 近 N 天内公告
                if d is not None and 0 <= d <= _G1_RECENT_DAYS:
                    # 预告=业绩兑现;含义随上游/股价而变(铜式正向催化 vs 锂式利好出尽)
                    com = ((s.get("leading") or {}).get("components") or {}).get("commodity") or {}
                    rev_ret = (s.get("reversal") or {}).get("recent_return")
                    commodity_weak = bool(com.get("divergent") or com.get("down"))
                    priced_in = (not _nan(rev_ret)) and rev_ret >= _G1_PRICED_IN
                    if commodity_weak or priced_in:
                        reason = "上游转弱" if commodity_weak else "股价已透支"
                        alerts.append({"level": "warn", "scope": nm, "rule": "G1",
                                       "msg": f"业绩预告已出(「{L.get('type', '?')}」公告{ann})→ 业绩兑现,"
                                              f"但{reason} → 利好出尽,减仓/离场"})
                    else:
                        alerts.append({"level": "info", "scope": nm, "rule": "G1",
                                       "msg": f"业绩预告已出(「{L.get('type', '?')}」公告{ann})→ 业绩兑现,"
                                              f"上游支撑+股价未透支,驱动力仍在(正向催化)"})

        # A3: 营收增速下滑(营收是利润之母)
        rev = (s.get("pitfalls") or {}).get("revenue") or {}
        if rev.get("valid"):
            ly, base, prev = rev.get("yoy"), rev.get("base"), rev.get("prev_base")
            if not _nan(ly) and not _nan(base) and not _nan(prev) and prev > 0:
                py = base / prev - 1.0  # 上年增速
                if ly < py - _A3_DECEL_MARGIN:
                    alerts.append({"level": "warn", "scope": nm, "rule": "A3",
                                   "msg": f"营收增速下滑({py * 100:.0f}%→{ly * 100:.0f}%)→ 营收是利润之母,业绩前瞻预警"})

        # Q1: 业绩含金量低(归母高增但扣非掉队 / 一次性占比高 → 一次性利润·纸面富贵)
        eq = s.get("earnings_quality") or {}
        if eq.get("valid") and eq.get("low_quality"):
            npy, ded, frac = eq.get("np_yoy"), eq.get("ded_yoy"), eq.get("non_recurring_frac")
            npy_s = f"{npy * 100:+.0f}%" if not _nan(npy) else "—"
            ded_s = f"{ded * 100:+.0f}%" if not _nan(ded) else "—"
            frac_s = f"{frac * 100:.0f}%" if not _nan(frac) else "—"
            alerts.append({"level": "warn", "scope": nm, "rule": "Q1",
                           "msg": f"业绩含金量低:{eq.get('reason', '')} → 一次性利润/纸面富贵风险"
                                  f"(归母YoY {npy_s}/扣非 {ded_s}/一次性占比 {frac_s})"})

        # G2: 法定披露截止日临近(公告时间差:截止日=最晚影响日)
        disc = (s.get("pitfalls") or {}).get("disclosure") or {}
        dl, lp = disc.get("deadline"), disc.get("latest_period")
        if dl and lp:
            d = _days(dl, asof)  # dl − asof;0..N = 未来 N 天内截止
            if d is not None and 0 <= d <= _G2_DEADLINE_NEAR_DAYS:
                alerts.append({"level": "info", "scope": nm, "rule": "G2",
                               "msg": f"{lp[:4]}期财报披露截止{dl}(剩{d}天)→ 公告日=影响第一天"})

        # E3: 股价/均线偏离接近历史极值 → 套利(超卖但仍在暴跌=飞刀,不作买点)
        dev = (s.get("price_timing") or {}).get("deviation") or {}
        pct = dev.get("pct")
        if dev.get("valid") and not _nan(pct):
            if pct <= _E3_LOW:
                rev_ret = (s.get("reversal") or {}).get("recent_return")
                if not _nan(rev_ret) and rev_ret < _E3_KNIFE_RECENT:
                    alerts.append({"level": "warn", "scope": nm, "rule": "E3",
                                   "msg": f"股价偏离60日线接近历史底部(pct={pct:.0%})但近60日仍跌{rev_ret * 100:.0f}% → 飞刀,勿当买点抄底"})
                else:
                    alerts.append({"level": "info", "scope": nm, "rule": "E3",
                                   "msg": f"股价偏离60日线接近历史底部(pct={pct:.0%})→ 超卖·套利买点"})
            elif pct >= _E3_HIGH:
                alerts.append({"level": "warn", "scope": nm, "rule": "E3",
                               "msg": f"股价偏离60日线接近历史顶部(pct={pct:.0%})→ 超买·减仓/套利卖点"})

        # P1: 提前埋伏候选(基本面领先:深跌×业绩拐头×含金量×未兑现×企稳;财报=兑现出场)
        rev = s.get("reversal") or {}
        np_ = (s.get("pitfalls") or {}).get("net_profit") or {}
        eq = s.get("earnings_quality") or {}
        et = _eturn(np_.get("latest"), np_.get("base"))
        leading = s.get("leading") or {}
        ls = float(leading["score"]) if leading.get("valid") else 0.0
        outlook = max(et, ls)   # 领先信号能替代/早于报告期业绩拐头
        sf = _stab(rev.get("recent_return"))
        ps = _positioning_score(rev.get("drawdown"), outlook, not bool(eq.get("low_quality")),
                                rev.get("recent_return"), sf)
        if ps["score"] >= _P1_SCORE_MIN:
            dd_s = f"{abs(rev['drawdown']):.0%}" if not _nan(rev.get("drawdown")) else "—"
            rr_s = f"{rev['recent_return']:+.0%}" if not _nan(rev.get("recent_return")) else "—"
            alerts.append({"level": "info", "scope": nm, "rule": "P1",
                           "msg": f"提前埋伏候选:深跌 {dd_s} × 业绩拐头 × 近60日涨 {rr_s} × 企稳"
                                  f" → 埋伏分 {ps['score']:.0f}(领先基本面埋伏,财报兑现即离场)"})
        # M1: 上游商品背离(同比涨但近期回落 → 未来业绩承压,领先信号转弱,减仓/卖出)
        com = (leading.get("components") or {}).get("commodity") or {}
        if com.get("divergent"):
            alerts.append({"level": "warn", "scope": nm, "rule": "M1",
                           "msg": f"上游{com.get('variety', '?')}价同比{com.get('yoy', 0) * 100:+.0f}%但近期回落"
                                  f"{com.get('recent', 0) * 100:.0f}% → 未来业绩承压(领先信号转弱,周期股减仓/卖出)"})
        # M2: 上游商品同比转负 → 周期确认向下,卖出/避开
        if com.get("down"):
            alerts.append({"level": "warn", "scope": nm, "rule": "M2",
                           "msg": f"上游{com.get('variety', '?')}价同比{com.get('yoy', 0) * 100:+.0f}%转负"
                                  f" → 周期确认向下,卖出/避开"})

    # E4: 蓝筹 vs 成长趋势背离 → 仓位倾向(市场级,复用指数层 style)
    if index_diag:
        st = index_diag.get("style") or {}
        if st.get("valid"):
            b, g = st.get("blue_up"), st.get("growth_up")
            if b is not None and g is not None and b != g:
                lean = "蓝筹(上证50)" if b else "成长(创业板)"
                alerts.append({"level": "info", "scope": "大盘", "rule": "E4",
                               "msg": f"蓝筹vs成长趋势背离(蓝筹{'上行' if b else '下行'}/成长{'上行' if g else '下行'})→ 偏向{lean}"})

    return alerts


def format_for_push(alerts_list: list, title_prefix: str = "📡 信号提醒") -> tuple[str, str]:
    """把 alerts 渲染成推送友好文本(双通道之微信)。返回 (title, text)。
    无触发时返回空 title(调用方可跳过推送)。"""
    if not alerts_list:
        return ("", "")
    warns = [a for a in alerts_list if a["level"] == "warn"]
    infos = [a for a in alerts_list if a["level"] == "info"]
    title = f"{title_prefix}(⚠{len(warns)} 💡{len(infos)})"
    lines = [f"**{title}**", ""]
    for a in alerts_list:
        icon = "⚠" if a["level"] == "warn" else "💡"
        lines.append(f"{icon} **[{a['rule']}] {a['scope']}**\n{a['msg']}")
    return (title, "\n".join(lines))
