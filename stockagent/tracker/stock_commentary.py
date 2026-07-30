"""Stock-level AI commentary — LLM interpretation of a single stock's full diagnosis.

Mirrors research.commentary in shape (LLM first → rule template fallback, hard guard),
but the *stance* differs from research by design (aligned with user):

  research 看板 LLM 全禁「预测/建议」词 —— 因为它会推送到晨报、可能影响实盘判断。
  个股 AI 评估是用户**主动点开看**的深度参考、只读旁路(不喂交易引擎),故尺度放宽:
    允许「定性评估 + 带触发条件的动作建议」(买入/加仓/持有/减仓/卖出/观望),
    仅禁止「纯价格涨跌预测」(目标价/将上涨/看涨/涨停/翻倍…) —— 模型不可靠的那部分仍拦死。

决策归规则引擎,解释归大模型:所有数字都来自 diagnose_stock_full 已算出的事实,LLM 只解读 +
按投资心法三类打法给出条件化建议。无 key / 异常 / 含禁词 → 走 _rule_template(同样五段,确定性)。
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from ..report import llm_client
from . import stock_diagnose as sd

# Hard guard: 只禁「纯价格涨跌预测」词。允许定性判断(看好/预计业绩…)和带条件的动作建议。
# 注意:比 research.commentary._BANNED 窄 —— 那里连"看好/预计/有望"都禁(推送场景)。
_BANNED = (
    "目标价", "将上涨", "将下跌", "将会大涨", "将会暴跌",
    "看涨", "看跌", "涨停", "跌停", "翻倍", "翻番", "暴涨", "暴跌",
)

# ---- 投资思维框架(按个股类型分流;提炼自 docs/我的笔记/投资心法.md §9 三类打法 + §12 纪律)----
_FRAMEWORK = {
    "value": (
        "【价值股打法·防御型】像买房一样买入,想象「买入永不卖是否值得」,靠每年分红一点点回本。"
        "核心公式:股息率 = 分红率 / 市盈率;同股息率下分红率低+PE低=提升空间大,股息率一定时PE越低越好。"
        "偏好成熟行业龙头(国家控股+垄断更佳,高分红印证现金流真实)。"
        "两类陷阱须识别并避开:① PE低+股息率也低 = 多半处于衰退期;② PE低+股息率高+PB高 = 周期陷阱(周期顶部假低估,看过去十年分红金额可识别)。"
    ),
    "growth": (
        "【成长股打法·进攻型】PEG 估值:增速维持则年股价涨幅 ≈ 业绩涨幅。"
        "戴维斯双击 = 增速提升→业绩+估值双涨(最理想);戴维斯双杀 = 增速下滑→估值+业绩双杀(散户亏损主因)。"
        "两条铁律:① 业绩下滑第一时间清仓,不抱侥幸;② 绝不持有业绩下滑的成长股。只信冰冷数据,不信价值观/企业文化/大V说法。"
    ),
    "cyclic": (
        "【周期股打法·进攻型】估值反着看:周期顶部利润峰值→PE很低(陷阱);周期底部利润低谷→PE很高(机会)。"
        "用 PB 历史分位判顶底(周期股 PE 会失真,别用 PE 分位)。"
        "预判抢跑无法精准抄底逃顶:左侧=PB历史低位提前介入(代价:长时间浮亏);右侧=周期明朗再买(代价:牺牲底部空间)。"
        "找稳健公司避破产:看经营现金流是否持续<净利润、看偿债计划。看研报只看数据和逻辑,不看结论(卖方立场)。"
    ),
}
_COMMON = (
    "【通用纪律·贯穿所有类型】① 避险是第一刚需,不是盈利——把宝押在输不起的一方;② 不信仰任何公司文化/"
    "任何人的说法(含本框架本身),只信实时算出的数据;③ 业绩披露第一时间反应(销售额>预报>快报>正式报,越早越占先机);"
    "④ 不预测、不赌方向,看错就小仓位止损(一次抓对足够止损十次以上)。"
)

_SYSTEM_PREFIX = (
    "你是A股个股诊断的 AI 评估助手。基于给定的该股全套结构化诊断事实(估值/业绩/戴维斯/避坑/预告链/"
    "择时位置/利润归因/已触发信号),生成一段「当下评估与买卖建议」。\n\n"
    "输出格式(严格遵守,纯文本,用【】段标题,每段 2-4 句,段间换行):\n"
    "【估值】当前 PE/PB 及其历史分位、所处 zone(便宜/偏贵/分化/中性)的含义。\n"
    "【业绩与归因】最近营收/净利增速、S07 利润三来源(业绩/分红/估值)历年贡献结构、戴维斯信号含义。\n"
    "【择时位置】相对 60 日线位置(线上/线下+偏离)、是否有效突破/跌破、震荡市与否。\n"
    "【风险与避坑】公告时间差/异常高增速/预告链拐点/已触发信号中需警惕的点。\n"
    "【行动建议】必须给出明确的动作标签(买入/加仓/持有/减仓/卖出/观望 之一)+ 触发条件(满足什么才动手)+ 理由。\n\n"
    "硬约束:\n"
    "1. 只引用给定事实里的数字,绝不编造任何未给出的数字。\n"
    "2. 行动建议必须带触发条件(不能无条件喊买卖),由给定事实推导而来。\n"
    "3. 绝对禁止纯价格涨跌预测——不得出现「目标价XX元/将上涨/将下跌/看涨/看跌/涨停/翻倍/暴涨/暴跌」等;"
    "可以做基于事实的定性判断(如「业绩处于回升期」「估值偏贵」)。\n"
    "4. 用中文说人话,不要逐项复述全部数字,要归纳出「这些数字加起来意味着什么」。\n"
)

_STYLE_HINT = {  # facts 里 classification 无 / 未分类时的兜底框架
    "value": "价值", "growth": "成长", "cyclic": "周期",
}

# 顶部免责(前端在 AI_EVALS 文本前统一拼;LLM/模板都不需要自己写)
_DISCLAIMER = "⚠ 以上为模型/规则基于已算出诊断事实的解读,不构成投资建议,决策与风险自负。"


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _pct(v, signed: bool = False) -> str:
    if _nan(v):
        return "NA"
    return f"{v*100:+.0f}%" if signed else f"{v*100:.0f}%"


def _num(v, nd: int = 2) -> str:
    return "NA" if _nan(v) else f"{v:.{nd}f}"


def has_banned_word(text: str) -> bool:
    """Public so tests can assert the guard with the same banned list."""
    return any(b in (text or "") for b in _BANNED)


def _system_for(primary: str | None, secondary: list | None = None) -> str:
    """按个股类型组装 system prompt(角色+五段格式+约束+对应打法+通用纪律)。

    多类股(primary + secondary 同时命中,如宁德 cyclic+growth+value)注入【全部】命中打法 +
    「兼具多类、判断当前哪类主导」的引导——不同打法估值锚与纪律不同(周期看 PB 分位/成长看
    PEG 与业绩拐点/价值看股息率与 PE),让模型综合而非机械套一类。
    """
    styles: list[str] = []
    for s in [primary] + list(secondary or []):
        if s in _FRAMEWORK and s not in styles:
            styles.append(s)
    if not styles:
        fw = "(该股自动分类为「未分类」,无强匹配打法,按通用估值与趋势原则评估即可。)"
    elif len(styles) == 1:
        fw = _FRAMEWORK[styles[0]]
    else:
        names = "/".join(_STYLE_HINT[s] for s in styles)
        fw = (f"该股兼具多类属性({names})——不同打法估值锚与纪律不同,需综合判断【当前哪类"
              f"逻辑最主导】并兼顾其余属性的纪律(如兼具成长,业绩下滑须第一时间警觉;兼具"
              f"价值,关注股息率与PE陷阱):\n" + "\n".join(_FRAMEWORK[s] for s in styles))
    return _SYSTEM_PREFIX + "\n" + fw + "\n\n" + _COMMON


def _facts_for(d: dict, attribution: list, alerts: list, name: str) -> dict:
    """把 diagnose_stock_full 输出 + S07 归因 + 该股 alerts 归约成纯事实 dict(剔除 NaN/原始序列)。

    所有数字都来自已算好的诊断结果,LLM 只解读不发明。
    """
    cls = d.get("classification") or {}
    vz = d.get("valuation_zone") or {}
    f = d.get("features") or {}
    dv = d.get("davis") or {}
    pit = d.get("pitfalls") or {}
    fc = d.get("forecast") or {}
    pt = d.get("price_timing") or {}
    dev = pt.get("deviation") or {}
    cross = pt.get("cross") or {}
    trend = pt.get("trend") or {}

    np_ = pit.get("net_profit") or {}
    rev = pit.get("revenue") or {}
    disc = pit.get("disclosure") or {}
    eq = d.get("earnings_quality") or {}

    # S07 归因:多年业绩/估值/分红贡献(绝对回报占比,可加);只取最近 6 年 + 各段均值概览
    attr_rows = []
    if attribution:
        for r in attribution[-6:]:
            attr_rows.append({
                "year": r.get("year"),
                "业绩贡献": _pct(r.get("earnings"), True),
                "估值贡献": _pct(r.get("valuation"), True),
                "分红贡献": _pct(r.get("dividend"), True),
                "合计": _pct(r.get("total"), True),
            })

    # alerts:该股已触发的信号提醒(规则+消息+级别)
    alert_rows = [{"rule": a.get("rule"), "level": a.get("level"), "msg": a.get("msg")}
                  for a in alerts] if alerts else []

    # 预告
    latest_fc = None
    if fc.get("valid") and fc.get("latest"):
        L = fc["latest"]
        latest_fc = {
            "type": L.get("type"), "yoy": _pct(L.get("yoy"), True),
            "sentiment": fc.get("latest_sentiment"), "announce_date": L.get("announce_date"),
        }

    return {
        "名称": name,
        "代码": d.get("symbol"),
        "现价": _num(d.get("price_last")),
        "最新交易日": d.get("date_last"),
        "主分类": cls.get("primary") or "未分类",
        "次分类": cls.get("secondary") or [],
        "估值": {
            "PE_TTM": _num(d.get("pe_ttm"), 1),
            "PB": _num(d.get("pb")),
            "PE历史分位": _pct(vz.get("pe_pct")),   # 0=最便宜,1=最贵
            "PB历史分位": _pct(vz.get("pb_pct")),
            "zone": vz.get("zone"),
        },
        "特征": {
            "营收CAGR": _pct(f.get("revenue_cagr"), True),
            "净利CAGR": _pct(f.get("profit_cagr"), True),
            "利润波动(std)": _pct(f.get("profit_vol")),
            "股息率": f"{(f.get('div_yield') or 0)*100:.1f}%" if not _nan(f.get("div_yield")) else "NA",
        },
        "戴维斯": {
            "type": dv.get("type"), "label": dv.get("label"),
            "最近净利YoY": _pct(dv.get("profit_yoy_latest"), True),
            "PE变化": _pct(dv.get("pe_change"), True),
        },
        "避坑": {
            "净利YoY": _pct(np_.get("yoy"), True),
            "净利2年CAGR": _pct(np_.get("cagr2"), True),
            "净利异常高增速": bool(np_.get("abnormal")),
            "净利可信YoY": _pct(np_.get("trustworthy"), True),
            "营收YoY": _pct(rev.get("yoy"), True),
            "营收2年CAGR": _pct(rev.get("cagr2"), True),
        },
        "业绩含金量": {
            "一次性占比": _pct(eq.get("non_recurring_frac")),
            "归母YoY": _pct(eq.get("np_yoy"), True),
            "扣非YoY": _pct(eq.get("ded_yoy"), True),
            "增速背离": _pct(eq.get("deviation")),
            "low_quality": bool(eq.get("low_quality")),
            "reason": eq.get("reason") or "",
        },
        "披露": {
            "最新财报期": disc.get("latest_period"),
            "法定截止日": disc.get("deadline"),
            "截至asof是否已披露": bool(disc.get("disclosed_by_asof")),
        },
        "预告链": latest_fc,
        "预告拐点": {
            "A1_增速下滑": bool(fc.get("a1_deceleration")),
            "A2_多转空": bool(fc.get("a2_turn_bearish")),
        },
        "择时位置": {
            "站上60日线": trend.get("above_ma"),
            "均线上行": trend.get("ma_trend_up"),
            "偏离60日线": _pct(dev.get("cur_dev"), True),
            "偏离历史分位": _pct(dev.get("pct")),   # 0=超卖,1=超买
            "最近穿越方向": cross.get("direction"),
            "穿越距今天数": cross.get("bars_ago"),
            "震荡市": bool(pt.get("choppy")),
        },
        "S07利润归因_近6年": attr_rows,
        "已触发信号": alert_rows,
    }


# ---------------- 规则兜底(确定性,同样五段)----------------
def _action_label(d: dict) -> tuple[str, str]:
    """由 (zone, davis, price_timing) 规则映射出(动作标签, 理由)。不预测涨跌,只给条件化动作。"""
    vz = (d.get("valuation_zone") or {}).get("zone", "")
    dv_type = (d.get("davis") or {}).get("type", "neutral")
    pt = d.get("price_timing") or {}
    trend = pt.get("trend") or {}
    above_ma = trend.get("above_ma")
    choppy = bool(pt.get("choppy"))
    dev_pct = (pt.get("deviation") or {}).get("pct")

    low = "低位" in vz
    high = "高位" in vz

    if high and dv_type in ("double_kill", "double_kill_risk"):
        return ("减仓/警惕", "估值高位 + 业绩承压(戴维斯双杀风险),逢高减仓为主。")
    if low and dv_type in ("double_play", "double_play_setup") and above_ma:
        return ("可分批建仓(右侧)", "估值低位 + 业绩回升 + 已站上 60 日线,右侧建仓窗口。")
    if low and dv_type == "double_play_setup":
        return ("观望偏多(待突破)", "估值便宜 + 业绩正增,但仍在均线下方;待有效突破 60 日线再右侧跟进。")
    if low and dv_type == "double_play_watch":
        return ("观望(待业绩拐头)", "估值便宜但业绩仍探底;等营收同比转正 + 站稳均线再考虑。")
    if high:
        return ("观望(偏贵)", "估值偏高,性价比不足;等回调至中性区间或业绩超预期。")
    if above_ma is False and not _nan(dev_pct) and dev_pct <= 0.05:
        return ("观望(超卖,左侧观察)", "运行于均线下方且接近历史超卖区,左侧可跟踪但不抢跑。")
    if above_ma is False:
        return ("观望(线下)", "运行于 60 日线下方,趋势未明朗,不急于介入。")
    if choppy:
        return ("观望(震荡)", "处于震荡市,趋势信号噪音大,均线信号暂不生效。")
    return ("持有/观望", "估值与趋势均处中性,无明确 actionable 信号,维持现状。")


def _rule_template(d: dict, alerts: list, name: str) -> str:
    """确定性规则兜底,五段同形。所有数字直引诊断 dict,不预测涨跌。"""
    vz = d.get("valuation_zone") or {}
    f = d.get("features") or {}
    dv = d.get("davis") or {}
    pit = d.get("pitfalls") or {}
    pt = d.get("price_timing") or {}
    dev = pt.get("deviation") or {}
    np_ = pit.get("net_profit") or {}
    rev = pit.get("revenue") or {}
    trend = pt.get("trend") or {}
    eq = d.get("earnings_quality") or {}

    label, reason = _action_label(d)

    val_s = (f"PE(TTM){_num(d.get('pe_ttm'),1)} 处 {_pct(vz.get('pe_pct'))} 分位、"
             f"PB {_num(d.get('pb'))} 处 {_pct(vz.get('pb_pct'))} 分位,zone=「{vz.get('zone','—')}」。")
    earn_s = (f"营收CAGR {_pct(f.get('revenue_cagr'),True)}、净利CAGR {_pct(f.get('profit_cagr'),True)};"
              f"最近净利YoY {_pct(np_.get('yoy'),True)}、营收YoY {_pct(rev.get('yoy'),True)};"
              f"戴维斯「{dv.get('label','—')}」(净利YoY {_pct(dv.get('profit_yoy_latest'),True)} × PE变化 {_pct(dv.get('pe_change'),True)})。")
    pos = "上方" if trend.get("above_ma") else ("下方" if trend.get("above_ma") is False else "—")
    timing_s = (f"运行于 60 日线{pos}(偏离 {_pct(dev.get('cur_dev'),True)}),"
                f"偏离历史分位 {_pct(dev.get('pct'))};" + (" 震荡市;" if pt.get("choppy") else ""))
    pit_items = []
    if np_.get("abnormal"):
        pit_items.append(f"净利增速 {_pct(np_.get('yoy'),True)} 疑低基数幻觉,可信值 {_pct(np_.get('trustworthy'),True)}")
    if eq.get("valid") and eq.get("low_quality"):
        pit_items.append(f"业绩含金量低(归母{_pct(eq.get('np_yoy'),True)}/扣非{_pct(eq.get('ded_yoy'),True)},一次性占比{_pct(eq.get('non_recurring_frac'))})→ 一次性利润/纸面富贵")
    pit_items.append(f"披露 {((pit.get('disclosure') or {}).get('latest_period') or '?')[:4]}期 截止 {(pit.get('disclosure') or {}).get('deadline','?')}")
    if alerts:
        pit_items.append("信号 " + "、".join(f"[{a.get('rule')}]{a.get('msg','')[:24]}" for a in alerts[:3]))
    risk_s = "；".join(pit_items) + "。"

    return (
        f"【估值】{val_s}\n"
        f"【业绩与归因】{earn_s}\n"
        f"【择时位置】{timing_s}\n"
        f"【风险与避坑】{risk_s}\n"
        f"【行动建议】{label} —— {reason}\n"
        f"{_DISCLAIMER}"
    )


# ---------------- LLM 评估(并行)----------------
def _eval_one(sym: str, d: dict, names: dict, store, by_scope: dict) -> tuple[str, str] | None:
    """单股 LLM 评估。返回 (sym, 文本) 或 None(无 key/异常/含禁词 → 交规则模板兜底)。"""
    name = names.get(sym, sym)
    attribution = sd.attribution_by_year(sym, store, 6)
    this_alerts = by_scope.get(name) or by_scope.get(sym) or []
    facts = _facts_for(d, attribution, this_alerts, name)
    cls = d.get("classification") or {}
    system = _system_for(cls.get("primary"), cls.get("secondary"))
    prompt = ("基于以下该股诊断事实,按五段格式生成「当下评估与买卖建议」"
              "(行动建议必须给明确标签+触发条件,禁止纯涨跌预测):\n"
              + json.dumps(facts, ensure_ascii=False, indent=2))
    try:
        # glm-5.2 是推理模型,需给 reasoning 留预算 + 五段中文(~1k content tokens)
        txt = llm_client.chat(prompt, system=system, max_tokens=6000)
    except Exception:  # noqa: BLE001
        return None
    if not txt:
        return None
    txt = txt.strip()
    if has_banned_word(txt):  # 模型漏了纯涨跌预测 → 丢弃走规则
        return None
    return (sym, txt + "\n" + _DISCLAIMER)


def stock_eval_llm(diagnoses: dict, alerts_list: list, names: dict, store) -> dict:
    """逐只 LLM 评估(并行 ThreadPoolExecutor,LLM 是 IO-bound)。

    返回 {sym: 文本};无 key / 失败 / 含禁词的 sym 不出现(交 stock_eval 兜底)。
    """
    if not llm_client.llm_available():
        return {}
    by_scope: dict = defaultdict(list)
    for a in alerts_list:
        by_scope[a.get("scope")].append(a)   # evaluate_stocks 的 scope=name(或 sym)

    syms = list(diagnoses.keys())
    out: dict[str, str] = {}
    # max_workers=min(8, len):5 只股并行 ≈ 单股延迟;再多也限流,避免触发 provider 限速
    workers = max(1, min(8, len(syms)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_eval_one, sym, diagnoses[sym], names, store, by_scope)
                   for sym in syms]
        for fu in futures:
            res = fu.result()
            if res:
                out[res[0]] = res[1]
    return out


def stock_eval(diagnoses: dict, alerts_list: list, names: dict, store,
               use_llm: bool = True) -> dict:
    """单股 AI 评估入口。LLM first;每股独立兜底 _rule_template(失败/无key/含禁词)。

    diagnoses = {symbol: diagnose_stock_full 输出};alerts_list = collect_stock_alerts 输出;
    names = {symbol: 显示名}。返回 {symbol: 评估文本(含免责)}。
    """
    names = names or {}
    by_scope: dict = defaultdict(list)
    for a in alerts_list:
        by_scope[a.get("scope")].append(a)

    out = stock_eval_llm(diagnoses, alerts_list, names, store) if use_llm else {}
    for sym, d in diagnoses.items():
        if sym in out:
            continue
        name = names.get(sym, sym)
        this_alerts = by_scope.get(name) or by_scope.get(sym) or []
        out[sym] = _rule_template(d, this_alerts, name)
    return out
