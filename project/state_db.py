#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
状态库：把解析器输出落到 SQLite，并提供查询接口
================================================
对应核心工作一「状态数据底座 + 解析接入」的存储与查询部分。

表围绕四个概念（与方案书一致）：
    项目 project / 节点 node / 状态 event / 证据 evidence
每条记录都保留 evidence —— 这是 S2「100% 可溯源」的落点。

用法：
    python state_db.py --load data/parsed_retrospective.json   # 落库
    python state_db.py --status                                 # 各项目当前节点
    python state_db.py --trace 代码托管                          # 某项目全链路
    python state_db.py --missing                                # 哪些节点没有记录
    python state_db.py --stats                                  # 统计
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = Path(__file__).parent / "data" / "state.db"

# E1–E18 节点名（与 parser.py 的 NODE_KEYWORDS 保持一致）
NODE_NAMES = {
    "E1": "承接确认", "E2": "项目策划书", "E3": "商务报价单",
    "E4": "报价确认", "E5": "商务合同", "E6": "合同已盖章",
    "E7": "项目正式启动", "E8": "进度周报", "E9": "项目风险同步",
    "E10": "需求变更", "E11": "验收通知", "E12": "项目交付物",
    "E13": "尾款支付通知", "E14": "开票资料", "E15": "发票已开具",
    "E16": "项目结项确认", "E17": "交付后支持与回访", "E18": "长期合作邀请",
}


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS states (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            project   TEXT NOT NULL,
            node      TEXT NOT NULL,
            node_name TEXT,
            date      TEXT,
            event     TEXT,
            actor     TEXT,
            evidence  TEXT,
            source    TEXT,
            parsed_at TEXT,
            UNIQUE(project, node, date, event)
        )
    """)
    conn.commit()


def load(conn, path):
    """把解析器输出落库。重复导入同一份不会产生重复记录。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = data if isinstance(data, list) else (
        data.get("items") or data.get("records") or [data])

    init_db(conn)
    inserted = skipped = 0
    for r in rows:
        node = r.get("node") or ""
        try:
            conn.execute(
                "INSERT INTO states (project, node, node_name, date, event,"
                " actor, evidence, source, parsed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (r.get("project") or "未命名项目", node, NODE_NAMES.get(node, ""),
                 r.get("date"), r.get("event"), r.get("actor"),
                 r.get("evidence"), r.get("source"), r.get("parsed_at")),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1
    conn.commit()
    print(f"导入完成：新增 {inserted} 条，跳过重复 {skipped} 条 → {DB_PATH}")


def show_status(conn):
    """各项目当前处于哪个节点（取最新一条）"""
    projects = [r[0] for r in conn.execute(
        "SELECT DISTINCT project FROM states ORDER BY project")]
    if not projects:
        print("状态库为空，请先 --load 导入数据。")
        return
    print("=" * 64)
    print("项目当前状态")
    print("=" * 64)
    for p in projects:
        latest = conn.execute(
            "SELECT date, node, event FROM states WHERE project=?"
            " ORDER BY date DESC LIMIT 1", (p,)).fetchone()
        n = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT node) FROM states WHERE project=?",
            (p,)).fetchone()
        print(f"\n【{p}】")
        print(f"  当前节点：{latest[1]} {NODE_NAMES.get(latest[1], '')}"
              f"（{latest[0]}）")
        print(f"  最新动态：{(latest[2] or '')[:60]}")
        print(f"  已沉淀　：{n[0]} 条记录 / 覆盖 {n[1]} 个节点")


def show_trace(conn, keyword):
    """某项目的全链路（按日期升序）"""
    rows = conn.execute(
        "SELECT date, node, actor, event, evidence FROM states"
        " WHERE project LIKE ? ORDER BY date", (f"%{keyword}%",)).fetchall()
    if not rows:
        print(f"没有匹配「{keyword}」的项目。")
        return
    print("=" * 64)
    print(f"全链路：{keyword}")
    print("=" * 64)
    for d, node, actor, event, ev in rows:
        print(f"\n  {d} | {node} {NODE_NAMES.get(node, '')} | {actor or '—'}")
        print(f"    {(event or '')[:100]}")
        if ev:
            print(f"    证据：{ev[:80]}")


def show_missing(conn):
    """哪些 E 节点没有任何记录 —— 让"缺失"可见"""
    have = {r[0] for r in conn.execute("SELECT DISTINCT node FROM states")}
    miss = [(k, v) for k, v in NODE_NAMES.items() if k not in have]
    print("=" * 64)
    print("节点覆盖度检查（E1–E18 中未出现任何记录的节点）")
    print("=" * 64)
    print(f"已有记录：{len(have)} 个节点")
    print(f"缺失记录：{len(miss)} 个节点")
    if miss:
        print("-" * 64)
        for k, v in miss:
            print(f"  ⚠️ {k} {v}")
        print("-" * 64)
        print("说明：未出现不等于未发生，可能是流程未走到，也可能是记录缺失——")
        print("      这正是需要人工确认的部分（系统只摆事实，不下结论）。")


def show_stats(conn):
    total = conn.execute("SELECT COUNT(*) FROM states").fetchone()[0]
    no_ev = conn.execute(
        "SELECT COUNT(*) FROM states WHERE evidence IS NULL OR evidence=''"
    ).fetchone()[0]
    print("=" * 64)
    print("状态库统计")
    print("=" * 64)
    print(f"  记录总数　　：{total}")
    print(f"  项目数　　　：{conn.execute('SELECT COUNT(DISTINCT project) FROM states').fetchone()[0]}")
    print(f"  覆盖节点数　：{conn.execute('SELECT COUNT(DISTINCT node) FROM states').fetchone()[0]}")
    print(f"  带证据记录　：{total - no_ev}/{total}"
          f"  {'✅ S2 100% 可溯源' if no_ev == 0 else f'⚠️ {no_ev} 条缺证据'}")


def main():
    ap = argparse.ArgumentParser(description="状态库：落库与查询")
    ap.add_argument("--load", help="导入解析器输出的 JSON")
    ap.add_argument("--status", action="store_true", help="各项目当前节点")
    ap.add_argument("--trace", help="按关键词查某项目全链路")
    ap.add_argument("--missing", action="store_true", help="未出现记录的节点")
    ap.add_argument("--stats", action="store_true", help="统计")
    args = ap.parse_args()

    conn = connect()
    if args.load:
        load(conn, args.load)
    elif args.status:
        show_status(conn)
    elif args.trace:
        show_trace(conn, args.trace)
    elif args.missing:
        show_missing(conn)
    elif args.stats:
        show_stats(conn)
    else:
        ap.print_help()
    conn.close()


if __name__ == "__main__":
    main()
