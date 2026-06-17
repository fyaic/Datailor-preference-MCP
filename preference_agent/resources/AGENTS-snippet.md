# Datailor Personal Preference Agent

Datailor is a local-first personal preference MCP. Before replying, executing tasks, writing to external systems, or making confirmation decisions, read the preference decision returned by Datailor; do not fabricate user preferences.

## Session Start

At session start, call `hook_session_start` with the current agent, `session_id`, task summary, and context. It runs preference decisioning, session prewarm, and injection observability logging.

If the preference store is empty, Datailor may automatically discover Codex / Claude / Kimi / OpenClaw history and run a cold-start scan on the first tool call. Do not ask the user to manually provide `source_path` unless they explicitly specify a file to import.

## Before Each Reply

For every user message, call `hook_user_message`. If that tool is unavailable, call `get_preference_decision`.

Rules:
- When `decision: apply` is returned, treat `agent_instruction` as a hard instruction for this turn.
- When `decision: escalate` includes a conflict, briefly ask the user which preference applies for this turn, then call `resolve_preference_conflict` after the user answers.
- When `no_preference` is returned, continue normally and do not invent preferences.
- High-risk or uncertain actions still follow normal user-confirmation rules.

## Turn And Action Capture

After each turn, call `hook_turn_complete` with `user_message` and `assistant_response`. The hook buffers qualifying signals and batch-extracts preferences; do not summarize preferences manually one by one.

After actions such as tests, formatting, commits, or external writes, call `hook_action_executed` when action metadata is available.

At session end, call `hook_session_end` so Datailor can flush the turn buffer and sync injection artifacts.

## Feedback Loop

When the user corrects, confirms, or rejects a preference, call `report_preference_feedback`:
- `correction`: the user corrected a preference or its application.
- `confirmation`: the user confirmed a preference is correct.
- `rejection`: the user explicitly rejected a preference.

When possible, this tool updates `personal-preferences.md` directly; it is not just a log write.

## Manifesto Panel

When the user asks to view, review, adjust, or understand personal preferences, call `open_preference_panel` and return the local `localhost` link. Do not expand the user's full preference content in chat.

`/preferences` is only an optional enhancement for MCP clients that support prompts. Codex CLI, Kimi CLI, and similar clients usually recognize their own command systems, so the reliable entry points are `datailor doctor`, `datailor onboard`, and `datailor ui`.

## Observability

All decide / hook / prewarm calls write injection logs. When the user asks whether preferences are active, open the Manifesto UI Injection Log so they can inspect matched preferences, injection time, and the actual `agent_instruction`.
