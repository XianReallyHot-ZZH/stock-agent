"""LLM claim-extraction (draft) over episode transcripts (ADR-0001 · Q9).

Turn one transcript into a batch of **falsifiable** Claims + operational Rules. The LLM PARSES
stated claims (it does not forecast), so this does not violate "模型不发明数字". Output is DRAFT
(state='draft'); a human confirms → 'confirmed' before it counts toward the track record
(actionable tier). Bulk historical auto-settles under a sampled audit instead (Q9 tiered).

Idempotent + transcript-driven: process_episode() skips an episode whose date already has claims
(unless force=True); re-runs pick up newly-backfilled transcripts. Re-extracting an episode upserts
claim content but PRESERVES human state (confirmed/vetoed) via the store's ON CONFLICT clause.

Reuse: stockagent.report.llm_client.chat (OpenAI-compatible, .env key).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Optional

from ..report import llm_client
from . import drivers

log = logging.getLogger(__name__)

# canonical asset vocabulary: asset → (driver_node_id | None, series=(source, symbol) | None).
# series=None → manual settlement only (no auto-scoreable data). The LLM is instructed to emit
# `asset` as one of these keys; this map is the single source of truth for asset→data linkage.
ASSET_REGISTRY: dict[str, tuple] = {
    "黄金":   ("gold",        ("fut", "GC")),
    "白银":   (None,          ("fut", "SI")),
    "美元指数": ("usd",       ("dxy", "DXY")),
    "美债10Y": ("real_rate",  ("ust", "US10Y")),
    "美债2Y":  ("real_rate",  ("ust", "US2Y")),
    "美债30Y": ("real_rate",  ("ust", "US30Y")),
    "2s10s":   ("curve_2s10s", ("ust", "US2S10S")),
    "实际利率10Y": ("real_rate", ("fred", "DFII10")),   # 10年期TIPS收益率(黄金死敌)·FRED免费CSV
    "通胀预期10Y": ("term_premium", ("fred", "T10YIE")),  # 10年期盈亏平衡通胀·FRED
    "期限溢价10Y": ("term_premium", ("nyfed_acm", "ACMTP10")),  # NY Fed ACM 10年期期限溢价·XLS
    "标普500": ("us_equity",  ("usidx", ".INX")),
    "纳斯达克": ("us_equity", ("usidx", ".IXIC")),
    "道琼斯":  ("us_equity",  ("usidx", ".DJI")),
    "原油":    ("oil",        ("fut", "CL")),
    "铜":      ("nonferrous", ("commodity", "铜")),   # 沪铜主连 CU0(元/吨);外盘铜 akshare 数据失真,不用
    "有色":    ("nonferrous", ("commodity", "铜")),   # 有色一篮子,以沪铜代理(与项目"铜价"一致)
    "A股":     ("a_share",    ("index_daily", "000001")),  # 上证综指(JZ 的"A股/上证"点位指此;非沪深300)
    "半导体":  ("semis",      None),
    "恒生":    (None,         None),
}
ASSET_KEYS = list(ASSET_REGISTRY)
_NODE_LIST = ", ".join(f"{n['id']}({n['label']})" for n in drivers.NODES)

_SYSTEM = (
    "你是金融文本抽取助手。从一期宏观分析视频的中文转写稿中,抽出作者对未来做出的**可证伪的"
    "预测性断言(claims)**和**操作规则(rules)**。\n"
    "抽取原则:\n"
    "1. 只抽对【未来】的明确判断/目标/规则(方向、点位、区间、时点、情景分支);不抽对过去事实的陈述、"
    "不抽泛泛而谈(如「黄金长期看涨」无具体时点=不可证伪,不抽)。\n"
    "2. 每个 claim 必须有可判定的 horizon(兑现日期 YYYY-MM-DD,或明确事件触发如「9月美联储会议后」)。\n"
    "3. 【资产忠实·最重要】asset 必须是作者【在本期转写稿里实际讨论到的】标的,从给定词汇表选。"
    "若某标的本期完全没提到,绝不为它建 claim——宁可少抽,不可凭背景知识补。\n"
    "4. 【方向忠实·最重要】direction 严格照作者明说的方向:涨/上看/突破→up,跌/下探/回调→down,"
    "震荡/区间→flat。**绝不用你的宏观常识推断方向**;条件句「若X则Y」按 Y 的原话;拿不准就不抽。\n"
    "5. 【断言忠实】statement 忠实复述作者原意,**不得添加作者没说的条件/限定词/数字**"
    "(不要凭空加「若未被做空」「极限」「必然」等加工语)。\n"
    "6. claim_type 从 direction/range/level/timing/scenario 选;scenario 类同标的多分支,每条标 "
    "is_primary(主推=1,备选=0)并给同一 scenario_group。\n"
    "7. basis_nodes 从给定节点列表里选 1-3 个该断言依赖的因果驱动节点。\n"
    "8. rules 是操作纪律(禁定投/禁做空/逢回调加仓/止盈…),不是预测,单列。\n\n"
    "严格只输出一个 JSON 对象,不要任何解释文字。"
)

_USER_TMPL = (
    "本期发布日期 = {episode_date}。所有 claim 的 horizon 必须是该日期【之后】的未来日期"
    "(YYYY-MM-DD);把'9月''年底''下个月'等相对说法换算成 {episode_date} 当年或次年的具体日期,"
    "绝不抽 horizon 早于 {episode_date} 的 claim。\n"
    "节点列表(用于 basis_nodes): {nodes}\n"
    "asset 词汇表(必选其一): {assets}\n\n"
    "输出 JSON 格式:\n"
    '{{"claims": [{{"asset":"黄金","claim_type":"direction","statement":"黄金9-12月突破4800",'
    '"direction":"up","level_value":4800,"range_low":null,"range_high":null,'
    '"horizon":"2026-12-31","confidence":"strong","basis_nodes":["usd","gold"],'
    '"is_primary":1,"scenario_group":null}}], '
    '"rules": [{{"statement":"禁止在高位定投A股和美股","rule_type":"禁定投"}}]}}\n\n'
    "转写稿:\n{transcript}"
)


def _norm_stmt(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).strip()


def claim_uid(episode_date: str, asset: str, claim_type: str, statement: str) -> str:
    return hashlib.md5(
        f"{episode_date}|{asset}|{claim_type}|{_norm_stmt(statement)}".encode("utf-8")
    ).hexdigest()[:16]


def normalize_horizon(horizon: str, episode_date: str) -> str:
    """Bump a YYYY-MM-DD horizon whose year is too early — the LLM systematically dates near-future
    as past (a 2026-08 episode saying "8月" → 2025-08-31). Increment the year until horizon ≥
    episode_date (cap +3y). Non-date horizons (event triggers) pass through unchanged."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(horizon or ""))
    if not m or not episode_date:
        return horizon
    y, mo, dy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    cap = int(episode_date[:4]) + 3
    cand = f"{y:04d}-{mo:02d}-{dy:02d}"
    while cand < episode_date and y < cap:
        y += 1
        cand = f"{y:04d}-{mo:02d}-{dy:02d}"
    return cand


def normalize_claim_horizons(store) -> int:
    """Fix any stored claim whose horizon predates its episode (extraction-era bug, or claims
    extracted before the auto-correct). Idempotent — safe to run every render. Returns count fixed."""
    fixed = 0
    for c in store.get_wm_claims():
        old = c.get("horizon") or ""
        new = normalize_horizon(old, c.get("episode_date") or "")
        if new != old:
            with store._conn() as conn:
                conn.execute("UPDATE wm_claims SET horizon=? WHERE uid=?", (new, c["uid"]))
            fixed += 1
    return fixed


def rule_uid(episode_date: str, statement: str) -> str:
    return hashlib.md5(f"rule|{episode_date}|{_norm_stmt(statement)}".encode("utf-8")).hexdigest()[:16]


def _parse_llm_json(text: str) -> dict:
    if not text:
        return {"claims": [], "rules": []}
    t = text.strip()
    t = re.sub(r"^```(json)?", "", t, flags=re.IGNORECASE).strip()
    if t.endswith("```"):
        t = t[:-3].strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {"claims": [], "rules": []}
    try:
        obj = json.loads(t[start:end + 1])
    except json.JSONDecodeError:
        return {"claims": [], "rules": []}
    return {"claims": obj.get("claims") or [], "rules": obj.get("rules") or []}


def _build_claim(raw: dict, episode_date: str, asof: str) -> Optional[dict]:
    """Validate + normalize one LLM claim dict → store-ready dict, or None if invalid."""
    asset = str(raw.get("asset", "")).strip()
    if asset not in ASSET_REGISTRY:  # reject unknown asset (LLM must pick from vocab)
        return None
    ctype = str(raw.get("claim_type", "")).strip()
    if ctype not in ("direction", "range", "level", "timing", "scenario"):
        return None
    statement = str(raw.get("statement", "")).strip()
    if len(statement) < 4:
        return None
    horizon = str(raw.get("horizon", "")).strip()
    if not horizon:
        return None
    horizon = normalize_horizon(horizon, episode_date)  # auto-fix off-by-year (LLM dates near-future as past)
    node, _series = ASSET_REGISTRY[asset]
    basis = raw.get("basis_nodes") or ([node] if node else [])
    basis = [b for b in basis if drivers.valid_node(b)]
    if node and node not in basis:
        basis.append(node)
    return {
        "uid": claim_uid(episode_date, asset, ctype, statement),
        "episode_date": episode_date,
        "asset": asset,
        "claim_type": ctype,
        "statement": statement[:500],
        "direction": (str(raw.get("direction")).strip() or None)
        if raw.get("direction") in ("up", "down", "flat") else None,
        "level_value": raw.get("level_value"),
        "range_low": raw.get("range_low"),
        "range_high": raw.get("range_high"),
        "horizon": horizon[:40],
        "confidence": str(raw.get("confidence", "")).strip() or "medium",
        "basis_nodes": ",".join(basis),
        "is_primary": 1 if raw.get("is_primary") in (1, True, "1") else 0,
        "scenario_group": str(raw.get("scenario_group") or "")[:32],
        "state": "draft",
        "source": "llm",
        "created_at": asof,
    }


def _link_scenario_parents(claims: list[dict]) -> None:
    """For scenario claims sharing a scenario_group, set non-primary parent_uid → primary's uid.
    Only the primary branch counts toward edge (Q6)."""
    by_group: dict[str, list[dict]] = {}
    for c in claims:
        if c["claim_type"] == "scenario" and c.get("scenario_group"):
            by_group.setdefault(c["scenario_group"], []).append(c)
    for grp, members in by_group.items():
        primary = next((m for m in members if m["is_primary"]), members[0])
        primary["is_primary"] = 1
        for m in members:
            if m is not primary:
                m["is_primary"] = 0
                m["parent_uid"] = primary["uid"]


def extract_transcript(transcript: str, episode_date: str, asof: str,
                       use_llm: bool = True) -> tuple[list[dict], list[dict]]:
    """Returns (claims, rules) — store-ready dicts (state='draft'). Empty if LLM unavailable/failed."""
    if use_llm and not llm_client.llm_available():
        log.warning("LLM unavailable — extraction skipped for %s", episode_date)
        return [], []
    prompt = _USER_TMPL.format(nodes=_NODE_LIST, assets=" / ".join(ASSET_KEYS),
                               episode_date=episode_date, transcript=transcript)
    claims: list[dict] = []
    rules: list[dict] = []
    for attempt in range(2):  # 并发/负载下 deepseek 偶返回空 content → 重试一次
        try:
            text = llm_client.chat(prompt, system=_SYSTEM, max_tokens=6000, timeout=180)
        except Exception as e:  # noqa: BLE001
            log.warning("LLM extract failed for %s: %s", episode_date, str(e)[:120])
            break
        parsed = _parse_llm_json(text)
        claims = [c for c in (_build_claim(r, episode_date, asof) for r in parsed["claims"]) if c]
        rules = [{
            "uid": rule_uid(episode_date, str(r.get("statement", ""))),
            "episode_date": episode_date,
            "statement": str(r.get("statement", ""))[:300],
            "rule_type": str(r.get("rule_type", "")).strip()[:40] or "其他",
            "state": "draft",
        } for r in parsed["rules"] if str(r.get("statement", "")).strip()]
        if claims or rules:
            break
        if attempt == 0:
            log.info("extract %s: empty response, retrying once", episode_date)
    _link_scenario_parents(claims)
    log.info("extracted %s: %d claims, %d rules", episode_date, len(claims), len(rules))
    return claims, rules


# ---- transcript discovery (idempotent, transcript-driven) ----
def _episode_date_of(path: Path) -> Optional[str]:
    """Filename prefix YYYY-MM-DD_*."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})_", path.name)
    return m.group(1) if m else None


def discover_transcripts(docs_dir: Path, since: Optional[str] = None) -> list[Path]:
    """Non-_segs .txt transcripts under docs_dir, sorted by date. `since` filters by date prefix."""
    if not docs_dir.exists():
        return []
    out = []
    for p in docs_dir.glob("*.txt"):
        if p.name.endswith("_segs.txt"):
            continue
        d = _episode_date_of(p)
        if not d:
            continue
        if since and d < since:
            continue
        out.append(p)
    return sorted(out, key=lambda p: _episode_date_of(p) or p.name)


def process_episode(path: Path, store, asof: str, use_llm: bool = True,
                    force: bool = False) -> dict:
    """Extract one transcript → upsert DRAFT claims/rules. Skips if already extracted (unless force)."""
    ep = _episode_date_of(path)
    if not ep:
        return {"episode": path.name, "skipped": "no date"}
    if not force and store.get_wm_claims(episode_date=ep):
        return {"episode": ep, "skipped": "already extracted"}
    transcript = path.read_text(encoding="utf-8")
    claims, rules = extract_transcript(transcript, ep, asof, use_llm=use_llm)
    nc = store.upsert_wm_claims(claims) if claims else 0
    nr = store.upsert_wm_rules(rules) if rules else 0
    return {"episode": ep, "claims": nc, "rules": nr}


def process_episodes_parallel(paths: list, store, asof: str, use_llm: bool = True,
                               force: bool = False, workers: int = 1) -> list[dict]:
    """LLM 抽取 + 顺序入库(避免 SQLite 多写冲突)。workers 默认 1(顺序)——实测 deepseek 并发会
    返回空/降质 content(4 claim 掉到 1),故保质量用顺序;换并发稳定的 provider 后可调高。
    读 transcript + 跳过已抽 在主线程;LLM 调用丢线程池;future 完成一个、主线程 upsert 一个。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def work(item):
        ep, text = item
        return ep, extract_transcript(text, ep, asof, use_llm=use_llm)

    jobs = []
    for p in paths:
        ep = _episode_date_of(p)
        if not ep:
            continue
        if not force and store.get_wm_claims(episode_date=ep):
            continue  # 已抽
        jobs.append((ep, p.read_text(encoding="utf-8")))
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(work, item): item[0] for item in jobs}
        for fu in as_completed(futures):
            ep = futures[fu]
            try:
                _, (claims, rules) = fu.result()
            except Exception as e:  # noqa: BLE001
                results.append({"episode": ep, "skipped": f"err {str(e)[:50]}"})
                continue
            nc = store.upsert_wm_claims(claims) if claims else 0
            nr = store.upsert_wm_rules(rules) if rules else 0
            results.append({"episode": ep, "claims": nc, "rules": nr})
            log.info("parallel extract %s: +%d claims +%d rules", ep, nc, nr)
    return results
