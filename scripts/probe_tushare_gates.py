"""tushare 迁移实测门探针 (docs/EXECUTION_PLAN-tushare迁移.md · 实测门清单).

一次性只读探针: 对 5 项「官方文档未确认/自相矛盾」的接口语义做实测, 结果决定对应
数据腿迁不迁(测不过 → 该腿降级为「保留」, 零沉没成本)。不落库、不改引擎。

用法:
  python scripts/probe_tushare_gates.py          # 全部 5 门
  python scripts/probe_tushare_gates.py 1 3 5    # 只跑指定门
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from stockagent.data import tushare_client as tc  # noqa: E402


def _recent_trade_dates(n: int = 3) -> list[str]:
    """最近 n 个 daily_basic 有数据的交易日(YYYYMMDD), 从今天往回扫。"""
    from datetime import date, timedelta
    out: list[str] = []
    d = date.today()
    while len(out) < n and d > date.today() - timedelta(days=20):
        try:
            df = tc.query("daily_basic", trade_date=d.strftime("%Y%m%d"),
                          fields="ts_code")
        except Exception:  # noqa: BLE001
            df = None
        if df is not None and len(df):
            out.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return out


def _gate1_forecast_by_ann_date() -> str:
    """门1: forecast/express 按 ann_date 拉全市场(文档自相矛盾, 需实证)。

    平淡披露窗(如 9 月中)本来就没几家公告, 行数不能证伪; 必须拿预告高峰日
    (1 月末年报预告 / 7 月中中报预告 deadline 惯例)验量。"""
    dates = _recent_trade_dates(2)
    heavy = ["20260715", "20260131"]  # 中报/年报预告披露高峰
    best = 0
    for d in heavy + dates:
        try:
            fc = tc.query("forecast", ann_date=d, fields="ts_code,ann_date,type")
            ex = tc.query("express", ann_date=d, fields="ts_code,ann_date")
            print(f"  forecast(ann_date={d}) -> {len(fc)} 行 | express -> {len(ex)} 行")
            best = max(best, len(fc))
        except Exception as e:  # noqa: BLE001
            print(f"  ann_date={d} 失败: {str(e)[:100]}")
    if best >= 100:
        return f"PASS — 高峰日全市场可用(max {best} 行/日)"
    if best > 0:
        return f"WEAK — 有行但高峰日未过百({best}); 迁移前须再实测真高峰日"
    return "FAIL — 按 ann_date 拉不到(预报腿保留东财 yjyg/yjkb)"


def _gate2_index_weight_guozheng() -> str:
    """门2: index_weight 是否覆盖国证系指数(399006 创业板指, 159915 的底座)。"""
    ctrl = tc.query("index_weight", index_code="000300.SH",
                    fields="ts_code,weight")
    try:
        gz = tc.query("index_weight", index_code="399006.SZ",
                      fields="ts_code,weight")
        print(f"  对照组 000300.SH -> {len(ctrl)} 行 | 国证 399006.SZ -> {len(gz)} 行")
        if len(gz) > 0:
            return "PASS — 国证系覆盖, 成分腿可全量迁 index_weight"
        return "INFO — 国证不覆盖(仅中证), 399006 类保留 csindex/现源兜底"
    except Exception as e:  # noqa: BLE001
        print(f"  399006.SZ 查询失败: {str(e)[:100]}")
        return "INFO — 国证查询报错(可能不支持该代码族), 成分腿中证部分迁、国证保留"


def _gate3_fut_continuous_contract() -> str:
    """门3: fut_daily 连续合约代码形态(主力连续 9999 风格?)——商品日线腿的映射前提。"""
    basic = tc.query("fut_basic", exchange="SHFE", fields="ts_code,symbol,name,fut_code")
    cont = [c for c in basic["ts_code"].astype(str) if c.startswith("RB")]
    print(f"  SHFE 螺纹(RB*) 合约样例: {sorted(cont)[:6]} ... 共 {len(cont)}")
    # tushare 新式代码: RB.SHF = 主力连续, RB2701.SHF = 具体月合约 — 连续腿必须测前者
    for c in ["RB.SHF"]:
        name_rows = basic[basic["ts_code"] == c]
        if len(name_rows):
            print(f"  fut_basic({c}) name = {name_rows.iloc[0].get('name')}")
        df = tc.query("fut_daily", ts_code=c, start_date="20260901",
                      end_date="20260913", fields="ts_code,trade_date,close,oi")
        print(f"  fut_daily({c}, 近两周) -> {len(df)} 行"
              + (f" 尾行 {df.iloc[-1].to_dict()}" if len(df) else ""))
        if len(df):
            return f"PASS — 主力连续 {c} 可直接拉, 商品日线腿映射可行"
    try:
        mp = tc.query("fut_mapping", ts_code="RB.SHF", fields="ts_code,trade_date,map_ts_code")
        print(f"  fut_mapping(RB.SHF) -> {len(mp)} 行")
        if len(mp):
            return "PASS — fut_mapping 提供主力映射, 商品日线腿可经映射自拼"
    except Exception as e:  # noqa: BLE001
        print(f"  fut_mapping 失败: {str(e)[:100]}")
    try:
        mp = tc.query("fut_mapping", ts_code="RB9999.XSGE", fields="ts_code,trade_date,map_ts_code")
        print(f"  fut_mapping(RB9999.XSGE) -> {len(mp)} 行")
        if len(mp):
            return "PASS — fut_mapping 提供主力映射, 商品日线腿可经映射自拼"
    except Exception as e:  # noqa: BLE001
        print(f"  fut_mapping 失败: {str(e)[:100]}")
    return "FAIL — 连续合约与映射都不可用(商品日线腿保留 sina)"


def _gate4_fut_wsr_exchanges() -> str:
    """门4: fut_wsr 仓单覆盖哪些交易所(决定库存腿能否从 CZCE 三品种扩展)。"""
    dates = _recent_trade_dates(1)
    d = dates[0]
    df = tc.query("fut_wsr", trade_date=d)
    if df is None or not len(df):
        return "FAIL — fut_wsr 按日拉为空"
    print(f"  fut_wsr 列: {list(df.columns)}")
    print("  样例:\n" + df.head(3).to_string())
    exs = []
    for col in ("exchange", "exchange_id", "market"):
        if col in df.columns:
            exs = sorted(df[col].astype(str).unique())
            print(f"  交易所({col}): {exs}")
    if "ts_code" in df.columns:
        suf = sorted({str(c).split(".")[-1] for c in df["ts_code"]})
        print(f"  ts_code 后缀: {suf}")
        exs = exs or suf
    if exs:
        return f"INFO — 当日覆盖 {exs} — 扩展范围与逐所明细核对后再定"
    return "INFO — 列结构待人工判读(见上方样例)"


def _gate5_shibor_lpr_origin() -> str:
    """门5: shibor_lpr 历史起点 + 限频真相。

    首轮实测撞到「1次/小时」限频墙(文档 120 积分档未标) → 改单次全区间调用、
    不重试(1991 起月度 ~430 行 << 单次 4000 行上限, 一次拿全)。"""
    try:
        df = tc.query("shibor_lpr", start_date="19910101", end_date="20261231",
                      fields="trade_date,lpr1y,lpr5y", retries=1)
    except Exception as e:  # noqa: BLE001
        return f"LIMIT — {str(e)[:110]} | 实施腿设计: 单次全区间、retries=1、失败留旧源"
    if not len(df):
        return "FAIL — 全区间空(接口有权限但无数据?)"
    earliest = df["trade_date"].min()
    print(f"  全区间单次调用: {len(df)} 行, 最早 {earliest}, 最晚 {df['trade_date'].max()}")
    if earliest <= "19910501":
        return "PASS — 起点不浅于金十(1991), LPR 可全量重灌"
    return f"INFO — 起点为 {earliest}(浅于金十 1991): LPR 走增量接续+重叠对账"


GATES = {
    1: ("forecast/express 按 ann_date 拉全市场", _gate1_forecast_by_ann_date),
    2: ("index_weight 国证系覆盖", _gate2_index_weight_guozheng),
    3: ("fut_daily 连续合约形态", _gate3_fut_continuous_contract),
    4: ("fut_wsr 仓单交易所覆盖", _gate4_fut_wsr_exchanges),
    5: ("shibor_lpr 历史起点", _gate5_shibor_lpr_origin),
}


def main() -> None:
    want = {int(a) for a in sys.argv[1:]} or set(GATES)
    print(f"tushare 实测门探针 (token: {'已配置' if tc.has_token() else '⚠ 未配置!'})\n")
    results = {}
    for no, (title, fn) in GATES.items():
        if no not in want:
            continue
        print(f"=== 门{no} · {title} ===")
        try:
            results[no] = fn()
        except Exception as e:  # noqa: BLE001
            results[no] = f"ERROR — {str(e)[:120]}"
        print(f"==> {results[no]}\n")
    print("=== 汇总 ===")
    for no in sorted(results):
        print(f"门{no}: {results[no]}")


if __name__ == "__main__":
    main()
