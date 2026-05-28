# Initial And Incremental Capture Design

## Cold Start

On a new installation, `personal-preferences.md` in the user data directory contains only structure and no real preferences. Default locations are Windows `%APPDATA%\Datailor\personal-preferences.md`, macOS `~/Library/Application Support/Datailor/personal-preferences.md`, and Linux `${XDG_DATA_HOME:-~/.local/share}/datailor/personal-preferences.md`.

In this state:

- `decide` returns `decision: no_preference`.
- Agents should continue normal workflow or ask the user when needed.
- After a session ends, the user can pass chat records to `capture`; the system generates the first preferences from real answers.

## Initial Capture

Input can be one file or a directory:

- Kimi exported Markdown / JSON / TXT.
- Local Codex or OpenClaw session records.
- Manually curated session fragments.

Pipeline:

```text
read files
  -> normalize into role/content messages
  -> model extracts preference candidates
  -> merge with empty store
  -> write human-readable Markdown preferences
```

Extraction standards:

- Extract only stable, reusable agent-behavior preferences.
- Every preference must have an evidence quote.
- Do not treat one-off task facts as preferences.
- Do not infer preferences without user expression support.

## Incremental Capture

Incremental capture is not append-only. It performs four merge actions:

| Action | Condition | Result |
| --- | --- | --- |
| new | No same-intent preference exists | Add a new preference |
| merge | Same intent and consistent | Append evidence and strengthen confidence |
| replace | User explicitly said "from now on", "change to", or similar | Update the primary preference |
| conflict | Same intent but uncertain change | Mark `needs_review` and keep conflict evidence |

## Kimi Raw Data Capture Recommendation

Prefer exported files or locally readable history over screen scraping:

1. Locate Kimi history export directories or manually export a high-value set of conversations.
2. Keep one session per file, with date and topic in the filename.
3. Sample 20 sessions for quality validation first.
4. Use `capture --dry-run` to inspect candidate counts before writing.
5. For false positives, adjust model prompts or add negative examples; do not patch rules manually first.

## Quality Sampling

After each capture batch, sample-check:

- Whether the preference truly comes from user expression.
- Whether scope, trigger intent, and exceptions are clear.
- Whether temporary tasks were incorrectly classified as long-term preferences.
- Whether Markdown structure is complete.
