# 初次捕获与增量捕获设计

## 冷启动

新安装时用户级数据目录中的 `个人偏好.md` 只包含结构，不包含任何真实偏好。默认位置为 Windows `%APPDATA%\Datailor\个人偏好.md`，macOS `~/Library/Application Support/Datailor/个人偏好.md`，Linux `${XDG_DATA_HOME:-~/.local/share}/datailor/个人偏好.md`。此时：

- `decide` 返回 `decision: no_preference`。
- agent 应按原流程询问用户或继续当前任务。
- session 结束后，用户可以把对话记录交给 `capture`，系统从真实回答中生成第一批偏好。

## 初次捕获

输入可以是单个文件，也可以是目录：

- Kimi 导出的 Markdown/JSON/TXT。
- Codex 或 OpenClaw 的本地会话记录。
- 手动整理的 session 片段。

处理流程：

```text
读取文件
  -> 归一化为 role/content messages
  -> 模型提取候选偏好
  -> 与空偏好库合并
  -> 写入人类可读的 Markdown 偏好条目
```

提取标准：

- 只提取稳定、可复用的 agent 行为偏好。
- 每条偏好必须有证据 quote。
- 不把一次性任务事实当偏好。
- 不把没有用户表达支撑的推测当偏好。

## 增量捕获

增量捕获不是简单追加，而是四类合并：

| 动作 | 条件 | 结果 |
|------|------|------|
| new | 没有同类意图 | 新增偏好 |
| merge | 同类且一致 | 追加证据，增强置信 |
| replace | 用户明确表达“以后/改成/从现在开始” | 更新主偏好 |
| conflict | 同类但不确定是否变化 | 标记 `needs_review`，保留冲突证据 |

## Kimi 原始数据捕获建议

优先使用导出文件或本地可读历史，而不是屏幕抓取：

1. 找到 Kimi 历史导出目录或手动导出一批高价值对话。
2. 保持每个 session 一个文件，文件名带日期和主题。
3. 先抽样 20 个 session 做质量验证。
4. 通过 `capture --dry-run` 看候选数量，再正式写入。
5. 对误提取样本，调整模型 prompt 或增加负例，不直接手工改规则。

## 质量抽检

每批捕获后抽检：

- 偏好是否真实来自用户表达。
- 是否写清适用场景、触发意图、例外条件。
- 是否存在把临时任务误判为长期偏好的情况。
- 中文和 Markdown 结构是否完整。
