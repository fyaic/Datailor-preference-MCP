# Datailor Personal Preference Agent

Datailor 是本地优先的个人偏好 MCP。你在回复用户、执行任务、写入外部系统、做确认决策之前，应优先读取 Datailor 返回的偏好决策；不要伪造用户偏好。

## Session 启动

Session 开始时调用 `hook_session_start`，传入当前 agent、session_id、任务摘要和上下文。它会执行偏好决策、session prewarm，并写入注入观测日志。

如果偏好库为空，Datailor 可能在首次工具调用时自动发现 Codex / Claude / Kimi 历史并执行冷启动扫描。不要要求用户手动填写 `source_path`，除非用户明确指定导入某个文件。

## 每轮回复前

每条用户消息到达时调用 `hook_user_message`。如果该工具不可用，再调用 `get_preference_decision`。

执行规则：
- 返回 `decision: apply` 时，把 `agent_instruction` 作为本轮硬约束。
- 返回 `decision: escalate` 且包含冲突时，只向用户简短询问本次采用哪条偏好，用户回答后调用 `resolve_preference_conflict`。
- 返回 `no_preference` 时正常继续，不要编造偏好。
- 高风险或不确定事项仍按用户确认流程处理。

## Turn 与 Action 捕获

每个 turn 完成后调用 `hook_turn_complete`，传入 `user_message` 和 `assistant_response`。该 hook 会按信号条件缓冲并批量补录偏好，不要自己逐条总结偏好。

执行测试、格式化、提交、外部写入等动作后，如果有 action metadata，调用 `hook_action_executed`。

Session 结束时调用 `hook_session_end`，让 Datailor flush turn buffer，并同步注入产物。

## 反馈闭环

用户纠正、确认或拒绝某条偏好时，调用 `report_preference_feedback`：
- correction：用户纠正了偏好或本次应用方式。
- confirmation：用户确认偏好正确。
- rejection：用户明确拒绝某条偏好。

该工具会在可定位时真实更新 `个人偏好.md`，不只是写日志。

## Manifesto 面板

当用户要求查看、审查、调整或理解个人偏好时，调用 `open_preference_panel`，返回本地 `localhost` 链接。不要在聊天中展开用户的完整偏好内容。

`/preferences` 只是支持 MCP prompts 的客户端上的可选增强。Codex CLI、Kimi CLI 等客户端通常只识别自己的命令系统，因此通用入口是 `datailor doctor`、`datailor onboard` 和 `datailor ui`。

## 可观测性

所有 decide / hook / prewarm 调用都会写入注入日志。用户询问“偏好有没有生效”时，打开 Manifesto UI 的 Injection Log，让用户看到命中的偏好、注入时间和实际 `agent_instruction`。
