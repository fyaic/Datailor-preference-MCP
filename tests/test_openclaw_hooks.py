from __future__ import annotations

import unittest

from preference_agent.openclaw_hooks import handle_openclaw_event


class FakeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def on_session_start(self, **kwargs):
        self.calls.append(("session_start", kwargs))
        return {"ok": True, "hook": "session_start"}

    def on_user_message(self, **kwargs):
        self.calls.append(("user_message", kwargs))
        return {"decision": "apply", "agent_instruction": "Use concise replies."}

    def on_action_executed(self, **kwargs):
        self.calls.append(("action_executed", kwargs))
        return {"ok": True, "hook": "action_executed"}

    def on_turn_complete(self, **kwargs):
        self.calls.append(("turn_complete", kwargs))
        return {"ok": True, "hook": "turn_complete"}

    def on_session_end(self, **kwargs):
        self.calls.append(("session_end", kwargs))
        return {"ok": True, "hook": "session_end"}


class OpenClawHookBridgeTests(unittest.TestCase):
    def test_before_prompt_build_returns_prompt_mutation(self) -> None:
        manager = FakeManager()
        result = handle_openclaw_event(
            "before_prompt_build",
            {"session_id": "s1", "prompt": "Please review this."},
            manager=manager,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["prependSystemContext"], "Use concise replies.")
        self.assertEqual(manager.calls[0][0], "user_message")
        self.assertEqual(manager.calls[0][1]["message"], "Please review this.")

    def test_before_prompt_build_uses_openclaw_context_session_id(self) -> None:
        manager = FakeManager()
        result = handle_openclaw_event(
            "before_prompt_build",
            {
                "payload": {"prompt": "Please review this."},
                "context": {"sessionId": "ctx-session", "sessionKey": "ctx-key"},
            },
            manager=manager,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(manager.calls[0][1]["session_id"], "ctx-session")
        self.assertEqual(manager.calls[0][1]["context"]["session_key"], "ctx-key")

    def test_before_prompt_build_reads_context_nested_in_payload_event(self) -> None:
        manager = FakeManager()
        result = handle_openclaw_event(
            "before_prompt_build",
            {
                "payload": {
                    "prompt": "Please review this.",
                    "context": {"sessionId": "event-session", "sessionKey": "event-key"},
                }
            },
            manager=manager,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(manager.calls[0][1]["session_id"], "event-session")
        self.assertEqual(manager.calls[0][1]["context"]["session_key"], "event-key")

    def test_after_tool_call_maps_to_action_capture(self) -> None:
        manager = FakeManager()
        result = handle_openclaw_event(
            "after_tool_call",
            {"session_id": "s1", "toolName": "pytest", "result": "24 passed"},
            manager=manager,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(manager.calls[0][0], "action_executed")
        self.assertEqual(manager.calls[0][1]["action"], "openclaw.after_tool_call.pytest")

    def test_after_tool_call_handles_nested_tool_and_structured_result(self) -> None:
        manager = FakeManager()
        result = handle_openclaw_event(
            "after_tool_call",
            {
                "session_id": "s1",
                "toolCall": {"name": "pytest"},
                "result": {"exitCode": 0, "stdout": "187 passed"},
            },
            manager=manager,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(manager.calls[0][1]["action"], "openclaw.after_tool_call.pytest")
        self.assertIn('"exitCode": 0', manager.calls[0][1]["result"])
        self.assertIn("187 passed", manager.calls[0][1]["result"])

    def test_agent_end_maps_to_turn_complete(self) -> None:
        manager = FakeManager()
        result = handle_openclaw_event(
            "agent_end",
            {"session_id": "s1", "prompt": "Question", "assistantResponse": "Answer"},
            manager=manager,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(manager.calls[0][0], "turn_complete")
        self.assertEqual(manager.calls[0][1]["user_message"], "Question")
        self.assertEqual(manager.calls[0][1]["assistant_response"], "Answer")

    def test_llm_output_uses_assistant_texts(self) -> None:
        manager = FakeManager()
        result = handle_openclaw_event(
            "llm_output",
            {"session_id": "s1", "prompt": "Question", "assistantTexts": ["Answer", "More"]},
            manager=manager,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(manager.calls[0][0], "turn_complete")
        self.assertEqual(manager.calls[0][1]["assistant_response"], "Answer\nMore")

    def test_agent_end_extracts_messages_turn(self) -> None:
        manager = FakeManager()
        result = handle_openclaw_event(
            "agent_end",
            {
                "session_id": "s1",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Question"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "Answer"}]},
                ],
            },
            manager=manager,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(manager.calls[0][1]["user_message"], "Question")
        self.assertEqual(manager.calls[0][1]["assistant_response"], "Answer")

    def test_session_end_maps_to_flush(self) -> None:
        manager = FakeManager()
        result = handle_openclaw_event("session_end", {"session_id": "s1"}, manager=manager)

        self.assertTrue(result["ok"])
        self.assertEqual(manager.calls[0][0], "session_end")


if __name__ == "__main__":
    unittest.main()
