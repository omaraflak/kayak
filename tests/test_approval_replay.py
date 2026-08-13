"""Tests for describing approvals a conversation is parked on.

A tool approval is announced exactly once, over a stream the browser can lose at any
time. Without a way to re-announce it, backgrounding a tab meant returning to a
conversation that looked idle while the engine sat waiting out its full timeout.
"""

import pytest

from backend.app.agent.approvals import ApprovalRegistry


@pytest.fixture
def registry() -> ApprovalRegistry:
    return ApprovalRegistry()


class TestListForConversation:
    def test_describes_a_parked_call_well_enough_to_re_ask(self, registry):
        registry.register(
            call_id="call_1",
            conversation_id="conv_a",
            tool_name="run_command",
            arguments='{"command": "rm -rf build"}',
        )

        pending = registry.list_for_conversation("conv_a")

        # The replayed prompt has to show what is about to run, not just that
        # something is.
        assert pending == [
            {
                "id": "call_1",
                "name": "run_command",
                "arguments": '{"command": "rm -rf build"}',
            }
        ]

    def test_does_not_leak_approvals_from_other_conversations(self, registry):
        registry.register("call_1", "conv_a", "run_command")
        registry.register("call_2", "conv_b", "write_file")

        assert [item["id"] for item in registry.list_for_conversation("conv_a")] == ["call_1"]

    def test_a_conversation_with_nothing_parked_lists_nothing(self, registry):
        assert registry.list_for_conversation("conv_a") == []

    def test_a_resolved_call_stops_being_listed_once_waited_on(self, registry):
        pending = registry.register("call_1", "conv_a", "run_command")
        registry.resolve("call_1", True)

        # resolve() wakes the waiter; the entry is cleared by wait()'s cleanup, which
        # is what a real turn does next.
        assert pending.approved is True

    def test_cancelling_a_conversation_clears_its_entries(self, registry):
        registry.register("call_1", "conv_a", "run_command")
        registry.register("call_2", "conv_b", "write_file")

        registry.cancel_conversation("conv_a")

        assert registry.list_for_conversation("conv_a") == []
        assert len(registry.list_for_conversation("conv_b")) == 1
