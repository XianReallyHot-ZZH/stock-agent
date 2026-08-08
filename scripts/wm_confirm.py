"""Confirm / veto draft claims — the human-in-loop quality valve (ADR-0001 · Q9).

LLM extraction is DRAFT. You verify each claim against the source (dashboard charts + transcripts)
and confirm (counts toward track record) or veto (dropped from scoring). Per Q9: bulk historical
auto-settles under sampled audit, but anything you'd act on should be confirmed first.

uid 可只输前 8 位(唯一即可)。

Usage:
  python scripts/wm_confirm.py                                # 列出全部草稿(编号)
  python scripts/wm_confirm.py --state confirmed              # 列出已确认
  python scripts/wm_confirm.py --confirm a1b2c3d4             # 确认一条
  python scripts/wm_confirm.py --veto a1b2c3d4                # 否决一条(从评分剔除)
  python scripts/wm_confirm.py --confirm-episode 2025-03-13   # 批量确认某期全部草稿
  python scripts/wm_confirm.py --reset a1b2c3d4               # 退回草稿
  python scripts/wm_confirm.py --stats                        # 各状态计数
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data.store import Store

_TYPE_CN = {"direction": "方向", "range": "区间", "level": "点位", "timing": "时点", "scenario": "情景"}
_DIR_CN = {"up": "涨", "down": "跌", "flat": "震荡"}


def _resolve(store: Store, prefix: str) -> str | None:
    if len(prefix) >= 16:
        return prefix
    ms = [c["uid"] for c in store.get_wm_claims() if c["uid"].startswith(prefix)]
    if len(ms) == 1:
        return ms[0]
    print(f"⚠ 前缀 {prefix!r}: {'无匹配' if not ms else f'{len(ms)} 条不唯一(多输几位)'}")
    return None


def _list(store: Store, state: str) -> None:
    rows = store.get_wm_claims(state=state)
    print(f"\n=== {state} ({len(rows)} 条) ===")
    for i, c in enumerate(rows):
        t = _TYPE_CN.get(c["claim_type"], c["claim_type"])
        d = _DIR_CN.get(c["direction"] or "", "")
        print(f"[{i}] {c['episode_date']} {c['asset']:7s} {t}{(' '+d) if d else '':4s} "
              f"hzn={c['horizon'][:10]:10s} {c['uid'][:8]}")
        print(f"    {c['statement'][:70]}")


def _stats(store: Store) -> None:
    counts = {"draft": 0, "confirmed": 0, "vetoed": 0}
    for c in store.get_wm_claims():
        counts[c["state"]] = counts.get(c["state"], 0) + 1
    print("\n=== 状态计数 ===")
    for k, v in counts.items():
        print(f"  {k:10s}: {v}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Confirm/veto western-macro draft claims.")
    ap.add_argument("--state", default="draft", help="列出该状态的 claim(draft/confirmed/vetoed)")
    ap.add_argument("--confirm", metavar="UID", help="确认一条(uid 或前缀)")
    ap.add_argument("--veto", metavar="UID", help="否决一条")
    ap.add_argument("--reset", metavar="UID", help="退回草稿")
    ap.add_argument("--confirm-episode", metavar="DATE", help="批量确认某期全部草稿")
    ap.add_argument("--veto-episode", metavar="DATE", help="批量否决某期全部草稿")
    ap.add_argument("--stats", action="store_true", help="各状态计数")
    args = ap.parse_args()

    store = Store(get_config().db_path)

    if args.stats:
        _stats(store); return
    if args.confirm or args.veto or args.reset:
        op = {"confirm": "confirmed", "veto": "vetoed", "reset": "draft"}[
            next(k for k, v in {"confirm": args.confirm, "veto": args.veto, "reset": args.reset}.items() if v)
        ]
        uid = _resolve(store, args.confirm or args.veto or args.reset)
        if not uid:
            return
        n = store.set_wm_claim_state(uid, op)
        print(f"{'✅' if op=='confirmed' else '⛔' if op=='vetoed' else '↩'} {uid[:8]} → {op}" if n
              else f"⚠ 未找到 uid {uid[:8]}")
        return
    for ep_attr, target_state in (("--confirm-episode", "confirmed"), ("--veto-episode", "vetoed")):
        ep = getattr(args, ep_attr.lstrip("--").replace("-", "_"))
        if ep:
            rows = store.get_wm_claims(episode_date=ep, state="draft")
            for c in rows:
                store.set_wm_claim_state(c["uid"], target_state)
            print(f"{'✅' if target_state=='confirmed' else '⛔'} {ep}: {len(rows)} 条 → {target_state}")
            return
    _list(store, args.state)


if __name__ == "__main__":
    main()
