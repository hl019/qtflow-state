"""
交付自检脚本 —— 验证解析链路可运行。

参照 qtdata 的 pytest 体系设计，但保持零依赖，可直接用标准库运行。
覆盖四个层面：
  1. 示例文件是否存在
  2. 解析器能否产出字段
  3. 关键字段（项目、节点）是否抽取成功
  4. 输出 JSON 是否可序列化、可回读

运行方式：
    python scripts/verify_delivery.py
"""

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SAMPLE_DIR = BASE / "project" / "data" / "samples"
OUTPUT = BASE / "project" / "data" / "parsed_states.json"

# 将 project 目录加入模块搜索路径，避免依赖包安装
sys.path.insert(0, str(BASE / "project"))

checks = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    """记录一项检查结果。"""
    checks.append((name, condition, detail))
    status = "PASS" if condition else "FAIL"
    line = f"  [{status}] {name}"
    if detail:
        line += f" —— {detail}"
    print(line)
    return condition


def main() -> int:
    print("qtflow-state 交付自检")
    print("-" * 50)

    # 1. 示例文件
    samples = sorted(SAMPLE_DIR.glob("*.txt")) if SAMPLE_DIR.exists() else []
    check("示例邮件存在", len(samples) > 0, f"共 {len(samples)} 个文件")
    if not samples:
        return _summary()

    # 2. 解析器可导入
    try:
        from parser import parse_email  # type: ignore
        check("解析器可导入", True)
    except Exception as exc:  # noqa: BLE001
        check("解析器可导入", False, str(exc))
        return _summary()

    # 3. 逐封解析并检查关键字段
    records = []
    for path in samples:
        text = path.read_text(encoding="utf-8")
        record = parse_email(text, source=path.name)
        records.append(record)
        ok = bool(record.get("project")) and bool(record.get("node"))
        check(
            f"{path.name} 关键字段抽取",
            ok,
            f"项目={record.get('project')} 节点={record.get('node')}",
        )

    # 4. 证据字段必须存在（用于人工复核）
    has_evidence = all(r.get("evidence") for r in records)
    check("证据字段完整", has_evidence, "每条记录均保留 evidence")

    # 5. JSON 可序列化且可回读
    try:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        reread = json.loads(OUTPUT.read_text(encoding="utf-8"))
        check("JSON 输出可回读", len(reread) == len(records), f"{len(reread)} 条记录")
    except Exception as exc:  # noqa: BLE001
        check("JSON 输出可回读", False, str(exc))

    return _summary()


def _summary() -> int:
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print("-" * 50)
    print(f"结果：{passed}/{total} 项通过")
    if passed == total:
        print("交付自检通过")
        return 0
    print("存在未通过项，请修复后再交付")
    return 1


if __name__ == "__main__":
    sys.exit(main())
