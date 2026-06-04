<p align="center">
  <img src="assets/datailor-github-card.png" alt="Datailor - local-first personal preference memory for agents" width="920">
</p>

# Datailor Preference MCP

→ **[datailor.space](https://datailor.space/)**

Datailor is a local-first personal preference MCP for AI agents. It extracts stable, reusable user preferences from chat history and runtime hooks, stores them in the human-readable `personal-preferences.md`, and exposes them through MCP tools, a CLI, an AGENTS managed block, and a local Manifesto UI.

The V0 goal is not to remember everything. It validates a controlled workflow:

- The preference store can start empty without inventing user preferences.
- Only stable, reusable preferences that can guide future agent behavior are saved.
- One-off tasks, paths, URLs, issue IDs, secrets, generic fake scopes, and copied raw user fragments are filtered out.
- `personal-preferences.md` is the only canonical preference source; the Executive Summary and UI are derived views.
- MCP tools, CLI commands, AGENTS rules, and injection logs connect preferences to real agent workflows.

## Installation

For normal use, install from GitHub with `pipx`. After installation, `datailor` and `datailor-mcp` are available from an isolated command-line environment and do not depend on a local source checkout.

```powershell
python -m pip install --user pipx
python -m pipx ensurepath
pipx install git+https://github.com/fyaic/Datailor-preference-MCP.git

datailor onboard   # 👈 one command: guided interactive setup
```

`datailor onboard` launches an interactive wizard (model API, AGENTS.md rules,
fitting mode, agent integrations, history scan). Running `datailor` with no
command shows the same hint, so you always know what to run next.

Check the installation:

```powershell
datailor doctor --agent codex
```

Developer install:

```powershell
git clone https://github.com/fyaic/Datailor-preference-MCP.git datailor-preference-mcp
cd datailor-preference-mcp
python -m pip install -e .
```

Upgrade or uninstall:

```powershell
pipx upgrade datailor-preference-mcp
pipx uninstall datailor-preference-mcp
```

## Default Data Directory

Datailor writes runtime data to a user-level data directory by default, not to the repository `data\` directory. This lets pipx installs, editable installs, and MCP clients share the same preference store.

| System | Default directory |
| --- | --- |
| Windows | `%APPDATA%\Datailor` |
| macOS | `~/Library/Application Support/Datailor` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/datailor` |

Default canonical preference store:

```text
%APPDATA%\Datailor\personal-preferences.md
```

Override with environment variables:

```powershell
$env:DATAILOR_DATA_DIR = "D:\Datailor"
$env:PREFERENCE_STORE_PATH = "D:\Datailor\personal-preferences.md"
```

## First Run

### Guided setup (interactive)

Run `onboard` with no extra flags in a terminal to launch the interactive setup
wizard:

```powershell
datailor onboard
```

The wizard walks you through, OpenClaw-style, with arrow-key menus and a
polished terminal UI:

1. **Model backend** — local heuristic (zero config) or an OpenAI-compatible API
   (base URL / API key / model name, with an optional live connectivity check).
   Saved to `<data_dir>\datailor.env`.
2. **AGENTS.md rules** — preview and install the Datailor managed block.
3. **Fitting mode** — `curate` (review changes first) or `auto` (auto-apply
   high-confidence changes).
4. **Agent integrations** — register Datailor as an MCP server in detected
   Codex / Claude Code / Kimi clients.
5. **History scan** — run a full cold-start capture (or dry-run preview) with
   live progress.

`datailor` and `datailor-mcp` auto-load saved config from
`<data_dir>\datailor.env` and a project-local `.env.local` on startup (real
environment variables always take precedence). Force the mode with
`--interactive` / `--no-interactive`.

### Scripted / non-interactive

Inspect the current state:

```powershell
datailor doctor --agent codex
```

Cold-start scan local agent histories (any flags, `--json`, `--quiet`, or
`--no-interactive` keep the classic non-interactive flow):

```powershell
datailor onboard --agent codex --mode recall-extract
```

`onboard` creates the default `personal-preferences.md`, discovers Claude / Codex / Kimi histories, orders sources by recent activity and caller agent, runs incremental scanning, and installs a global `AGENTS.md` managed block so agents know when to call Datailor hooks.

When `--agent kimi` is used, `onboard` also updates Kimi CLI lifecycle hooks in `~/.kimi/config.toml`: it comments out a top-level empty `hooks = []` entry and writes a Datailor-managed `[[hooks]]` block for `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure`, `Stop`, and `SessionEnd`. Kimi can then call `datailor-kimi-hook` at lifecycle points. The AGENTS block remains a fallback constraint for clients that cannot inject hook output into the same model turn.

The `AGENTS.md` write is transparent. Datailor only writes a short managed block between these markers and does not overwrite user-authored rules:

```markdown
<!-- datailor-preference:start -->
# Datailor Personal Preference Agent
...
<!-- datailor-preference:end -->
```

That block instructs agents to call the corresponding MCP hook at session start, user-message arrival, turn completion, action execution, and session end; to record feedback through the feedback tool; and to open the UI Injection Log when the user asks whether preferences are active.

Manual repair or reinstall:

```powershell
datailor install-agent-rules
datailor install-kimi-hooks --agent kimi
```

Disable automatic writes:

```powershell
datailor onboard --agent codex --no-agent-rules
datailor onboard --agent kimi --no-kimi-hooks
```

Open the local Manifesto UI:

```powershell
datailor ui
```

`/preferences` is only an optional enhancement for MCP clients that support prompts. Codex CLI, Kimi CLI, and similar clients usually use their own command systems, so the reliable entry points are `datailor doctor`, `datailor onboard`, and `datailor ui`.

## MCP Configuration

Generate MCP configuration for the current agent:

```powershell
datailor mcp-config --agent codex
```

`mcp-config` installs the same `AGENTS.md` managed block by default. With `--agent kimi`, it also installs Kimi CLI hooks. To keep stdout directly pasteable into MCP config files, stdout contains only MCP JSON; AGENTS/Kimi target paths, actions, and markers are printed to stderr.

Example output:

```json
{
  "mcpServers": {
    "datailor-preferences": {
      "command": "datailor-mcp",
      "args": ["--agent", "codex"],
      "env": {
        "PREFERENCE_MODEL_BACKEND": "heuristic"
      }
    }
  }
}
```

If `--store` is provided, the generated config includes `PREFERENCE_STORE_PATH`:

```powershell
datailor mcp-config --agent kimi --store "D:\Datailor\personal-preferences.md"
```

Source development should still use `datailor-mcp`; do not bind MCP config to a repository `cwd` or to `python -m preference_agent.mcp_server`.

Print only MCP JSON without writing `AGENTS.md`:

```powershell
datailor mcp-config --agent codex --no-agent-rules
datailor mcp-config --agent kimi --no-agent-rules --no-kimi-hooks
```

Kimi hook commands observe lifecycle events and write Datailor injection logs / turn buffers. Current Kimi CLI shell hook responses only support allow/block decisions, so they cannot directly inject `agent_instruction` into the same model turn. The AGENTS managed block still tells the agent to read and apply preferences before replying.

## IDE / Client Integration

`mcp-config` only prints copyable MCP JSON. To write Datailor into Codex, Claude Code, or Kimi Code client config files, use the explicit `integrate` command:

```powershell
datailor integrate status --client all
datailor integrate install --client codex --dry-run
datailor integrate install --client codex
datailor integrate doctor --client all
datailor integrate remove --client codex
```

`integrate` writes transparently:

- `status` / `doctor` do not write files.
- `install --dry-run` shows the planned change without writing config or creating backups.
- `export-plugin --dry-run` reports the destination and whether an existing directory would be replaced, without creating directories or deleting existing output.
- `install` backs up existing config files before writing and remains idempotent.
- `remove` only removes the Datailor managed entry and leaves other MCP servers untouched.
- The default server name matches `mcp-config`: `datailor-preferences`.
- If `--scope` is omitted, each client uses its profile default: Codex/Kimi use `user`; Claude Code uses `project`.

Current client targets:

| Client | Default write target | Scope notes |
| --- | --- | --- |
| Codex | `[mcp_servers.datailor-preferences]` in `~/.codex/config.toml` | `--scope project` writes `.codex/config.toml` in the current project; Codex only loads project config for trusted projects. |
| Claude Code | `.mcp.json` in the current project | `user` / `local` scope writes `~/.claude.json`; project scope is easier to review. |
| Kimi Code | `mcpServers.datailor-preferences` in `~/.kimi/mcp.json` | Kimi CLI stores MCP config at user scope; project scope maps to user config and emits a warning. |

When `--store` is explicit, the written MCP environment includes `PREFERENCE_STORE_PATH`:

```powershell
datailor --store "D:\Datailor\personal-preferences.md" integrate install --client kimi
```

`onboard` does not modify every IDE/client config by default. To install a client during first run, pass it explicitly:

```powershell
datailor onboard --agent codex --integrate-client codex
datailor onboard --agent claude --integrate-client claude --integrate-scope project
```

`doctor`, `onboard`, and `cold-start-scan` next steps point users to `datailor integrate status --client all` and `datailor integrate install --client all --dry-run` so IDE/client integration is discoverable.

Plugin templates can be exported to a local directory and then installed or inspected through each client's official workflow:

```powershell
datailor integrate export-plugin --client codex --output .\datailor-plugins
datailor integrate export-plugin --client claude --output .\datailor-plugins
datailor integrate export-plugin --client kimi --output .\datailor-plugins
```

Exported templates stay short: Datailor MCP config, common CLI entry points, and preference hook rules. Codex and Claude Code templates include `.mcp.json` and a skill. The Kimi template includes `plugin.json`, `SKILL.md`, and a small command wrapper. The wrapper command uses the current Python interpreter and an absolute script path, then calls Datailor through `python -m preference_agent.cli`; it does not depend on Kimi's working directory or on `datailor` being on PATH. Actual plugin loading behavior still depends on each client's current official plugin flow.

## Cold-Start Scan

Run a standalone cold-start scan:

```powershell
datailor cold-start-scan --agent codex --mode recall-extract
```

The default output is human-readable and streams discovered sources, the current source being scanned, candidate counts, added/merged/conflict counts, and skipped reasons. Use JSON for automation:

```powershell
datailor cold-start-scan --agent codex --mode recall-extract --json
```

Show only the final state:

```powershell
datailor cold-start-scan --agent codex --quiet
```

Useful flags:

- `--dry-run`: preview only; do not write to the preference store.
- `--max-files`: limit file count for debugging or emergency runs; default is unlimited.
- `--max-minutes`: limit runtime per source; default is unlimited.

## Fitting

`fitting` performs offline consolidation. It reads the current preference store and optional history sources, extracts long-term patterns from natural-language instructions, generates memory-rot suggestions, and writes a Fitting Report. The default mode is `curate`: generate a review plan without directly overwriting `personal-preferences.md`.

```powershell
datailor fitting --agent codex --instructions "focus on UI writing preferences; ignore one-off install commands"
```

Scan a specific history source:

```powershell
datailor fitting --source "C:\path\to\history.jsonl" --instructions-file ".\fitting-instructions.txt"
```

Inspect and apply:

```powershell
datailor fitting-list
datailor fitting-show fitting-20260527-173000 --report
datailor fitting-apply fitting-20260527-173000 --accept chg-001
```

Auto mode only applies low-risk, high-confidence `preference` changes. Workflow, error pattern, memory-rot, and other complex changes stay in the report and plan.

```powershell
datailor fitting --auto-apply --agent codex
```

Fitting writes local artifacts:

- `report.md`: human review report.
- `result.json`: structured result for CLI/MCP/UI.
- `draft-insights.jsonl`: candidate `preference`, `workflow`, `error_pattern`, `tool_quirk`, and related patterns.
- `rot-suggestions.jsonl`: duplicate, overlap, conflict, stale, and negative-feedback suggestions.
- `apply-plan.json`: explicitly acceptable change plan.

Fitting is integrated with the Mode system:

| Mode | Behavior |
| --- | --- |
| `curate` | Default review mode. Hooks can trigger Fitting after thresholds are met, but only a `pending_review` plan is generated; the user must accept changes through UI, CLI, or MCP. |
| `auto` | Automatic mode. Hooks can trigger Fitting after thresholds are met and automatically apply high-confidence, low-risk preference changes. |

Trigger conditions are controlled by environment variables:

| Variable | Default | Description |
| --- | ---: | --- |
| `PREFERENCE_FITTING_AUTO` | `1` | Master switch for automatic Fitting triggers |
| `PREFERENCE_FITTING_TRIGGER_RECORDS` | `5` | New/changed preference record threshold |
| `PREFERENCE_FITTING_TRIGGER_DAYS` | `7` | Days since last Fitting threshold |
| `PREFERENCE_FITTING_COOLDOWN_MINUTES` | `60` | Automatic trigger cooldown |
| `PREFERENCE_FITTING_MAX_FILES` | `0` | Max auto-discovered history files; `0` means unlimited |
| `PREFERENCE_FITTING_MAX_MINUTES` | `0` | Reserved runtime limit; `0` means unlimited |

MCP also exposes `start_fitting`, `get_fitting_status`, and `apply_fitting_plan`. `start_fitting` supports `mode: "auto" | "curate"` and `auto_apply: true`. Note that `mode: "auto"` itself means auto-apply high-confidence, low-risk preferences even when `auto_apply: false` is also passed; use `mode: "curate"` or omit `mode` to generate only a review plan.

## Real Capture

Production capture should not use `recall-only` to write the store. `recall-only` is for offline tests and recall debugging. Real capture should use `recall-extract` or `semantic-extract`.

```powershell
$env:PREFERENCE_CAPTURE_MODE = "recall-extract"
$env:PREFERENCE_CAPTURE_AGENT_RULES = "0"
datailor capture-job --source "C:\path\to\history.jsonl" --max-minutes 10
```

Long-running captures can be repeated safely. The runner uses `.capture-state\*.checkpoint.json` under the user data directory and only advances checkpoints after candidates are refined and written, so interrupted runs do not silently skip data.

Kimi Code `user-history` JSONL often contains only a `content` field and no `role` field. Datailor treats content-only JSONL as user input.

## Preference Store Capacity

The canonical `personal-preferences.md` store is capped at 50 counted preferences by default. Override the limit with `PREFERENCE_STORE_MAX_RECORDS` when testing or operating a different policy:

```powershell
$env:PREFERENCE_STORE_MAX_RECORDS = "50"
```

The cap is not a blind truncation step. Before writing the store, Datailor first merges exact or near-exact duplicates and preserves evidence on the surviving record. If the store still exceeds the cap, lower-value records are kept out of the canonical active set and written to a local `*.cap-review.jsonl` artifact for review. Capture results, Fitting apply results, and the Manifesto API expose cap summaries such as `limit`, `before_count`, `after_count`, `review_required`, and review-candidate counts.

Fitting also treats cap pressure as memory rot. It proposes review-first consolidation actions so the long-term store stays compact through merge, archive, or generalized preference rewrites instead of accumulating low-value one-off rules.

## Model Configuration

The default backend is `heuristic` and does not require an API. Use OpenAI-compatible settings for cloud or local models:

```powershell
$env:PREFERENCE_MODEL_BACKEND = "openai-compatible"
$env:PREFERENCE_MODEL_BASE_URL = "http://localhost:11434/v1"
$env:PREFERENCE_MODEL_API_KEY = "local"
$env:PREFERENCE_MODEL_NAME = "qwen2.5:7b"
```

Cloud model example:

```powershell
$env:PREFERENCE_MODEL_BACKEND = "openai-compatible"
$env:PREFERENCE_MODEL_BASE_URL = "https://api.moonshot.ai/v1"
$env:PREFERENCE_MODEL_API_KEY = "replace-me"
$env:PREFERENCE_MODEL_NAME = "Kimi-K2.5"
```

Optional embeddings for semantic recall:

```powershell
$env:PREFERENCE_EMBEDDING_BACKEND = "glm"
$env:PREFERENCE_EMBEDDING_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
$env:PREFERENCE_EMBEDDING_API_KEY = "replace-me"
$env:PREFERENCE_EMBEDDING_MODEL = "embedding-3"
```

Do not commit `.env.local` or any API key.

## Local UI

```powershell
datailor ui --no-open
```

Default address:

```text
http://127.0.0.1:8080
```

The UI binds to localhost only. It reads the canonical `personal-preferences.md` and displays:

- **Dashboard**: active / pending / conflict / feedback counts and Executive Summary
- **Manifesto**: full preference list
- **Injection Log**: preference injection timeline, matched preferences, generated `agent_instruction`, and injection status
- **Conflict A/B**: side-by-side conflict comparison
- **Feedback**: Confirm / Reject / Correct
- **Fitting**: latest Fitting Report, pending change counts, Accept Selected / Accept All / Reject All in `curate` mode
- **Settings**: switch between `curate` and `auto`

All `decide` / hook / prewarm calls write `.injection-log.jsonl`. The Injection Log tab parses that file into a readable timeline so users can verify that preferences entered the agent workflow.

UI feedback modifies the canonical preference store:

- Confirm: promote a pending preference to `active`.
- Reject: remove the preference from `personal-preferences.md`.
- Correct: rewrite the preference text from user input and mark it active.

Before overwriting the Markdown store, Datailor creates a snapshot. After changes, it refreshes the Executive Summary. Feedback JSONL is only an audit and evolution log.

## Injection And Observability

MCP exposes preference decisions, cold-start capture, feedback, hooks, conflict resolution, and UI launching tools.

| Tool | Description |
| --- | --- |
| `get_onboarding_status` | Check first-run setup status |
| `get_preference_decision` | Read user preferences before an agent acts |
| `start_cold_start_capture` | Start cold-start capture, equivalent to CLI `onboard` capture flow |
| `capture_preferences_from_session` | Capture preferences from a specific session file |
| `discover_agents` | Detect installed local agent history sources |
| `prewarm_preferences` | Prewarm session-level preference cache |
| `hook_session_start` | H1 session-start hook |
| `hook_user_message` | H2 user-message hook |
| `hook_turn_complete` | H3 turn-complete hook |
| `hook_action_executed` | H4 behavior-signal hook |
| `hook_session_end` | H5 session-end hook |
| `sync_preference_injection` | Generate static rules and sync them to AGENTS.md |
| `report_preference_feedback` | Record user feedback: confirmation, correction, rejection, or usage |
| `resolve_preference_conflict` | Mark a conflict resolved and update preference state |
| `open_preference_panel` | Open the local Manifesto UI |
| `start_fitting` | Start a Fitting job in `curate` or `auto` mode |
| `get_fitting_status` | Read latest or specified Fitting job status |
| `apply_fitting_plan` | Apply explicitly accepted Fitting changes |
| `toggle_preferences` | Enable or disable preference injection (`{"enabled": false}`) |

All `decide` / hook / prewarm calls write injection logs. The Manifesto UI Injection Log shows time, agent, session, matched preferences, and the actual injected `agent_instruction`.

Runtime MCP hooks return a compact visible payload by default: the decision, complete `agent_instruction`, match count, short matched preference list, and small summaries for capture/sync work. Internal metadata such as confidence factors, prewarm internals, capture details, and auto-discovery file lists remains available by calling the same tool with `include_debug=true`. Datailor does not truncate the visible `agent_instruction`; if an instruction is shown, it is the full executable instruction for that turn.

Static injection artifacts can be generated manually:

```powershell
datailor sync-injection
datailor prewarm --agent codex --task "prepare a final reply after code implementation"
```

These artifacts are generated views, not a new preference source. The only canonical source remains `personal-preferences.md`.

## Enabling And Disabling Datailor

The MCP protocol has no native enable/disable switch for a server, so Datailor
implements the toggle internally at the decision layer rather than by editing
your client's MCP config. Turning it off does not remove any integration; it
simply makes the injection tools return empty instructions.

```powershell
datailor disable   # AI stops receiving preference instructions
datailor enable    # resume injecting preferences
datailor status    # show whether Datailor is currently enabled
```

Agents that can call tools directly may toggle it through MCP:

```json
{ "name": "toggle_preferences", "arguments": { "enabled": false } }
```

How it behaves:

- The switch is stored as `DATAILOR_ENABLED` in `<data_dir>/datailor.env` and is
  re-read on every call, so a CLI toggle takes effect immediately even for a
  long-running `datailor-mcp` process — no restart needed.
- When disabled, injection tools (`get_preference_decision`, `hook_user_message`,
  `hook_session_start`, `prewarm_preferences`) return an empty `agent_instruction`
  with `decision="disabled"`. Every response also carries `"enabled": false` so
  the agent knows Datailor is off.
- Capture is unaffected: turn-complete and behavior-signal hooks keep recording
  new preferences in the background. Disabling only stops injection, not learning.

## Conflict Handling

Before returning an agent instruction, `decide` checks whether matched active preferences conflict. If they cannot all apply at once, Datailor returns `decision=escalate` instead of concatenating contradictory rules. The agent should briefly ask the user which preference applies for this turn, or whether neither applies.

Datailor records asked conflict combinations and uses a cooldown to avoid repeatedly asking about the same conflict. After the user answers, the agent calls `resolve_preference_conflict` to update preference state and mark the conflict handled.

## Versioning And Recovery

```powershell
datailor snapshots
datailor restore-snapshot --snapshot "C:\path\to\.snapshots\20260525-120000-000000-pre-save.md"
```

`personal-preferences.md` is the only canonical preference source. Automatic snapshots are only for recovery. By default, every overwrite of an existing Markdown store saves the previous version under `.snapshots\` next to the store.

## Data And Privacy

The repository ignores runtime data and local config by default:

- `.env.local`
- `data\`
- `.capture-state\`
- `.debug-capture\`
- `.feedback\`
- `.hooks\`
- `.injection\`
- `.snapshots\`
- `.summary\`
- `.ui\`

Real preferences, chat history, debug output, and API keys should not be committed. Code paths redact common secret shapes and replacement characters before writing.

## Tests

```powershell
python -m pytest -q
```

Current regression: `112 passed`.

## Author

Datailor is built and maintained by Rosetta Zidian Guo and her team.

The original idea was contributed by Dr. Ren Diao.
