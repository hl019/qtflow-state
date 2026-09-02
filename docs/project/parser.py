"""
邮件解析器 —— 从邮件文本中提取流程状态。

当前版本为规则解析（正则），不依赖外部服务，保证可离线运行。
后续版本会接入 LLM 处理表述多样的邮件，规则解析作为兜底与校验基准。

设计原则：
  1. 每一条抽取结果都保留 evidence（证据原文），供人工复核。
  2. 抽取失败的字段返回 None，不猜测、不填充默认值。
  3. 解析器只负责"抽取事实"，不负责"判断对错"——判断留给人。
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

# E1 至 E18 的节点关键词，用于在邮件正文中定位当前处于哪个节点。
# 来源：quanttide-handbook-of-business-entity/qtdata/connect/email.md
NODE_KEYWORDS = {
    "E1": "承接确认",
    "E2": "项目策划书",
    "E3": "商务报价单",
    "E3b": "关于报价的沟通",
    "E4": "报价确认",
    "E5": "商务合同",
    "E6": "合同已盖章",
    "E7": "项目正式启动",
    "E8": "进度周报",
    "E9": "项目风险同步",
    "E10": "需求变更",
    "E11": "验收通知",
    "E12": "项目交付物",
    "E13": "尾款支付通知",
    "E14": "开票资料",
    "E15": "发票已开具",
    "E16": "项目结项确认",
    "E17": "交付后支持与回访",
    "E18": "长期合作邀请",
}

# 责任角色关键词，来源同上文档的角色表。
ROLE_KEYWORDS = ["商务经理", "项目经理", "PM", "财务", "销售"]


def parse_email(text: str, source: str = "") -> Dict[str, Optional[str]]:
    """解析单封邮件，返回结构化状态记录。

    返回的字段说明：
      project            项目名称，从标题方括号中提取
      node               节点编号（E1 至 E18），由标题与正文共同判定
      subject            邮件标题原文
      actor              发件角色
      date               邮件日期，归一化为 YYYY-MM-DD
      expected_response  期望响应，取正文中以"请"开头的首句
      evidence           证据原文前 120 字，用于人工复核
      source             来源文件名，用于追溯
      parsed_at          解析时间
    """
    result = {
        "project": None,
        "node": None,
        "subject": None,
        "actor": None,
        "date": None,
        "expected_response": None,
        "evidence": text[:120].replace("\n", " ").strip(),
        "source": source,
        "parsed_at": datetime.now().isoformat(timespec="seconds"),
    }

    subject = _extract_subject(text)
    if subject:
        result["subject"] = subject
        project, node_hint = _split_subject(subject)
        result["project"] = project
        result["node"] = node_hint or _detect_node(text)

    result["actor"] = _detect_actor(text)
    result["date"] = _extract_date(text)
    result["expected_response"] = _extract_expected_response(text)

    return result


def _extract_subject(text: str) -> Optional[str]:
    """提取邮件标题。兼容"主题:"、"标题:"、"Subject:"三种写法。"""
    for pattern in (r"主题[:：]\s*(.+)", r"标题[:：]\s*(.+)", r"(?i)subject[:：]\s*(.+)"):
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()
    # 回退策略：取第一行非空文本作为标题
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return None


def _split_subject(subject: str):
    """从标题中拆分项目名与节点编号。

    标题格式示例：[项目名称] - 合作承接确认与信息收集
    """
    m = re.match(r"[\[【](.+?)[\]】]\s*[-–—]?\s*(.*)", subject)
    if not m:
        return None, None
    project = m.group(1).strip()
    remainder = m.group(2).strip()
    # 先用标题后半段匹配节点关键词
    for node_id, keyword in NODE_KEYWORDS.items():
        if keyword and keyword in remainder:
            return project, node_id
    # 标题后半段无匹配时，再用完整标题匹配一次
    for node_id, keyword in NODE_KEYWORDS.items():
        if keyword and keyword in subject:
            return project, node_id
    return project, None


def _detect_node(text: str) -> Optional[str]:
    """在正文中匹配节点编号。E3b 优先于 E3，避免误判降价协商分支。"""
    for node_id in ("E3b",) + tuple(k for k in NODE_KEYWORDS if k != "E3b"):
        keyword = NODE_KEYWORDS[node_id]
        if keyword and keyword in text:
            return node_id
    return None


def _detect_actor(text: str) -> Optional[str]:
    """识别发件角色。按角色表顺序匹配，PM 与项目经理归一为 PM。"""
    for role in ROLE_KEYWORDS:
        if role in text:
            return "PM" if role == "项目经理" else role
    return None


def _extract_date(text: str) -> Optional[str]:
    """提取日期并归一化为 YYYY-MM-DD。"""
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _extract_expected_response(text: str) -> Optional[str]:
    """提取期望响应。取正文中以"请"开头的首句，保留完整表述。"""
    m = re.search(r"请([^。！？\n]*[。！？]?)", text)
    if m:
        return ("请" + m.group(1)).strip()
    return None


def parse_directory(sample_dir: Path) -> list:
    """批量解析目录下的所有 .txt 邮件文件。"""
    records = []
    for path in sorted(sample_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        records.append(parse_email(text, source=path.name))
    return records


def main() -> int:
    """命令行入口：解析示例目录并输出 JSON。"""
    base = Path(__file__).resolve().parent
    sample_dir = base / "data" / "samples"

    if not sample_dir.exists():
        print(f"[错误] 示例目录不存在：{sample_dir}")
        return 1

    records = parse_directory(sample_dir)

    out_path = base / "data" / "parsed_states.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"已解析 {len(records)} 封邮件")
    for r in records:
        flag = "OK " if r["node"] and r["project"] else "WARN"
        print(f"  [{flag}] {r['source']}: 项目={r['project']} 节点={r['node']} 角色={r['actor']} 日期={r['date']}")
    print(f"结果已写入：{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
