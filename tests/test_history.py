"""Tests for conversation history repair and context budgeting.

These cover the failure that made cancelled turns unrecoverable: an assistant message
requesting tool calls that nothing ever answers is rejected by every provider, so the
conversation stayed broken for every subsequent turn.
"""

from backend.app.agent.history import (
    INTERRUPTED_RESULT,
    MAX_TOOL_RESULT_CHARS,
    TRUNCATION_NOTICE,
    group_into_atomic_blocks,
    repair_tool_call_pairing,
    truncate_to_budget,
    truncate_tool_result,
)
from backend.app.models import Message, MessageRole


def _assistant_with_calls(*call_ids: str) -> Message:
    return Message(
        conversation_id="c1",
        role=MessageRole.ASSISTANT,
        content=None,
        tool_calls=[
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "run_command", "arguments": "{}"},
            }
            for call_id in call_ids
        ],
        created_at="2026-01-01T00:00:00",
    )


def _tool_result(call_id: str, content: str = "ok") -> Message:
    return Message(
        conversation_id="c1",
        role=MessageRole.TOOL,
        content=content,
        tool_call_id=call_id,
        name="run_command",
        created_at="2026-01-01T00:00:01",
    )


def _user(text: str) -> Message:
    return Message(
        conversation_id="c1",
        role=MessageRole.USER,
        content=text,
        created_at="2026-01-01T00:00:00",
    )


class TestRepairToolCallPairing:
    def test_well_formed_history_is_unchanged(self):
        messages = [_user("hi"), _assistant_with_calls("a1"), _tool_result("a1")]
        assert repair_tool_call_pairing(messages) == messages

    def test_unanswered_tool_call_gets_synthetic_result(self):
        repaired = repair_tool_call_pairing([_user("hi"), _assistant_with_calls("a1")])

        assert len(repaired) == 3
        assert repaired[2].role == MessageRole.TOOL
        assert repaired[2].tool_call_id == "a1"
        assert repaired[2].content == INTERRUPTED_RESULT

    def test_partially_answered_turn_fills_only_the_gap(self):
        repaired = repair_tool_call_pairing(
            [_assistant_with_calls("a1", "a2"), _tool_result("a1")]
        )

        answered = {m.tool_call_id: m.content for m in repaired if m.role == MessageRole.TOOL}
        assert answered == {"a1": "ok", "a2": INTERRUPTED_RESULT}

    def test_orphan_tool_result_is_dropped(self):
        # A result whose call was never requested is rejected by providers just as
        # hard as an unanswered call.
        repaired = repair_tool_call_pairing([_user("hi"), _tool_result("ghost")])

        assert [m.role for m in repaired] == [MessageRole.USER]

    def test_duplicate_results_for_one_call_are_collapsed(self):
        repaired = repair_tool_call_pairing(
            [_assistant_with_calls("a1"), _tool_result("a1", "first"), _tool_result("a1", "second")]
        )

        results = [m for m in repaired if m.role == MessageRole.TOOL]
        assert len(results) == 1
        assert results[0].content == "first"

    def test_every_call_is_answered_exactly_once(self):
        repaired = repair_tool_call_pairing(
            [
                _assistant_with_calls("a1", "a2"),
                _tool_result("a1"),
                _user("next"),
                _assistant_with_calls("b1"),
            ]
        )

        requested = [
            call["id"]
            for m in repaired
            if m.role == MessageRole.ASSISTANT and m.tool_calls
            for call in m.tool_calls
        ]
        answered = [m.tool_call_id for m in repaired if m.role == MessageRole.TOOL]
        assert sorted(requested) == sorted(answered)


class TestTruncateToolResult:
    def test_short_output_is_untouched(self):
        assert truncate_tool_result("hello") == "hello"

    def test_long_output_keeps_head_and_tail(self):
        text = "A" * 5000 + "MIDDLE" + "Z" * 5000
        result = truncate_tool_result(text, max_chars=1000)

        assert len(result) < len(text)
        assert result.startswith("A")
        assert result.endswith("Z")
        assert "omitted from the middle" in result

    def test_default_cap_is_applied(self):
        result = truncate_tool_result("x" * (MAX_TOOL_RESULT_CHARS * 2))
        assert len(result) < MAX_TOOL_RESULT_CHARS * 2


class TestGroupIntoAtomicBlocks:
    def test_tool_results_stay_with_their_assistant_turn(self):
        entries = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "tool_calls": [{"id": "a1"}]},
            {"role": "tool", "tool_call_id": "a1"},
            {"role": "assistant", "content": "done"},
        ]

        blocks = group_into_atomic_blocks(entries)

        assert [len(b) for b in blocks] == [1, 2, 1]


class TestTruncateToBudget:
    def test_history_within_budget_is_returned_verbatim(self):
        entries = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        assert truncate_to_budget(entries, max_chars=10_000) == entries

    def test_system_prompt_always_survives(self):
        entries = [{"role": "system", "content": "sys"}] + [
            {"role": "user", "content": "x" * 500} for _ in range(50)
        ]

        result = truncate_to_budget(entries, max_chars=1000)

        assert result[0]["role"] == "system"
        assert result[1]["content"] == TRUNCATION_NOTICE

    def test_truncation_never_splits_a_tool_block(self):
        entries = [{"role": "system", "content": "sys"}]
        for index in range(20):
            entries.append(
                {
                    "role": "assistant",
                    "tool_calls": [{"id": f"call{index}"}],
                    "content": "y" * 200,
                }
            )
            entries.append(
                {"role": "tool", "tool_call_id": f"call{index}", "content": "z" * 200}
            )

        result = truncate_to_budget(entries, max_chars=1500)

        # Any surviving tool result must still be preceded by the assistant turn
        # that requested it.
        surviving_calls = {
            call["id"]
            for entry in result
            if entry.get("tool_calls")
            for call in entry["tool_calls"]
        }
        for entry in result:
            if entry.get("role") == "tool":
                assert entry["tool_call_id"] in surviving_calls

    def test_most_recent_block_is_kept_even_when_oversized(self):
        entries = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old"},
            {"role": "user", "content": "x" * 5000},
        ]

        result = truncate_to_budget(entries, max_chars=100)

        assert result[-1]["content"] == "x" * 5000
