# OpenClaw Hook Bridge

OpenClaw support is implemented as an extension of the existing Datailor package. It is not a separate Datailor fork.

## Installed Pieces

- `openclaw` client/profile in the integration registry.
- OpenClaw history discovery for `~/.openclaw/agents/*/sessions/*.jsonl`.
- OpenClaw JSONL parsing for `type=message` records with `message.role=user`.
- `datailor-openclaw-hook`, a Python bridge that maps OpenClaw typed hook events to Datailor hook semantics.
- `preference_agent/resources/openclaw-plugin/`, a plugin template that forwards typed hooks to the Python bridge.

## MCP Install

Preview the command:

```powershell
datailor integrate install --client openclaw --dry-run
```

Install through the OpenClaw CLI and write the Datailor hook plugin template:

```powershell
datailor integrate install --client openclaw
```

Datailor calls `openclaw mcp set datailor-preferences <json>`. It does not edit private OpenClaw config files directly.
It also writes the bundled plugin template to `~/.openclaw/plugins/datailor-preferences`, installs it through OpenClaw's plugin registry, and enables the fixed `datailor-preferences` plugin so typed hooks can call `datailor-openclaw-hook`.

Remove the OpenClaw integration:

```powershell
datailor integrate remove --client openclaw
```

Remove unsets the Datailor MCP server and disables the `datailor-preferences` OpenClaw plugin. The plugin directory may remain on disk for future reinstall, but the plugin is disabled and its typed hooks are no longer mounted.

## Plugin Install

Export the plugin template:

```powershell
datailor integrate export-plugin --client openclaw --output .\datailor-plugins
```

Manual export is useful for review or custom plugin installation. The regular `integrate install --client openclaw` path writes the same template to the default OpenClaw plugin directory.

The plugin forwards these events:

| OpenClaw hook | Datailor action |
| --- | --- |
| `session_start` | `hook_session_start` semantics |
| `before_prompt_build` | `hook_user_message`, returning `prependSystemContext` when a preference applies |
| `agent_turn_prepare` | optional prompt context append |
| `after_tool_call` | `hook_action_executed` semantics |
| `agent_end` | `hook_turn_complete` semantics |
| `session_end` | `hook_session_end` semantics |

The bridge fails open. Timeouts, missing Datailor commands, and malformed output return a compact non-blocking payload so OpenClaw can continue.

## Verification

```powershell
python -m preference_agent.cli mcp-config --agent openclaw --no-agent-rules
python -m pytest tests/test_openclaw_hooks.py tests/test_integrations_core.py
openclaw mcp list
openclaw hooks list --eligible --json
```

Do not paste raw OpenClaw config output into issues, docs, or logs. Record only whether the command succeeded and the relevant exit code.
