"""黄金阶段定位器 — 回测闸门 (ADR-0001 · Phase 3 MVP · 只读)。

对每条黄金 claim 做*无前视*规则分类, 比对 JZ 自述阶段 → 一致性 + 混淆矩阵 + ≥60% 闸门。
判断「JZ 的黄金阶段逻辑能否被数据+规则复现」的最短一刀:
  ≥60% → 路通, 扩到美元/曲线/原油; <60% → 降级为辅助参考 (混淆矩阵定位分歧点)。

Usage:
  python scripts/wm_gold_stage_eval.py                       # 跑回测 + 闸门
  python scripts/wm_gold_stage_eval.py --show-dropped        # 打印被剔除的歧义语句
  python scripts/wm_gold_stage_eval.py --sweep               # 标定 DEV 阈值 / lookback
  python scripts/wm_gold_stage_eval.py --threshold 0.55 --min-classified 10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:  # Windows 控制台默认 GBK → 强制 utf-8, 否则 ✓✗/中文 崩
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from stockagent.config import get_config
from stockagent.data.store import Store
from stockagent.western_macro import stage

SWEEP_GRID = [
    # (dev_low, dev_high, lookback, 标签)
    (0.10, 0.90, 252, "极宽/12月"),
    (0.15, 0.85, 252, "默认/12月"),
    (0.20, 0.80, 252, "A股口径/12月"),
    (0.25, 0.75, 252, "紧/12月"),
    (0.15, 0.85, 126, "默认/6月"),
    (0.20, 0.80, 126, "A股口径/6月"),
]


def _print_confusion(confusion: dict, stages: list[str]) -> None:
    rule_stages = sorted({r for row in confusion.values() for r in row}, key=lambda s: stage.STAGE_INDEX.get(s, 99))
    cols = rule_stages or stages
    head = "JZ ＼ 规则".ljust(14) + "  ".join(s[:6].ljust(8) for s in cols)
    print(head)
    for jz in stages:
        row = confusion.get(jz, {})
        cells = "  ".join(str(row.get(r, 0)).ljust(8) for r in cols)
        total = sum(row.values())
        print(f"{jz[:12].ljust(14)}{cells}{'  <- ' + str(total) if total else ''}")


def _banner(res: dict) -> str:
    if res.get("gate_inconclusive"):
        return f"闸门 不充分 (n_classified={res['n_classified']} < {res['min_classified']})"
    return "闸门 通过 ✓" if res["gate_pass"] else "闸门 未通过 ✗"


def run_once(st: Store, threshold: float, min_classified: int, verbose: bool) -> dict:
    res = stage.evaluate_gold_stage(st, threshold=threshold, min_classified=min_classified)
    if not res["valid"]:
        print(f"⚠ {res.get('reason', '无法回测')} (黄金数据不足)")
        return res
    print(f"\n=== 黄金阶段回测 (阈值 {threshold:.0%} · 最低分类 {min_classified}) ===")
    print(f"claim: {res['n_claims']} 条 / 已分类 {res['n_classified']} / 剔除 {res['n_dropped']} / "
          f"覆盖 {res['n_episodes']} 期")
    if not np_isnan(res["soft_agreement"]):
        print(f"per-claim  一致 soft={res['soft_agreement']:.1%}  hard={res['hard_agreement']:.1%}")
    if not np_isnan(res["per_episode_soft_agreement"]):
        print(f"per-episode 一致 soft={res['per_episode_soft_agreement']:.1%}  ← 闸门口径")
    print(f"→ {_banner(res)}")
    print("\n混淆矩阵 (行=JZ自述 / 列=规则分类):")
    _print_confusion(res["confusion"], stage.STAGES)

    if verbose:
        dropped = [e for e in res["per_claim"] if e.get("dropped")]
        if dropped:
            print(f"\n--- 剔除的歧义语句 ({len(dropped)} 条) ---")
            for e in dropped:
                print(f"  [{e['dropped_reason']}] {e['episode_date']} {e['statement'][:60]}")
        # per-episode 明细
        print("\n--- per-episode ---")
        for er in res["per_episode"]:
            mark = "✓" if er["agree"] >= 1.0 else ("~" if er["agree"] > 0 else "✗")
            print(f"  {mark} {er['episode_date']}  JZ={er['jz_stage'][:8]}  规则={er['rule_stage'][:8]}  (n={er['n']})")
    return res


def np_isnan(x) -> bool:
    try:
        return x != x
    except Exception:  # noqa: BLE001
        return False


def run_sweep(st: Store, threshold: float, min_classified: int) -> None:
    print("\n=== 标定扫描 (DEV_LOW/HIGH × lookback → per-episode soft) ===")
    best = None
    for lo, hi, lb, label in SWEEP_GRID:
        stage.GOLD_DEV_LOW = lo
        stage.GOLD_DEV_HIGH = hi
        stage.GOLD_DEV_LOOKBACK = lb
        res = stage.evaluate_gold_stage(st, threshold=threshold, min_classified=min_classified)
        soft = res["per_episode_soft_agreement"]
        soft = soft if not np_isnan(soft) else 0.0
        flag = "通过" if res["gate_pass"] else ("不充分" if res["gate_inconclusive"] else "未过")
        print(f"  lo={lo:.2f} hi={hi:.2f} lb={lb:<3}  soft={soft:.1%}  n={res['n_classified']:>2}  "
              f"{flag:<4}  ({label})")
        if best is None or soft > best[0]:
            best = (soft, lo, hi, lb, res["n_classified"], flag)
    # 恢复默认
    stage.GOLD_DEV_LOW, stage.GOLD_DEV_HIGH, stage.GOLD_DEV_LOOKBACK = 0.15, 0.85, 252
    if best:
        print(f"\n→ 最优: soft={best[0]:.1%} @ lo={best[1]} hi={best[2]} lb={best[3]} "
              f"(n={best[4]}, {best[5]})")


def main() -> None:
    ap = argparse.ArgumentParser(description="黄金阶段定位器回测闸门 (只读·ADR-0001)")
    ap.add_argument("--threshold", type=float, default=0.60)
    ap.add_argument("--min-classified", type=int, default=15)
    ap.add_argument("--show-dropped", action="store_true", help="打印被剔除的歧义语句 + per-episode 明细")
    ap.add_argument("--sweep", action="store_true", help="标定 DEV 阈值 / lookback")
    args = ap.parse_args()

    st = Store(get_config().db_path)
    if args.sweep:
        run_sweep(st, args.threshold, args.min_classified)
    run_once(st, args.threshold, args.min_classified, args.show_dropped)


if __name__ == "__main__":
    main()
