# Datailor OpenClaw Plugin

This template registers OpenClaw typed hooks and forwards compact event payloads to `datailor-openclaw-hook`.

Expected flow:

1. Install the Datailor Python package so `datailor-openclaw-hook` is on PATH.
2. Export this template with `datailor integrate export-plugin --client openclaw --output <dir>`.
3. Install or link the exported plugin through the OpenClaw plugin workflow, for example `openclaw plugins install <dir>/openclaw-datailor --link`.

The bridge fails open: if Datailor is unavailable or times out, OpenClaw continues normally.
