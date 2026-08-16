"""Probe earnings-expectation data sources for the ETF 行业业绩预期 research (akshare 1.18.64).

调研工具·非功能代码 — feeds the ground-truth table (§4) of
docs/RESEARCH-ETF行业业绩预期.md. Zero writes to stockagent/ / config/ / DB
(survey CSV goes to data/, which is gitignored scratch).

Run:
  python scripts/probe_earnings_sources.py             # quick probes P1-P15 (~2 min)
  python scripts/probe_earnings_sources.py --coverage  # + full-pool coverage survey
                                                       # (36 ETF × 成分×官方权重 × 一致预期 join)

Kept in the repo as a living record of endpoint verdicts + dead ends (pattern:
scripts/probe_research_sources.py). Rerun anytime to re-verify; update VERDICT
below when a verdict changes.

VERDICT (confirmed 2026-08-16):
  P1  holdings top10   fund_portfolio_hold_em(symbol, date=YYYY)        ❌ 全部 JSONDecodeError 0.2s 即失败
      → 现状 etf_earnings 全零的根因：端点已死(返回非 JSON)，非参数问题
  P2  跟踪标的代码     fund_individual_basic_info_xq(symbol=ETF)        ❌ KeyError 'data'(雪球要 cookie) → ETF→指数自动映射此路不通
  P3  一致预期全市场   stock_profit_forecast_em(symbol='')              ✅ 2822行×13列(研报数/评级5档/2025-28 EPS)/1.4s
      列: 代码 名称 研报数 评级-买入..卖出 2025..2028预测每股收益
  P4  一致预期按股     stock_profit_forecast_em(symbol='600519')        ❌ TypeError NoneType(见 DEAD ENDS)
  P5  同花顺盈利预测   stock_profit_forecast_ths(symbol, indicator)     ✅ 0.2-0.3s/股; 列: 年度/机构数/最小/均值/最大/行业平均数
      (行业平均数列=行业级均值直接可得; 逐股调用, 全池成分级成本~小时级, 适合补漏而非主源)
  P6  中证成分+权重    index_stock_cons_weight_csindex(symbol)          ✅ 300行×10列 含官方权重+月度快照日期(2026-07-31)
  P7  成分备胎         index_stock_cons_csindex ✅(无权重列) / index_stock_cons_sina ❌('sh000300' 空)
  P8  申万行业成分     sw_index_third_cons('801120.SI')                 ❌ akshare 内部列数 bug(18≠17)
  P9  东财行业成分     stock_board_industry_cons_em('银行')             ⚠️ 本机 push2 代理拦截(ProxyError, 已知环境限制)
  P10 业绩快报         stock_yjkb_em(date)                              ✅ 889行(FY2025年报)/16列 净利+营收现值/同比/环比
  P11 业绩报表(正式)   stock_yjbb_em(date)                              ✅ 5896行(2026Q1 全市场)/16列/2.9s
  P12 港股盈利预测     stock_hk_profit_forecast_et(symbol)              ✅ 逐券商明细(财政年度/纯利/EPS/派息/券商/评级/目标价/更新日)
      → 港股可聚合自算 mini-consensus; QDII 缺口比预想小
  P13 巨潮评级排名     stock_rank_forecast_cninfo()                     ⚠️ 391行=研报评级明细(机构/评级/目标价), 非盈利预测数值
  P14 指数全名单       index_csindex_all ✅(2354行, 列=指数简称/指数代码, 10.5s) / index_all_cni ❌ akshare 列数 bug(26≠25)
      → ETF→指数映射: 在 csindex 名单按 指数简称 匹配(自动映射 P2 已死); 国证系指数无免费名单 → 待人工
  P15 中证指数估值     stock_zh_index_value_csindex(symbol)             ✅ 单指数近20日 市盈率1/2+股息率1/2 (bonus: 指数级当前估值, 无历史)
  P7+ sina 成分备胎    index_stock_cons_sina(symbol='000300')           ✅ 300行×20列(实时行情列, 无权重) — 纯代码格式, 无 sh/sz 前缀

DEAD ENDS (don't use):
  - stock_profit_forecast_em(symbol='<code>'): 按股查询抛
    TypeError: 'NoneType' object is not subscriptable — endpoint rot (全市场 symbol='' 仍活)。
  - fund_portfolio_hold_em: 全部 JSONDecodeError("Can not decode value starting with character ';'")
    — 东财基金 F10 持仓端点返回非 JSON(2026-08-16 实测 6/6 失败)。现状 etf_earnings 层断点根因。
  - fund_individual_basic_info_xq: KeyError 'data' — 雪球接口要求 cookie, 无 cookie 全失败。
  - sw_index_third_cons: akshare 1.18.64 列数 bug(Length mismatch 18 vs 17), 申万成分暂不可得。
  - index_all_cni (国证指数名单): akshare 1.18.64 列数 bug(26≠25) — 国证系指数无免费名单/权重。
  - index_stock_cons_sina(symbol='sz399006'): 国证指数成分不可得(EMPTY); '000300' 纯代码格式可用(无权重)。
  - fundf10.eastmoney.com/FundArchivesDatas.jsp: 404 — 东财内部 JSONP 接口已迁移, F10 网页仍可读。

COVERAGE SURVEY (2026-08-16, --coverage): 29/36 池内 ETF 经 csindex 官方成分+权重解析成功(名称哨兵
全 ✓); 3 只待人工(512480 CES半导体=中华交易服务体系 / 159326 中证电网设备主题=代码不在截断名单 /
159915 创业板指399006=国证系无免费接口); 4 只 QDII(A股一致预期不适用)。一致预期覆盖权重(研报数≥3
且两年 EPS 齐): 中位数≈82%, 最高 酒98.0%/医药97.7%/化工96.7%, 最低 房地产38.4%/钢铁52.5%/传媒64.1%。
top-10 权重 25%-85%(军工39.3%/化工43.4%/红利低波25.4%) → top-10 重仓聚合路线代表性不足实锤。
注意: index_csindex_all 名单疑似截断(000688/930697/931009 不在名单但 cons API 正常服务)。
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import akshare as ak
import pandas as pd
import yaml

pd.set_option("display.width", 220)

# ETF → 跟踪指数 (index_code, 名称验证关键词)。来源: csindex 名单精确匹配 + 东财基金 F10 网页核对
# (fundf10.eastmoney.com, 159996=中证全指家用电器 / 159326=中证电网设备主题 已 F10 确认)。
# cons API 返回的 指数名称 必须含关键词, 否则打印 ⚠️ —— 代码错误的自动哨兵。
# None = 无法映射(QDII 跨境 / 指数不在中证体系 / 代码待查) —— 这就是 E1 index_code 字段的草稿。
INDEX_CODES: dict[str, tuple[str, str] | None] = {
    "512800": ("399986", "银行"),
    "512880": ("399975", "证券"),
    "512070": ("399966", "证保"),
    "512010": ("000913", "医药"),
    "159992": ("931152", "创新药"),
    "513060": None,  # QDII 恒生医疗
    "512690": ("399987", "酒"),
    "159865": ("931946", "畜牧养殖"),
    "159825": ("000949", "农业"),
    "159996": ("930697", "家用电器"),  # F10=中证全指家用电器; 代码靠 API 名称验证
    "512480": None,  # CES半导体(中华交易服务体系, csindex/cni 均无) → E1 待人工
    "159732": ("931494", "消费电子"),
    "159819": ("931071", "人工智能"),
    "159852": ("H30202", "软件"),
    "515880": ("931160", "通信设备"),
    "512980": ("399971", "传媒"),
    "159530": ("H30590", "机器人"),
    "515030": ("399976", "新能车"),
    "515790": ("931151", "光伏产业"),
    "159326": None,  # F10=中证电网设备主题指数, 代码不在截断名单 → E1 待查
    "512400": ("000819", "有色金属"),
    "515220": ("399998", "煤炭"),
    "515210": ("930606", "钢铁"),
    "159745": ("931009", "建筑材料"),  # F10=中证全指建筑材料, 931009(41样本); 930999 实为 SHS大湾区
    "159870": ("000813", "细分化工"),
    "159611": ("H30199", "电力"),
    "560280": ("931752", "工程机械"),
    "512660": ("399967", "军工"),
    "512200": ("931775", "房地产"),
    "516110": ("931008", "汽车"),
    "159915": None,  # 创业板指399006=国证系, csindex 无 → 待人工
    "588000": ("000688", "科创50"),
    "513180": None,  # QDII 恒生科技
    "513050": None,  # QDII 中概互联
    "159941": None,  # QDII 纳指
    "512890": ("H30269", "红利低波"),
}


def _call(label: str, fn, timeout: float = 45.0, **kw):
    """Call an akshare fn with a timeout; return (df|None, err|None, elapsed_s). Never raises."""
    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            df = ex.submit(fn, **kw).result(timeout=timeout)
        el = time.time() - t0
        if df is None or (isinstance(df, pd.DataFrame) and not len(df)):
            print(f"  [{label}] EMPTY ({el:.1f}s)")
            return None, "empty", el
        print(f"  [{label}] shape={df.shape} ({el:.1f}s)  cols={list(df.columns)[:14]}")
        return df, None, el
    except Exception as e:  # noqa: BLE001
        el = time.time() - t0
        msg = f"{type(e).__name__}: {str(e)[:100]}"
        print(f"  [{label}] FAIL ({el:.1f}s) {msg}")
        return None, msg, el


def head(df: pd.DataFrame, n: int = 2, cols: int = 8):
    if df is None:
        return
    with pd.option_context("display.max_colwidth", 18):
        print(df.head(n).iloc[:, :cols].to_string())


# ---------------------------------------------------------------- P1-P15 quick probes

def p01_holdings():
    print("\n" + "=" * 70 + "\nP1 重仓股(现状路线) fund_portfolio_hold_em — 断点定位\n" + "=" * 70)
    for sym in ("512800", "159865", "513060"):
        for y in (str(datetime.now().year), str(datetime.now().year - 1)):
            df, _, _ = _call(f"{sym}/{y}", ak.fund_portfolio_hold_em, symbol=sym, date=y, timeout=30)
            head(df)
            if df is not None:
                break


def p02_xq_basic():
    print("\n" + "=" * 70 + "\nP2 跟踪标的代码 fund_individual_basic_info_xq — ETF→指数映射的免费来源\n" + "=" * 70)
    for sym in ("512800", "513060", "159941", "512890"):
        df, _, _ = _call(sym, ak.fund_individual_basic_info_xq, symbol=sym, timeout=20)
        if df is not None:
            hit = df[df["item"].astype(str).str.contains("跟踪|业绩|指数", na=False)]
            print(hit.to_string() if len(hit) else "  (无跟踪标的字段)")
        time.sleep(0.5)


def p03_forecast_market():
    print("\n" + "=" * 70 + "\nP3 一致预期(全市场一次) stock_profit_forecast_em(symbol='')\n" + "=" * 70)
    df, _, _ = _call("market", ak.stock_profit_forecast_em, symbol="", timeout=90)
    head(df, n=3)
    return df


def p04_forecast_symbol():
    print("\n" + "=" * 70 + "\nP4 一致预期(按股) stock_profit_forecast_em(symbol='600519') — 预期死点\n" + "=" * 70)
    _call("600519", ak.stock_profit_forecast_em, symbol="600519", timeout=30)


def p05_forecast_ths():
    print("\n" + "=" * 70 + "\nP5 同花顺盈利预测 stock_profit_forecast_ths — 逐股成本测量\n" + "=" * 70)
    t0 = time.time()
    n_ok = 0
    for sym in ("600519", "000858", "601398", "300750", "002594"):
        df, err, _ = _call(sym, ak.stock_profit_forecast_ths, symbol=sym,
                           indicator="预测年报每股收益", timeout=30)
        n_ok += df is not None
        time.sleep(0.3)
    dt = time.time() - t0
    print(f"  → 5股 {n_ok} 成功, 合计 {dt:.0f}s, 均值 {dt/5:.1f}s/股(含限速间隔)")


def p06_csindex_weight():
    print("\n" + "=" * 70 + "\nP6 中证成分+官方权重 index_stock_cons_weight_csindex\n" + "=" * 70)
    df, _, _ = _call("000300", ak.index_stock_cons_weight_csindex, symbol="000300", timeout=45)
    head(df)


def p07_cons_fallbacks():
    print("\n" + "=" * 70 + "\nP7 成分备胎 index_stock_cons_csindex / index_stock_cons_sina\n" + "=" * 70)
    df, _, _ = _call("csindex/000905", ak.index_stock_cons_csindex, symbol="000905", timeout=30)
    head(df)
    for s in ("000300", "sz399006"):
        df, _, _ = _call(f"sina/{s}", ak.index_stock_cons_sina, symbol=s, timeout=30)
        head(df)


def p08_sw_cons():
    print("\n" + "=" * 70 + "\nP8 申万行业成分 sw_index_third_cons\n" + "=" * 70)
    df, _, _ = _call("801120.SI", ak.sw_index_third_cons, symbol="801120.SI", timeout=30)
    head(df)


def p09_board_industry_cons():
    print("\n" + "=" * 70 + "\nP9 东财行业成分 stock_board_industry_cons_em(银行)\n" + "=" * 70)
    df, _, _ = _call("银行", ak.stock_board_industry_cons_em, symbol="银行", timeout=30)
    head(df)


def p10_yjkb():
    print("\n" + "=" * 70 + "\nP10 业绩快报 stock_yjkb_em(date=20251231)\n" + "=" * 70)
    df, _, _ = _call("20251231", ak.stock_yjkb_em, date="20251231", timeout=60)
    head(df, n=2, cols=12)


def p11_yjbb():
    print("\n" + "=" * 70 + "\nP11 业绩报表(正式报) stock_yjbb_em(date=20260331)\n" + "=" * 70)
    df, _, _ = _call("20260331", ak.stock_yjbb_em, date="20260331", timeout=60)
    head(df, n=2, cols=12)


def p12_hk_forecast():
    print("\n" + "=" * 70 + "\nP12 港股盈利预测 stock_hk_profit_forecast_et — QDII 缺口判定\n" + "=" * 70)
    for sym in ("00700", "09988"):
        df, _, _ = _call(sym, ak.stock_hk_profit_forecast_et, symbol=sym, timeout=30)
        head(df)
        time.sleep(0.5)


def p13_cninfo_rank():
    print("\n" + "=" * 70 + "\nP13 巨潮预测排名 stock_rank_forecast_cninfo\n" + "=" * 70)
    df, _, _ = _call("no-arg", ak.stock_rank_forecast_cninfo, timeout=20)
    head(df)


def p14_index_lists():
    print("\n" + "=" * 70 + "\nP14 指数全名单 index_csindex_all / index_all_cni — ETF→指数映射底表\n" + "=" * 70)
    cs, _, _ = _call("csindex_all", ak.index_csindex_all, timeout=60)
    head(cs)
    cni, _, _ = _call("cni_all", ak.index_all_cni, timeout=60)
    head(cni)
    return cs, cni


def p15_csindex_value():
    print("\n" + "=" * 70 + "\nP15 中证指数估值 stock_zh_index_value_csindex(000934)\n" + "=" * 70)
    df, _, _ = _call("000934", ak.stock_zh_index_value_csindex, symbol="000934", timeout=30)
    head(df, n=3, cols=10)


# ---------------------------------------------------------------- coverage survey (Batch 5)

def _find_code(cs: pd.DataFrame | None, cni: pd.DataFrame | None, pattern: str) -> tuple[str, str, str]:
    """在官方指数名单里按 指数简称 匹配(先精确==后子串) → (code, official_name, list_name). 多命中取第一个并标注."""
    for lst, df in (("csindex", cs), ("cni", cni)):
        if df is None or not pattern:
            continue
        ncol = next((c for c in df.columns if "简称" in str(c) or "名称" in str(c)), None)
        ccol = next((c for c in df.columns if "代码" in str(c)), None)
        if not ncol or not ccol:
            continue
        names = df[ncol].astype(str)
        hit = df[names == pattern]
        how = ""
        if not len(hit):
            hit = df[names.str.contains(pattern, na=False, regex=False)]
            how = "(子串)"
        if len(hit):
            return (str(hit.iloc[0][ccol]).strip(), str(hit.iloc[0][ncol]).strip(),
                    f"{lst}{how}{'(多命中)' if len(hit) > 1 else ''}")
    return "", "", ""


def _consensus_map(df: pd.DataFrame) -> pd.DataFrame:
    """Whole-market consensus → DataFrame[code, n_reports, eps_fy1, eps_fy2] (财年滚动对齐)."""
    y0 = datetime.now().year
    eps_cols = {}
    for c in df.columns:
        if "预测每股收益" in str(c):
            try:
                eps_cols[int(str(c)[:4])] = c
            except ValueError:
                pass
    yrs = sorted(y for y in eps_cols if y >= y0)[:2]
    if len(yrs) < 2:
        print(f"  ⚠️ 预测年度列不足: {list(df.columns)}")
        return pd.DataFrame(columns=["code", "n_reports", "eps_fy1", "eps_fy2"])
    print(f"  财年对齐: fy1={yrs[0]} fy2={yrs[1]} (全表 {len(df)} 只, ≈全A的 {len(df)/54:.0f}%)")
    return pd.DataFrame({
        "code": df["代码"].astype(str),
        "n_reports": pd.to_numeric(df.get("研报数"), errors="coerce"),
        "eps_fy1": pd.to_numeric(df[eps_cols[yrs[0]]], errors="coerce"),
        "eps_fy2": pd.to_numeric(df[eps_cols[yrs[1]]], errors="coerce"),
    })


def coverage_survey(cs, cni):
    print("\n" + "=" * 70 + "\nCOVERAGE 全池测量: 36 ETF → 跟踪指数(硬编码码表+名称哨兵) → 成分×权重 → 一致预期 join\n" + "=" * 70)
    pool = yaml.safe_load((Path(__file__).resolve().parent.parent / "config" / "etf_pool.yaml").read_text(encoding="utf-8"))
    etfs = pool["rotation_pool"]

    cons_df, _, _ = _call("consensus", ak.stock_profit_forecast_em, symbol="", timeout=90)
    if cons_df is None:
        print("  ✗ 全市场一致预期拉取失败 — survey abort")
        return
    cmap = _consensus_map(cons_df)
    ok = cmap[(cmap["n_reports"] >= 3) & cmap["eps_fy1"].notna() & (cmap["eps_fy1"] > 0) & cmap["eps_fy2"].notna()]
    print(f"  可用覆盖(n_reports≥3 且 fy1/fy2 EPS 齐): {len(ok)} 只")

    rows = []
    for e in etfs:
        sym, name = e["symbol"], e["name"]
        ent = INDEX_CODES.get(sym)
        if ent is None:
            tag = "—QDII" if sym in ("513060", "513180", "513050", "159941") else "—待人工"
            rows.append({"symbol": sym, "name": name, "group": e.get("group", ""), "index_code": tag,
                         "index_name": "", "ok": "—", "n_cons": "—", "n_all": "—",
                         "cons_weight%": "—", "top10_weight%": "—"})
            print(f"  → {sym} {name}: {tag[1:]}")
            continue
        idx, expect = ent
        cons_w, n_cons, n_all, top10_w = float("nan"), 0, 0, float("nan")
        okflag = "?"
        df, err, _ = _call(f"cons:{idx}", ak.index_stock_cons_weight_csindex, symbol=idx, timeout=40)
        if df is not None:
            iname = str(df["指数名称"].iloc[0]) if "指数名称" in df.columns else ""
            okflag = "✓" if expect in iname else f"⚠️名称={iname}"
            wcol = "权重" if "权重" in df.columns else None
            if wcol:
                cc = df[["成分券代码", wcol]].copy()
                cc.columns = ["code", "weight"]
                cc["code"] = cc["code"].astype(str).str.zfill(6)
                cc["weight"] = pd.to_numeric(cc["weight"], errors="coerce").fillna(0)
                m = cc.merge(ok[["code"]], on="code", how="inner")
                n_all = len(cc)
                n_cons = len(m)
                cons_w = float(m["weight"].sum() / cc["weight"].sum()) if cc["weight"].sum() else 0.0
                top10_w = float(cc.nlargest(10, "weight")["weight"].sum())  # top-10 代表性(§8.4)
        rows.append({"symbol": sym, "name": name, "group": e.get("group", ""), "index_code": idx,
                     "index_name": okflag, "n_cons": n_cons, "n_all": n_all,
                     "cons_weight%": round(cons_w * 100, 1) if cons_w == cons_w else "—",
                     "top10_weight%": round(top10_w, 1) if top10_w == top10_w else "—"})
        print(f"  → {sym} {name}: {idx} {okflag} 覆盖权重={rows[-1]['cons_weight%']}% 命中={n_cons}/{n_all}")
        time.sleep(0.3)

    out = pd.DataFrame(rows)
    dest = Path(__file__).resolve().parent.parent / "data" / "consensus_coverage_survey.csv"
    out.to_csv(dest, index=False, encoding="utf-8-sig")
    print(f"\n  saved → {dest}")
    print(out.to_string())


def main():
    cov = "--coverage" in sys.argv
    only = "--survey-only" in sys.argv
    print(f"akshare {ak.__version__}  |  {datetime.now():%Y-%m-%d %H:%M}  |  coverage={cov}")
    if not only:
        p01_holdings(); p02_xq_basic()
        p03_forecast_market(); p04_forecast_symbol(); p05_forecast_ths()
        p06_csindex_weight(); p07_cons_fallbacks(); p08_sw_cons(); p09_board_industry_cons()
        p10_yjkb(); p11_yjbb(); p12_hk_forecast(); p13_cninfo_rank()
    cs = cni = None
    if cov:
        cs, cni = p14_index_lists()
        if not only:
            p15_csindex_value()
        coverage_survey(cs, cni)
    print("\n" + "=" * 70 + "\nDONE — update VERDICT/DEAD ENDS in this docstring.\n" + "=" * 70)


if __name__ == "__main__":
    main()
