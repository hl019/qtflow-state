# qtflow-state

面向自研平台的商务流程状态数据底座。

将量潮 E1 至 E18 邮件工作流中的状态信号沉淀为结构化数据，使流程状态在过渡期（邮件）与平台期（qtcloud-business / qtdata）均保持可见、可回溯、可校验。

## 当前进度

阶段 1：解析链路已打通，可运行。

E1 至 E18 节点 Schema 定义已完成。邮件解析器已实现，支持项目名、节点、角色、日期、期望响应五个字段的抽取。交付自检脚本已通过全部 5 项检查。示例邮件 E1 已可解析并输出结构化 JSON。

下一步是接入邮件导出与群聊脱敏样本，把节点覆盖从 E1 扩展到完整链路。

## 快速开始

Windows 用户双击 `run-demo.bat`，自动完成解析与自检，无需配置环境。

也可以使用命令行：

```
python project/parser.py
python scripts/verify_delivery.py
```

## 输出示例

解析结果写入 `project/data/parsed_states.json`，单条记录形如：

```json
{
  "project": "某某高校科研数据项目",
  "node": "E1",
  "subject": "[某某高校科研数据项目] - 合作承接确认与信息收集",
  "actor": "商务经理",
  "date": "2026-08-15",
  "expected_response": "请您提供以下信息（如有资料可直接回复附件）：",
  "evidence": "主题：[某某高校科研数据项目] - 合作承接确认与信息收集 X老师您好，...",
  "source": "E1.txt",
  "parsed_at": "2026-09-01T16:38:00"
}
```
