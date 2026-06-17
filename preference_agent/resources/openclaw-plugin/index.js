import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { runCommandWithTimeout } from "openclaw/plugin-sdk/process-runtime";

const HOOK_COMMAND = process.env.DATAILOR_OPENCLAW_HOOK || "datailor-openclaw-hook";

function runDatailor(event, payload, context, timeoutMs = 3000) {
  return runCommandWithTimeout(
    [HOOK_COMMAND, "--agent", "openclaw", "--event", event],
    {
      timeoutMs,
      input: JSON.stringify({ payload: payload || {}, context: context || {} })
    }
  )
    .then((result) => {
      if (result.code !== 0 || result.termination !== "exit") {
        return { ok: true, failOpen: true, event };
      }
      try {
        return result.stdout.trim() ? JSON.parse(result.stdout) : { ok: true, event };
      } catch {
        return { ok: true, failOpen: true, event };
      }
    })
    .catch(() => ({ ok: true, failOpen: true, event }));
}

export default definePluginEntry({
  id: "datailor-preferences",
  name: "Datailor Preferences",
  description: "Datailor preference injection and capture hooks for OpenClaw.",
  register(api) {
    api.on("session_start", (payload, context) => runDatailor("session_start", payload, context, 3000));
    api.on("before_prompt_build", (payload, context) => runDatailor("before_prompt_build", payload, context, 1500));
    api.on("agent_turn_prepare", (payload, context) => runDatailor("agent_turn_prepare", payload, context, 1500));
    api.on("after_tool_call", (payload, context) => runDatailor("after_tool_call", payload, context, 3000));
    api.on("agent_end", (payload, context) => runDatailor("agent_end", payload, context, 3000));
    api.on("session_end", (payload, context) => runDatailor("session_end", payload, context, 5000));
  }
});
