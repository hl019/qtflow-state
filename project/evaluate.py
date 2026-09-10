#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
S1 抽取准确率评估：解析器输出 对照 人工标注基准
==============================================
用法：
    python evaluate.py --pred data/parsed_retrospective.json \
                       --gt ../../test-samples/E节点标注基准.md

输出两个口径（都给，不挑好看的那个）：
    1. 准确率 Precision：解析器抽出来的，有多少是对的
    2. 覆盖率 Recall：人工标注的节点，有多少被抽到了

口径说明（避免自欺）：
    - 标注基准里存在"同日多条"（如 2026-02-05 有两条），而解析器会把同日内容并成一条，
      因此按**日期分组**比对：解析器一条命中同日任一标注节点，即计为命中。
    - 标注中"E3 / E4"这类多节点条目，任一命中即算命中。
    - 置信度"低"的标注项默认不计入严格口径（可在基准里声明）。
"""

import argparse
import json
import re
import sys
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

E_RE = re.compile(r"E\d+")


def load_ground_truth(path):
    """从人工标注基准（Markdown 表格）解析出标注条目"""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 5 or not cells[0].isdigit():
                continue
            rows.append({
                "num": int(cells[0]),
                "date": cells[1],
                "event": cells[2],
                "nodes": set(E_RE.findall(cells[3])),
                "raw_node": cells[3],
                "conf": cells[4],
            })
    return rows


def norm_date(d):
    """2026-06-16~18 → 2026-06-16（取起始日）"""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", d or "")
    return m.group(1) if m else None


def load_pred(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return d if isinstance(d, list) else (d.get("items") or d.get("records") or [])


def evaluate(gt_rows, pred_rows, skip_conf=("低",)):
    gt_by_date = defaultdict(list)
    for r in gt_rows:
        d = norm_date(r["date"])
        if d:
            gt_by_date[d].append(r)

    hit = miss = unaligned = 0
    details = []
    covered = set()

    for p in pred_rows:
        d = norm_date(p.get("date"))
        node = p.get("node")
        cands = [c for c in gt_by_date.get(d, []) if c["conf"] not in skip_conf]
        if not cands:
            unaligned += 1
            details.append((d, node, "—", "无同日标注可对照"))
            continue
        ok = any(node in c["nodes"] for c in cands)
        if ok:
            hit += 1
            covered.update(c["num"] for c in cands if node in c["nodes"])
        else:
            miss += 1
        details.append((d, node, "✓" if ok else "✗",
                        " / ".join(sorted({n for c in cands for n in c["nodes"]})) or "—"))

    strict_rows = [r for r in gt_rows if r["conf"] not in skip_conf and norm_date(r["date"])]
    return {
        "hit": hit, "miss": miss, "unaligned": unaligned,
        "precision": hit / (hit + miss) if (hit + miss) else 0,
        "recall": len(covered) / len(strict_rows) if strict_rows else 0,
        "gt_total": len(gt_rows), "gt_strict": len(strict_rows),
        "covered": len(covered), "details": details,
    }


def main():
    ap = argparse.ArgumentParser(description="S1 抽取准确率评估")
    ap.add_argument("--pred", required=True, help="解析器输出 JSON")
    ap.add_argument("--gt", required=True, help="人工标注基准 Markdown")
    args = ap.parse_args()

    gt = load_ground_truth(args.gt)
    pred = load_pred(args.pred)
    r = evaluate(gt, pred)

    print("=" * 64)
    print("S1 抽取准确率评估（解析器输出 对照 人工标注基准）")
    print("=" * 64)
    print(f"人工标注条目：{r['gt_total']} 条（严格口径 {r['gt_strict']} 条，已排除低置信）")
    print(f"解析器输出　：{len(pred)} 条")
    print("-" * 64)
    print(f"  准确率 Precision：{r['hit']}/{r['hit'] + r['miss']} = {r['precision'] * 100:.1f}%"
          f"   {'✅ 达到 S1 ≥90%' if r['precision'] >= 0.9 else '⚠️ 未达 S1 ≥90%'}")
    print(f"  覆盖率 Recall　：{r['covered']}/{r['gt_strict']} = {r['recall'] * 100:.1f}%"
          f"    （标注节点被抽到的比例）")
    if r["unaligned"]:
        print(f"  无法对照　　　：{r['unaligned']} 条（解析器有输出但标注无同日条目）")
    print("=" * 64)
    print("\n逐条对照（日期 | 解析器判定 | 结果 | 同日标注节点）")
    print("-" * 64)
    for d, node, mark, exp in r["details"]:
        print(f"  {d} | {node:>4} | {mark} | {exp}")


if __name__ == "__main__":
    main()
