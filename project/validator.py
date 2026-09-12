#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
交付清单校验器：区分三类节点状态，输出人能看懂的校验报告
==========================================================
对应核心工作二「交付清单校验器」。

三分类规则（可解释、可复核）：
    设 max_seq = 已记录节点的最大序号（流程已走到的位置）
    对 E1–E18 中没有任何记录的节点 n：
    1. 应有但缺失   seq(n) < max_seq
       —— 流程已走到它后面，它却没有任何记录（该补记录/该核查）
    2. 流程未走到   seq(n) > max_seq 且距最后记录未超过 gap 天
       —— 项目还在推进中，尚未到达该环节
    3. 待确认       seq(n) > max_seq 且距最后记录已超过 gap 天
       —— 项目大概率已进入收尾/复盘期，这些环节未见记录，
          需人工确认是否线下发生（系统只摆事实，不下结论）

用法：
    python validator.py --pred data/parsed_retrospective.json --check
    python validator.py --pred data/parsed_retrospective.json \
        --gt ../../test-samples/E节点标注基准.md --evaluate
"""

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

NODE_NAMES = {
    "E1": "承接确认", "E2": "项目策划书", "E3": "商务报价单",
    "E4": "报价确认", "E5": "商务合同", "E6": "合同已盖章",
    "E7": "项目正式启动", "E8": "进度周报", "E9": "项目风险同步",
    "E10": "需求变更", "E11": "验收通知", "E12": "项目交付物",
    "E13": "尾款支付通知", "E14": "开票资料", "E15": "发票已开具",
    "E16": "项目结项确认", "E17": "交付后支持与回访", "E18": "长期合作邀请",
}

ALL_NODES = list(NODE_NAMES.keys())


def seq(node):
    return int(node[1:])


def load_pred(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = data if isinstance(data, list) else (
        data.get("items") or data.get("records") or [data])
    return rows


def classify(rows, as_of, gap_days):
    """三分类。返回 (recorded, missing_detail, last_date, max_seq, gap)"""
    recorded = {}          # node -> 最新日期
    for r in rows:
        node = (r.get("node") or "").strip()
        if node not in NODE_NAMES:
            continue
        d = r.get("date") or ""
        if node not in recorded or (d and d > recorded[node]):
            recorded[node] = d

    max_seq = max((seq(n) for n in recorded), default=0)
    last_date = max(recorded.values(), default=None)
    gap = None
    if last_date:
        try:
            gap = (as_of - date.fromisoformat(last_date[:10])).days
        except ValueError:
            gap = None

    ended = gap is not None and gap > gap_days
    missing = {"应有但缺失": [], "流程未走到": [], "待确认": []}
    for n in ALL_NODES:
        if n in recorded:
            continue
        if seq(n) < max_seq:
            missing["应有但缺失"].append(n)
        elif ended:
            missing["待确认"].append(n)
        else:
            missing["流程未走到"].append(n)
    return recorded, missing, last_date, max_seq, gap, ended


def show_check(rows, as_of, gap_days, project=""):
    recorded, missing, last_date, max_seq, gap, ended = classify(
        rows, as_of, gap_days)

    pname = project or ((rows[0].get("project") or "未命名项目")
                        if rows else "未命名项目")
    print("=" * 68)
    print(f"交付清单校验报告：{pname}")
    print(f"校验基准日：{as_of} ｜ 收尾判定阈值：>{gap_days} 天无新记录")
    print("=" * 68)
    print(f"\n✅ 已有记录：{len(recorded)} 个节点"
          f"（最新 {last_date}，流程已到 {max_seq} 最大序号，"
          f"距最后记录 {gap if gap is not None else '—'} 天，"
          f"判定项目{'已进入收尾期' if ended else '仍在推进中'}）")

    print(f"\n🔴 应有但缺失（流程已走到其后，但无任何记录）"
          f"—— {len(missing['应有但缺失'])} 个")
    for n in missing["应有但缺失"]:
        print(f"   {n}  {NODE_NAMES[n]}")
    if not missing["应有但缺失"]:
        print("   （无）")

    print(f"\n🟡 待确认（项目已进入收尾期，未见记录，需人工确认是否线下发生）"
          f"—— {len(missing['待确认'])} 个")
    for n in missing["待确认"]:
        print(f"   {n}  {NODE_NAMES[n]}")
    if not missing["待确认"]:
        print("   （无）")

    print(f"\n⚪ 流程未走到（项目仍在推进中，尚未到达）"
          f"—— {len(missing['流程未走到'])} 个")
    for n in missing["流程未走到"]:
        print(f"   {n}  {NODE_NAMES[n]}")
    if not missing["流程未走到"]:
        print("   （无）")

    print("\n说明：本报告只陈述记录的有无与位置，不推定线下是否实际发生；")
    print("      「待确认」项正是需要商务经理人工核对的部分。")


# ---------- S3 评估：对照人工标注基准 ----------

def parse_gt(path):
    """从标注基准 md 提取：人工判定有记录的节点集合 + 人工判缺失的节点集合"""
    text = Path(path).read_text(encoding="utf-8")
    gt_records, gt_missing, section = set(), {}, "other"
    for line in text.splitlines():
        if line.startswith("## 时间线节点标注"):
            section = "timeline"
            continue
        if line.startswith("## 关键缺失节点"):
            section = "missing"
            continue
        if line.startswith("## "):
            section = "other"
            continue
        if section == "timeline" and re.match(r"^\|\s*\d+\s*\|", line):
            nodes = set(re.findall(r"E\d+", line))
            if "缺失" in line or "状态不明" in line:
                for n in nodes:
                    gt_missing[n] = "状态不明（缺失）"
            else:
                gt_records |= nodes
        if section == "missing" and "**E" in line:
            for n in set(re.findall(r"E\d+", line)):
                # 不覆盖 timeline 段已标注的"状态不明"——它是更精确的
                # 原始标注（如 E16："现有记录中没有更晚的最终验收结论"）
                gt_missing.setdefault(n, "关键缺失")
    return gt_records, gt_missing


def evaluate(rows, gt_path, as_of, gap_days):
    recorded, missing, last_date, max_seq, gap, ended = classify(
        rows, as_of, gap_days)
    gt_records, gt_missing = parse_gt(gt_path)

    print("=" * 68)
    print("S3 校验一致率评估（校验器判定 vs 人工标注基准）")
    print("=" * 68)
    print(f"人工标注：有记录节点 {len(gt_records)} 个，"
          f"关键缺失 {len(gt_missing)} 个"
          f"（{'、'.join(sorted(gt_missing, key=seq))}）")

    agree, disagree, detail = 0, 0, []
    for n in sorted(gt_records, key=seq):
        ok = n in recorded
        detail.append((n, NODE_NAMES[n], "有记录",
                       "有记录" if ok else "无记录", ok))
        agree += ok
        disagree += (not ok)
    for n in sorted(gt_missing, key=seq):
        pred = ("应有但缺失" if n in missing["应有但缺失"]
                else "待确认" if n in missing["待确认"]
                else "流程未走到" if n in missing["流程未走到"] else "有记录")
        gt = gt_missing[n]
        ok = (pred == "应有但缺失" and gt == "关键缺失") or \
             (pred == "待确认" and gt == "状态不明（缺失）")
        detail.append((n, NODE_NAMES[n], gt, pred, ok))
        agree += ok
        disagree += (not ok)

    total = agree + disagree
    rate = agree / total * 100 if total else 0.0

    print(f"\n{'节点':<6}{'名称':<12}{'人工标注':<14}{'校验器判定':<12}一致?")
    print("-" * 68)
    for n, name, g, p, ok in detail:
        print(f"{n:<6}{name:<12}{g:<14}{p:<12}{'✅' if ok else '❌'}")

    print("-" * 68)
    print(f"一致 {agree}/{total} = {rate:.1f}%"
          f"   {'✅ 达标（目标 ≥95%）' if rate >= 95 else '❌ 未达标（目标 ≥95%）'}")
    print("\n口径说明：")
    print("  · 对照集 = 人工明确表态的节点（有记录 + 关键缺失），共"
          f" {total} 个")
    print("  · 人工「关键缺失」↔ 校验器「应有但缺失」；人工「状态不明」"
          "↔ 校验器「待确认」")
    print("  · 收尾判定依赖基准日与 gap 阈值（当前"
          f" {as_of} / >{gap_days} 天），参数变化会影响"
          "「待确认 vs 流程未走到」的归类")
    return rate


def main():
    ap = argparse.ArgumentParser(description="交付清单校验器（三分类 + S3 评估）")
    ap.add_argument("--pred", required=True, help="解析器输出的 JSON")
    ap.add_argument("--check", action="store_true", help="输出三分类校验报告")
    ap.add_argument("--gt", help="人工标注基准 md（配合 --evaluate）")
    ap.add_argument("--evaluate", action="store_true", help="计算 S3 一致率")
    ap.add_argument("--as-of", default=str(date.today()),
                    help="校验基准日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--gap-days", type=int, default=30,
                    help="收尾判定阈值：距最后记录超过该天数视为收尾期（默认 30）")
    args = ap.parse_args()

    rows = load_pred(args.pred)
    as_of = date.fromisoformat(args.as_of)
    if args.check:
        show_check(rows, as_of, args.gap_days)
    if args.evaluate:
        if not args.gt:
            ap.error("--evaluate 需要配合 --gt 提供标注基准")
        evaluate(rows, args.gt, as_of, args.gap_days)
    if not args.check and not args.evaluate:
        ap.print_help()


if __name__ == "__main__":
    main()
