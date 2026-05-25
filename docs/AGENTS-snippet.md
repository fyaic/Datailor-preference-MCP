# Personal Preference Agent

## 三层偏好注入

Session 开始时，优先读取静态规则和兜底规则：

- `data/.injection/preference_rules.md`
- `data/.injection/fallback_rules.md`

如果这些文件尚未生成，调用 `sync_preference_injection` 或执行 `python -m preference_agent.cli sync-injection` 生成。

随后调用 `prewarm_preferences` 做 session 级预热，把返回的 `agent_instruction` 作为本 session 的硬约束。

## 动态决策

在回复用户、执行代码修改、创建文档、写入外部系统或询问确认之前，调用个人偏好工具：

```json
{
  "tool": "get_preference_decision",
  "arguments": {
    "agent": "codex-or-openclaw",
    "task": "描述你准备回复或执行的动作",
    "context": {
      "project": "当前项目",
      "risk": "low|medium|high",
      "files": ["相关文件"]
    }
  }
}
```

执行规则：

- 如果返回 `decision: apply`，把 `agent_instruction` 作为本轮硬约束。
- 如果返回 `no_preference`，按正常流程继续；不要伪造用户偏好。
- 如果返回 `escalate: true`，对高风险或不确定事项询问用户。
- 如果 MCP 不可用，至少遵守 `fallback_rules.md`。
- 完成 session 后，可把会话记录交给 `capture_preferences_from_session` 做增量捕获。

## 反馈闭环

当你基于用户偏好做出决策或回复后：

- 如果用户纠正了你的行为，例如“不是这个意思”“这次不用大纲”，调用 `report_preference_feedback`，类型为 `correction`。
- 如果用户确认了你的行为，例如“对，就是这样”“记住这个习惯”，调用 `report_preference_feedback`，类型为 `confirmation`。
- 如果用户明确拒绝某个偏好，调用 `report_preference_feedback`，类型为 `rejection`。
- 如果用户没有反馈，不需要主动打扰用户。

## Manifesto 面板

当用户要求查看、审查、调整或理解个人偏好时，调用 `open_preference_panel`，返回本地 `localhost` 链接。

执行规则：

- 面板是 `data/个人偏好.md` 的本地 HTML 渲染视图，不是新的官方偏好源。
- 默认只返回链接，不要擅自对外暴露或同步面板内容。
- 如果用户在面板或对话中确认、拒绝、纠正偏好，继续使用 `report_preference_feedback` 记录反馈。
- 如果面板不可用，直接告知用户偏好面板暂不可用，但仍可读取 Markdown 偏好源。
