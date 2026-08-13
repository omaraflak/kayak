"""Tests for automatic tool schema extraction and the thinking-token parser.

Both are pure functions the whole agent loop depends on: a wrong schema means the
model calls tools with the wrong arguments, and a parser slip leaks reasoning text
into the visible answer.
"""

from typing import List, Optional
from backend.app.llm import ThinkingStreamParser, extract_thinking_and_content
from backend.app.tools.registry import extract_tool_schema, parse_docstring


class TestParseDocstring:
    def test_description_and_args_are_separated(self):
        docstring = """Does a thing.

        Args:
            alpha: The first value.
            beta: The second value.
        """
        description, params = parse_docstring(docstring)

        assert "Does a thing." in description
        assert params["alpha"] == "The first value."
        assert params["beta"] == "The second value."

    def test_multiline_argument_descriptions_are_joined(self):
        docstring = """Summary.

        Args:
            alpha: A description that
                continues on the next line.
        """
        _, params = parse_docstring(docstring)

        assert params["alpha"] == "A description that continues on the next line."

    def test_typed_argument_annotations_are_stripped(self):
        docstring = """Summary.

        Args:
            alpha (str): Typed parameter.
        """
        _, params = parse_docstring(docstring)

        assert params["alpha"] == "Typed parameter."

    def test_missing_docstring_is_handled(self):
        assert parse_docstring(None) == ("", {})


class TestExtractToolSchema:
    def test_required_and_optional_parameters_are_distinguished(self):
        def sample(query: str, limit: int = 5) -> str:
            """Searches for things.

            Args:
                query: What to search for.
                limit: How many results.
            """
            return ""

        schema = extract_tool_schema(sample)["function"]

        assert schema["name"] == "sample"
        assert schema["parameters"]["required"] == ["query"]
        assert schema["parameters"]["properties"]["query"]["type"] == "string"
        assert schema["parameters"]["properties"]["limit"]["type"] == "integer"

    def test_runtime_context_parameters_are_hidden_from_the_model(self):
        def sample(path: str, workspace_dir=None, container_id=None, agent_depth=0) -> str:
            """Reads a path.

            Args:
                path: Path to read.
            """
            return ""

        properties = extract_tool_schema(sample)["function"]["parameters"]["properties"]

        assert set(properties) == {"path"}

    def test_generic_types_map_to_json_types(self):
        def sample(items: List[str], flag: bool, ratio: float, note: Optional[str] = None) -> str:
            """Takes assorted types."""
            return ""

        properties = extract_tool_schema(sample)["function"]["parameters"]["properties"]

        assert properties["items"]["type"] == "array"
        assert properties["flag"]["type"] == "boolean"
        assert properties["ratio"]["type"] == "number"


class TestThinkingStreamParser:
    def _feed_all(self, chunks: List[str]):
        parser = ThinkingStreamParser()
        events = []
        for chunk in chunks:
            events.extend(parser.feed(chunk))
        events.extend(parser.flush())
        return events

    def _joined(self, events, event_type: str) -> str:
        return "".join(e["content"] for e in events if e["type"] == event_type)

    def test_plain_text_is_all_content(self):
        events = self._feed_all(["Hello ", "world"])
        assert self._joined(events, "token") == "Hello world"
        assert self._joined(events, "thinking") == ""

    def test_thinking_block_is_separated_from_answer(self):
        events = self._feed_all(["<think>reasoning</think>answer"])

        assert self._joined(events, "thinking") == "reasoning"
        assert self._joined(events, "token") == "answer"

    def test_tags_split_across_chunk_boundaries(self):
        # Providers split tokens arbitrarily; a tag straddling two chunks must not
        # leak half of itself into the visible answer.
        events = self._feed_all(["<th", "ink>hid", "den</thi", "nk>visible"])

        assert self._joined(events, "thinking") == "hidden"
        assert self._joined(events, "token") == "visible"

    def test_unclosed_thinking_block_flushes_as_thinking(self):
        events = self._feed_all(["<think>never closed"])

        assert self._joined(events, "thinking") == "never closed"
        assert self._joined(events, "token") == ""

    def test_lone_angle_bracket_is_not_swallowed(self):
        events = self._feed_all(["a < b and c > d"])
        assert self._joined(events, "token") == "a < b and c > d"


class TestExtractThinkingAndContent:
    def test_thinking_is_stripped_from_content(self):
        thinking, content = extract_thinking_and_content("<think>why</think>result")

        assert thinking == "why"
        assert content == "result"

    def test_text_without_thinking_is_returned_unchanged(self):
        thinking, content = extract_thinking_and_content("just an answer")

        assert thinking is None
        assert content == "just an answer"

    def test_empty_input_returns_nothing(self):
        assert extract_thinking_and_content(None) == (None, None)
