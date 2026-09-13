"""生成 config/stock_industry_sw.yaml——申万 L3 名 → {type, commodity} 交叉映射。

原料: 旧 stock_industry.yaml(东财板块→{type,commodity},人工策展) × sw_industry_member
的 L3/L2 名称清单。策略: 名称归一(去Ⅰ/Ⅱ/Ⅲ后缀)后精确匹配 → difflib 相似度 ≥0.55 的
最佳单射。未匹配 → type=None 落表并打印清单(人工补——与东财时代同一维护口粮)。
幂等: 重跑覆盖生成文件; 人工补过之后再跑会保留(手工行加 `# manual` 注释则跳过覆盖?——
v1 简单覆盖,补录清单打印出来人工重新粘)。
Usage: python scripts/gen_sw_industry_yaml.py [--min-ratio 0.55]
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from stockagent.config import get_config
from stockagent.data.store import Store

_SUFFIX = re.compile(r"[ⅠⅡⅢⅣIVX]+$")


def _norm(name: str) -> str:
    """归一: 去 Ⅰ/Ⅱ/Ⅲ 罗马后缀 + 空白。白酒Ⅱ/白酒Ⅲ → 白酒。"""
    return _SUFFIX.sub("", str(name or "").strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-ratio", type=float, default=0.55)
    args = ap.parse_args()

    cfg = get_config()
    store = Store(cfg.db_path)
    old = cfg.industry_class()          # 东财板块 → {type, commodity}
    with store._conn() as c:  # noqa: SLF001 — 只读聚合
        rows = c.execute(
            "SELECT DISTINCT l3, l2, l1 FROM sw_industry_member "
            "WHERE out_date IS NULL OR out_date=''").fetchall()
    if not rows:
        print("sw_industry_member 为空——先跑 update_sw_industry")
        return

    norm_old = {}
    for k, v in old.items():
        norm_old[_norm(k)] = (str(k), v or {})

    matched: dict[str, dict] = {}
    unmatched: list[tuple[str, str, str]] = []   # (l3, l2, l1)
    used_old: set[str] = set()
    for l3, l2, l1 in sorted(rows):
        hit = None
        n3 = _norm(l3)
        if n3 in norm_old:
            hit = n3
        else:
            best = difflib.get_close_matches(n3, list(norm_old), n=1,
                                             cutoff=args.min_ratio)
            # 防串台: 相似命中必须词干有交集(防「汽车服务」配到「汽车」这类跨叶子)
            if best:
                stem = max(n3, _norm(best[0]), key=len)[:2]
                if stem[:2] in _norm(best[0]) or _norm(best[0])[:2] in n3:
                    hit = best[0]
        if hit:
            _orig, val = norm_old[hit]
            matched[l3] = {"type": val.get("type"),
                          "commodity": val.get("commodity")}
            used_old.add(hit)
        else:
            unmatched.append((l3, l2, l1))

    n_typed = sum(1 for v in matched.values() if v.get("type"))
    out = {
        "# NOTE": "申万 L3 → {type, commodity};由 scripts/gen_sw_industry_yaml.py 从"
                  " stock_industry.yaml(东财口径)交叉映射生成,未匹配项需人工补 type。",
        "industry_class": matched,
    }
    path = Path("config/stock_industry_sw.yaml")
    path.write_text(yaml.dump(out, allow_unicode=True, sort_keys=True),
                    encoding="utf-8")

    print(f"L3 总数 {len(rows)} · 已映射 {len(matched)}(有 type {n_typed}) · "
          f"未映射 {len(unmatched)} → {path}")
    if unmatched:
        print("未映射清单(补 type 即入对应轨;格式「L3名: {type: cyclic/growth/value, "
              "commodity: 品种}\"):")
        for l3, l2, l1 in unmatched:
            print(f"  {l3}  (L2 {l2} / L1 {l1})")
    # 旧表未被复用的键(东财有而申万拆并不同)——供人工核对
    unused = [k for k in old if _norm(k) not in used_old]
    print(f"旧东财键未复用 {len(unused)} 个(参考,不阻塞)")


if __name__ == "__main__":
    main()
