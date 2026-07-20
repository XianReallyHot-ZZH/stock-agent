"""标的分类器(价值/成长/周期)— Phase 1-A。

读 etf_pool.yaml 的 style(主)+ style_alt(次),返回 (主类型, [次类型])。
csrc_industry 映射做一致性校验(标的标周期但行业典型价值 → 警告)。

Phase 1-A 仅 ETF(人工标签,见 etf_pool.yaml)。个股的财务自动判定留 Phase 2。
统一的是「分类 → 选指标」框架(A3 scoring 按此分流)。
"""
from __future__ import annotations

from ..config import get_config

VALID_STYLES = {"value", "growth", "cyclic"}

# csrc_industry → 典型 style 的粗映射(仅不冲突的行业)。
# 冲突的跳过:「汽车制造业」(新能源车 growth / 整车 cyclic)、「电气机械」(家电 value / 光伏 growth)。
_INDUSTRY_STYLE_HINT = {
    "货币金融服务": "value",
    "酒、饮料和精制茶制造业": "value",
    "资本市场服务": "cyclic",
    "煤炭开采和洗选业": "cyclic",
    "有色金属冶炼和压延加工业": "cyclic",
    "畜牧业": "cyclic",
    "房地产业": "cyclic",
    "化学原料和化学制品制造业": "cyclic",
    "电力、热力生产和供应业": "cyclic",
    "医药制造业": "growth",
    "计算机、通信和其他电子设备制造业": "growth",
    "软件和信息技术服务业": "growth",
    "广播、电视、电影和影视录音制作业": "growth",
    "电信、广播电视和卫星传输服务": "growth",
    "通用设备制造业": "growth",
    "铁路、船舶、航空航天和其他运输设备制造业": "growth",
}


def classify(symbol: str, config=None) -> tuple[str | None, list[str]]:
    """返回 (主类型, [次类型])。无 style 标签 → (None, [])。"""
    cfg = config or get_config()
    meta = cfg.symbol_meta().get(str(symbol), {})
    style = meta.get("style")
    if style not in VALID_STYLES:
        return (None, [])
    alt = [s for s in (meta.get("style_alt") or []) if s in VALID_STYLES]
    return (style, alt)


def classify_all(config=None) -> dict[str, tuple[str | None, list[str]]]:
    """所有 rotation ETF 的分类 {symbol: (主, [次])}。"""
    cfg = config or get_config()
    return {sym: classify(sym, cfg) for sym in cfg.rotation_symbols()}


def consistency_warnings(config=None) -> list[str]:
    """行业 vs style 一致性校验(soft,返回警告列表,不阻断)。
    标的标某 style 但 csrc_industry 典型另一 style → 警告(可能标错,或行业映射不全)。"""
    cfg = config or get_config()
    warnings = []
    for sym in cfg.rotation_symbols():
        meta = cfg.symbol_meta().get(sym, {})
        style, _ = classify(sym, cfg)
        industry = meta.get("csrc_industry")
        hint = _INDUSTRY_STYLE_HINT.get(industry or "")
        if style and hint and style != hint:
            warnings.append(
                f"{meta.get('name', sym)}({sym}) 标 {style},但行业「{industry}」典型 {hint} —— 请确认")
    return warnings
