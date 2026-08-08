"""Sampled extraction audit (ADR-0001 · Q9).

Random sample of claims; for each, locate the statement in the source transcript and print the
matching context, so a human can judge faithfulness + field correctness. Output = audit sheet.
Compute the error rate from the judgments (done offline / by the reviewer).

Usage:
  python scripts/wm_audit.py                 # ~28 claims (20 settled + 8 open), seed 42
  python scripts/wm_audit.py --n 40 --seed 1
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data.store import Store

DOCS = Path(__file__).resolve().parent.parent / "docs" / "Billibili-JZ" / "western_economy"
_TYPE_CN = {"direction": "方向", "range": "区间", "level": "点位", "timing": "时点", "scenario": "情景"}
_DIR_CN = {"up": "涨", "down": "跌", "flat": "震荡"}


def _snippet(transcript: str, statement: str) -> tuple[str, int]:
    """Best-matching ~240-char window in the transcript by keyword overlap with the statement."""
    kws = re.findall(r"\d+\.?\d*|[一-龥]{2,}", statement)
    kws = [k for k in kws if (k.isdigit() or len(k) >= 2)][:8]
    if not kws or not transcript:
        return ("", 0)
    best, best_score = "", -1
    step = 40
    for i in range(0, max(1, len(transcript) - 240), step):
        win = transcript[i:i + 240]
        score = sum(1 for k in kws if k in win)
        if score > best_score:
            best, best_score = win, score
    return (best.strip()[:220], best_score)


def main() -> None:
    ap = argparse.ArgumentParser(description="Sampled audit of extracted claims.")
    ap.add_argument("--n", type=int, default=28)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)

    st = Store(get_config().db_path)
    sett = {s["claim_uid"] for s in st.get_wm_settlements()}
    settled = [c for c in st.get_wm_claims() if c["uid"] in sett]
    unsettled = [c for c in st.get_wm_claims() if c["uid"] not in sett]
    n_set = min(args.n - 8, len(settled))
    n_un = min(8, len(unsettled))
    sample = random.sample(settled, n_set) + random.sample(unsettled, n_un)
    random.shuffle(sample)

    for c in sample:
        ep = c["episode_date"]
        paths = [p for p in DOCS.glob(f"{ep}_*.txt") if not p.name.endswith("_segs.txt")]
        trans = paths[0].read_text(encoding="utf-8") if paths else ""
        snip, score = _snippet(trans, c["statement"]) if trans else ("(无转写文件)", 0)
        flag = "★已结算" if c["uid"] in sett else "未结算"
        t = _TYPE_CN.get(c["claim_type"], c["claim_type"])
        d = _DIR_CN.get(c["direction"] or "", "")
        print(f"\n[{flag}|命中关键词{score}] {ep} {c['asset']} {t}{d} hzn={c['horizon'][:10]}")
        print(f"  断言: {c['statement'][:78]}")
        print(f"  原文: {snip if snip else '(未找到匹配片段——疑似抽取偏差)'}")
    print(f"\n=== 共 {len(sample)} 条(已结算 {n_set} / 未结算 {n_un}),seed={args.seed} ===")


if __name__ == "__main__":
    main()
