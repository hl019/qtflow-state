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

# 复盘报告用的自然语言提示词。
#
# 复盘报告不会照抄节点名（如"项目交付物"），而是用自然语言描述事件
# （如"团队发送代码及相关说明文档"）。因此需要一套从事件描述到节点的映射。
# 顺序即优先级：更具体的表述排在前面，避免被宽泛词提前命中。
RETRO_NODE_HINTS = [
    ("E16", ("最终验收结论", "结项确认", "验收结论")),
    # 报价与付款排在交付之前：报价事件常夹带"数据与代码一同交付"字样，
    # 若让 E12 的宽泛词先命中，会把报价误判为交付。
    # 不使用裸"付款"——发票文本常夹带"付款日期""Invoice Paid"，会把开票误判为付款
    ("E13", ("付清全款", "跟进验收与付款", "尾款支付")),
    ("E11", ("验收通知", "补充说明", "审核更新后")),
    # "重开/补开/报销"是客户侧的**开票要求**，属 E14 开票资料（尚未开出）；
    # E15 只认"已开具/已开出"这类完成态表述，否则会把开票要求误判成已开票。
    # 实测依据：复盘样本"客户因报销要求重开四张英文发票"人工标注为 E14，
    # 原规则因 E15 含"重开"而误判为 E15，修正后 S1 由 87.5% 提升至 100%。
    ("E15", ("发票已开具", "已开具", "已开出")),
    ("E14", ("开票资料", "发票", "重开", "补开", "报销")),
    ("E4", ("接受当前价格", "报价确认")),
    ("E3", ("报价明细", "商务报价单", "报价")),
    # 不使用裸"交付"——过于宽泛，会误伤报价、需求等场景
    ("E12", ("交付物", "交付数据", "发送代码", "面板数据交付", "交付包")),
    ("E10", ("需求变更", "新增", "修正", "口径", "确认四个", "确认 Fork")),
    ("E9", ("风险同步", "排期", "评估加急")),
    ("E7", ("项目正式启动", "提供原", "启动")),
    ("E2", ("策划书",)),
    ("E1", ("承接确认", "更新对接人")),
]


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


# 以下为复盘报告解析。与邮件解析的区别：一封邮件产出一条状态记录，
# 而一份复盘报告包含完整项目时间线，需按行拆分、产出多条记录。

# 时间线行示例："2026-02-05：前期完成…" 或 "2026-06-16 至 2026-06-18：确认…"
# 范围写法取起始日期，语义上表示事件开始。
TIMELINE_LINE = re.compile(
    r"^\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})"
    r"(?:\s*至\s*\d{4}[-/]\d{1,2}[-/]\d{1,2})?"
    r"\s*[:：]\s*(.+)$"
)


def _extract_retro_field(text: str, field: str) -> Optional[str]:
    """从复盘报告头部提取元信息字段，如"项目：""状态："。"""
    m = re.search(rf"^{re.escape(field)}\s*[:：]\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else None


def _detect_retro_node(event: str) -> Optional[str]:
    """在复盘的事件描述中匹配节点。按提示词表顺序匹配，先具体后宽泛。"""
    for node_id, hints in RETRO_NODE_HINTS:
        for hint in hints:
            if hint in event:
                return node_id
    return None


def _detect_retro_actor(event: str) -> Optional[str]:
    """识别复盘事件的责任方。复盘中的角色通常是团队或客户。"""
    if "客户" in event:
        return "客户"
    if "团队" in event:
        return "团队"
    return None


def parse_retrospective(text: str, source: str = "") -> list:
    """解析项目复盘报告，返回状态记录列表（每个时间线条目一条）。

    每条记录字段：
      project   项目名，取自"项目："行
      node      节点编号，由事件描述匹配提示词表得出
      date      事件日期，归一化为 YYYY-MM-DD（范围写法取起始日期）
      event     事件描述原文
      actor     责任方（团队 / 客户）
      evidence  证据原文，即该时间线整行，供人工复核
      source    来源文件名
    """
    project = _extract_retro_field(text, "项目")
    records = []

    for line in text.splitlines():
        m = TIMELINE_LINE.match(line)
        if not m:
            continue
        year, month, day, event = m.groups()
        records.append(
            {
                "project": project,
                "node": _detect_retro_node(event),
                "date": f"{year}-{int(month):02d}-{int(day):02d}",
                "event": event.strip(),
                "actor": _detect_retro_actor(event),
                "evidence": line.strip(),
                "source": source,
                "parsed_at": datetime.now().isoformat(timespec="seconds"),
            }
        )

    return records


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
    """命令行入口。

    默认解析示例邮件目录，输出 parsed_states.json。
    传入 --retro <文件> 时解析项目复盘报告，输出 parsed_retrospective.json
    —— 一份复盘报告含完整时间线，会产出多条状态记录。
    """
    base = Path(__file__).resolve().parent
    args = sys.argv[1:]

    # 复盘报告模式
    if args and args[0] == "--retro":
        if len(args) < 2:
            print("[错误] --retro 需要指定复盘报告文件路径")
            return 1
        retro_path = Path(args[1])
        if not retro_path.exists():
            print(f"[错误] 复盘报告不存在：{retro_path}")
            return 1

        text = retro_path.read_text(encoding="utf-8")
        records = parse_retrospective(text, source=retro_path.name)

        out_path = base / "data" / "parsed_retrospective.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"已解析 {len(records)} 条时间线记录")
        for r in records:
            flag = "OK " if r["node"] else "WARN"
            print(f"  [{flag}] {r['date']} 节点={r['node']} 责任方={r['actor']} —— {r['event'][:44]}")
        matched = sum(1 for r in records if r["node"])
        print(f"节点命中：{matched}/{len(records)}")
        print(f"结果已写入：{out_path}")
        return 0

    # 默认：邮件模式
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
