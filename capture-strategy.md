# Large Local Session Capture Strategy

## Background

The default cold-start preference store is empty. A new installation should not guess preferences. It should capture them from two local evidence sources:

1. Agent rule files the user has already written.
2. User-authored messages in historical session JSONL files.

These sources have different priority. Agent rules are explicit preferences. Historical user messages are behavioral feedback and implicit preferences. The first capture should read rules first, then history.

## Overall Strategy

```text
start capture runner
  -> read environment config
  -> discover agent rule files
  -> extract explicit preferences and hard constraints
  -> write preference candidates
  -> stream JSONL files
  -> read only user/human input
  -> recall fragments that may contain preferences
  -> batch model refinement into reusable preferences
  -> filter one-off tasks, paths, URLs, issue IDs, and generic fake scopes
  -> merge, dedupe, and mark conflicts
  -> maintain one canonical Markdown preference store
```

## Execution Roles

Do not make an agent read huge JSONL files inside one conversation. Do not put the whole capture job into one chat turn.

| Role | Responsibility |
| --- | --- |
| Human / agent | Start the job, inspect final Markdown, inspect debug output if needed |
| Capture runner | Scan files, recall user input, maintain `personal-preferences.md` and checkpoints |
| Model backend | Required for production cold start; semantically refines candidate fragments |
| Markdown store | Only product output, with human-readable preferences |

`recall-only` is only for offline tests and recall incident debugging. Production cold start should use `recall-extract` or `semantic-extract`; otherwise one-off tasks and raw user text may be written as preferences.

## Environment Design

Minimum production config:

```powershell
$env:PREFERENCE_PROJECT_ROOT = "."
$env:PREFERENCE_CAPTURE_DEBUG = "0"
$env:PREFERENCE_CAPTURE_MODE = "recall-extract"
$env:PREFERENCE_CAPTURE_BATCH_SIZE = "24"
$env:PREFERENCE_CAPTURE_MAX_MINUTES = "0"
$env:PREFERENCE_MODEL_TIMEOUT = "120"
$env:PREFERENCE_MODEL_BACKEND = "openai-compatible"
```

`PREFERENCE_STORE_PATH` and `PREFERENCE_CHECKPOINT_DIR` usually do not need to be set. Datailor writes to the user-level data directory, such as `%APPDATA%\Datailor` on Windows. Set `DATAILOR_DATA_DIR` or `PREFERENCE_STORE_PATH` only when data must live somewhere else.

Cloud or local model backend:

```powershell
$env:PREFERENCE_MODEL_BACKEND = "openai-compatible"
$env:PREFERENCE_MODEL_BASE_URL = "https://api.example.com/v1"
$env:PREFERENCE_MODEL_API_KEY = "replace-me"
$env:PREFERENCE_MODEL_NAME = "replace-me"
```

Future local model example:

```powershell
$env:PREFERENCE_MODEL_BASE_URL = "http://localhost:11434/v1"
$env:PREFERENCE_MODEL_API_KEY = "local"
$env:PREFERENCE_MODEL_NAME = "qwen2.5:7b"
```

## Step 1: Read Existing Agent Rules First

### Goal

Read rules the user has already written and use them as the highest-confidence initial preference source.

### Candidate Paths

The capture runner should discover multiple rule locations instead of hard-coding `agents/claude.md`.

1. Current project root:
   - `AGENTS.md`
   - `agents.md`
   - `CLAUDE.md`
   - `claude.md`
2. Current project `agents/` directory:
   - `agents/claude.md`
   - `agents/codex.md`
   - `agents/openclaw.md`
   - `agents/*.md`
   - `agents/*.yaml`
   - `agents/*.yml`
3. Repository subprojects:
   - `<subproject>/AGENTS.md`
   - `<subproject>/agents/*.md`
   - `<subproject>/agents/*.yaml`

### Rule Extraction

Rule file content falls into three categories:

| Type | Handling |
| --- | --- |
| Hard constraint | Direct high-confidence preference candidate |
| Work habit | Medium/high-confidence preference candidate |
| Project-local rule | Mark with project path scope to avoid global pollution |

Example:

```json
{
  "source_type": "agent_rule",
  "source": "C:\\path\\to\\project\\AGENTS.md",
  "scope": "project:project-name",
  "preference": "After a phase is completed, ask whether the Linear issue should be updated.",
  "confidence": "high"
}
```

## Step 2: Read Only User Input From JSONL

For large local JSONL files, do not send both user input and model output to the model by default. First capture should read only user input because:

- Token cost drops sharply.
- User input directly contains requests, feedback, corrections, and preference changes.
- Model output is mostly execution and explanation noise.
- The preference system should learn how the user asks agents to behave, not how agents answer.

Keep messages with role values such as:

- `user`
- `human`
- other sender/author values that can be mapped to a user

Ignore:

- `assistant`
- `model`
- `ai`
- `tool`
- `system`

### Lightweight Context Exceptions

Sometimes a user message needs the previous assistant question or summary. Keep only lightweight context, never the full model output.

| Scenario | Context to keep |
| --- | --- |
| User says "use the plan you just gave" | Summary of previous assistant output |
| User says "that is wrong" | 200-500 character truncated previous assistant output |
| User answers "yes/no/okay" | Previous assistant question |

Local preprocessing should produce this as `context_hint`.

## Large JSONL Pipeline

### Phase 1: Profile

Sample the first 1000 lines to detect:

- JSONL row structure
- role field path
- content field path
- timestamp / session_id / conversation_id fields
- nested message arrays

Example output:

```json
{
  "format": "jsonl",
  "role_path": "message.role",
  "content_path": "message.content",
  "session_id_path": "conversation_id",
  "timestamp_path": "created_at"
}
```

### Phase 2: Stream

Read files line by line:

- Do not load the full JSONL file into memory.
- Write checkpoints every N lines.
- Log malformed rows and continue.
- By default, there is no global time limit; traversal continues until completion.
- `PREFERENCE_CAPTURE_MAX_MINUTES` is only a debug or emergency stop switch. Default `0` disables it.

Checkpoint example:

```json
{
  "file": "kimi-history.jsonl",
  "line": 120000,
  "last_session_id": "abc",
  "updated_at": "2026-05-23T12:00:00+08:00"
}
```

### Phase 3: User-Only Recall

Use low-cost local rules to recall candidate fragments from user input before calling any model.

Recall signals:

- Explicit preferences: "from now on", "default", "every time", "always", "I prefer", "I want"
- Constraints: "do not", "avoid", "must", "cannot"
- Corrections: "wrong", "not what I meant", "you should", "next time"
- Workflow: "test", "verify", "review", "read back", "Linear", "document"
- Preference changes: "change to", "from now on", "default to"

Recall output is candidate fragments, not final preferences.

### Phase 3.5: Multi-Route Recall

`PREFERENCE_RECALL_STRATEGY=multi` adds three routes on top of keyword recall:

| Route | Purpose | Default output |
| --- | --- | --- |
| Semantic recall | Compare user input with preference examples through GLM `embedding-3` or hash fallback | In-memory candidates; persisted only in debug mode |
| Expanded keyword recall | Cover positive, negative, conditional, comparative, and instruction-like expressions | In-memory candidates; persisted only in debug mode |
| Behavior pattern recall | Count high-frequency templates across sessions, such as "outline first", "test", "verify", or "read back" | In-memory candidates; persisted only in debug mode |
| Conversation-structure recall | Detect correction and emphasis signals such as "wrong", "you misunderstood", and "I said before" | In-memory candidates; persisted only in debug mode |

The POC uses an in-memory vector index. Chroma/FAISS should be introduced only after real data volume validates the need.

GLM embedding config:

```powershell
$env:PREFERENCE_RECALL_STRATEGY = "multi"
$env:PREFERENCE_EMBEDDING_BACKEND = "glm"
$env:PREFERENCE_EMBEDDING_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
$env:PREFERENCE_EMBEDDING_MODEL = "embedding-3"
$env:PREFERENCE_EMBEDDING_DIMENSIONS = "1024"
```

Store API keys only in `.env.local`; never write them to docs or logs.

### Phase 4: Batch Extract

Batch candidate fragments by topic and time:

- 20-100 user inputs per batch.
- Keep neighboring user feedback from the same session in the same batch when possible.
- Ask the model to output only structured preference candidates.
- The model must include evidence quotes.

### Phase 5: Markdown Store

Large capture has one default product output:

```text
<user-data>/
  personal-preferences.md
  .capture-state/
    kimi-history.checkpoint.json
```

`personal-preferences.md` keeps only human-readable bullets:

```md
# Personal Preferences

## Active Preferences

- When code is changed, run relevant tests by default; if tests cannot run, explain why and provide alternate verification.
- When a discussion becomes architectural, decision-heavy, or complex, ask whether it should be documented in Markdown.

## Observed Preferences

No observed preferences yet.
```

Metadata, candidate IDs, source quotes, and confidence do not enter the default store. This preserves readability and avoids unnecessary future token cost.

### Phase 5.5: Loops, Timeouts, And Recovery

Long tasks must be interruptible, resumable, and repeatable:

- Write checkpoints per batch.
- Give every candidate a stable `candidate_id`.
- Keep the outer loop running until file traversal is complete; do not rely on an agent to manage it manually.
- API failures affect only the current batch. Retry via `PREFERENCE_CAPTURE_MAX_RETRIES`; after repeated failure, log and skip the batch.
- Do not write multiple duplicate candidate/extracted/summary files by default.
- With `PREFERENCE_CAPTURE_DEBUG=1`, failed batches are written to `<user-data>\.debug-capture\*-failures.jsonl` with error, candidate ID, and source.
- `PREFERENCE_MODEL_TIMEOUT` limits each model request to prevent one stuck request from blocking the job.
- If `PREFERENCE_CAPTURE_MAX_MINUTES` or `--max-minutes` is set, write a checkpoint and exit cleanly when time is reached.
- The next run resumes from the checkpoint line.

`recall-extract` calls the model backend for recalled candidate fragments and merges structured results into `personal-preferences.md`. It never sends full JSONL to the model.

`semantic-extract` means multi-route recall plus model refinement. It still maintains only one canonical Markdown store; candidate, extracted, failure, and summary outputs are written only when `PREFERENCE_CAPTURE_DEBUG=1`.

Command example:

```powershell
python -m preference_agent.cli capture-job `
  --source "C:\path\kimi-history.jsonl" `
  --project-root "." `
  --mode recall-extract
```

### Phase 6: Merge

Before entering `personal-preferences.md`, candidates are merged:

| Action | Condition |
| --- | --- |
| new | No same-intent preference exists |
| merge | Same intent and consistent; append evidence |
| replace | User clearly changed the preference |
| conflict | Same intent but opposite direction; needs observation |
| reject | One-off task, factual statement, or no reusable value |

## Why Read Rules Before User Input

Agent rule files are explicit, user-curated preferences with high confidence, but incomplete coverage.

JSONL user input has broad coverage but high noise. It is best for:

- Repeated user corrections.
- Frequently requested workflows.
- Standards the user changed later.
- Habits that were never written into rule files.

The intended sequence is:

```text
rule files establish initial preference anchors
  -> JSONL user input adds evidence
  -> new evidence strengthens, updates, or marks conflicts
```

## Token Control

1. Do not read full model output.
2. Do not feed the full JSONL file to the model; feed only recalled candidate fragments.
3. Recall locally first, then refine with the model.
4. Process only candidate user inputs in each batch.
5. Keep evidence quotes, but only a small number of high-quality quotes per preference.
6. Maintain one Markdown store by default; debug output must be explicitly enabled.

## Acceptance Criteria

- Discover and read existing agent rule files.
- Stream large JSONL files without loading them fully into memory.
- Read only user input by default.
- Preserve the previous assistant question or summary as lightweight context when needed.
- Maintain one human-readable Markdown preference store directly.
- Resume from checkpoints.
- Avoid duplicate semantic output files by default; debug mode is the only path that writes troubleshooting material.
