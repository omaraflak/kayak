"""Tests for automatic tool schema extraction and the thinking-token parser.

Both are pure functions the whole agent loop depends on: a wrong schema means the
model calls tools with the wrong arguments, and a parser slip leaks reasoning text
into the visible answer.
"""

from typing import List, Optional
from backend.app.llm import ThinkingStreamParser, extract_thinking_and_content
from backend.app.tools.schema import extract_tool_schema, parse_docstring


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


class TestOptionalParameters:
    def test_optional_types_are_described_by_what_they_hold(self):
        # Reported as strings, a model sent "120" for a timeout and the tool raised.
        def sample(timeout: Optional[int] = 60, ratio: float | None = None) -> str:
            """Waits."""
            return ""

        properties = extract_tool_schema(sample)["function"]["parameters"]["properties"]

        assert properties["timeout"]["type"] == "integer"
        assert properties["ratio"]["type"] == "number"


class TestCustomToolsAreReadNotRun:
    SOURCE = '''
import os

os.environ["KAYAK_TOOL_RAN"] = "yes"

CATEGORY = "web"
RISK = "low"
READ_ONLY = True


def _helper():
    pass


def execute(query: str, limit: Optional[int] = 5, *, strict: bool = False) -> str:
    """Searches.

    Args:
        query: What to look for.
        limit: How many results.
    """
    return query
'''

    def test_the_source_is_never_executed(self, monkeypatch):
        import os
        from backend.app.tools.schema import read_custom_tool

        monkeypatch.delenv("KAYAK_TOOL_RAN", raising=False)
        read_custom_tool("searcher", self.SOURCE)

        assert "KAYAK_TOOL_RAN" not in os.environ

    def test_the_schema_comes_from_the_signature_and_docstring(self):
        from backend.app.tools.schema import read_custom_tool

        tool = read_custom_tool("searcher", self.SOURCE)
        function = tool.schema["function"]

        assert tool.entrypoint == "execute"
        assert function["name"] == "searcher"
        assert function["description"] == "Searches."
        assert function["parameters"]["required"] == ["query"]
        properties = function["parameters"]["properties"]
        assert properties["query"] == {"type": "string", "description": "What to look for."}
        assert properties["limit"]["type"] == "integer"
        assert properties["strict"]["type"] == "boolean"

    def test_declared_classification_is_read_from_constants(self):
        from backend.app.models import ToolCategory, ToolRisk
        from backend.app.tools.schema import read_custom_tool

        metadata = read_custom_tool("searcher", self.SOURCE).metadata

        assert metadata.category == ToolCategory.WEB
        assert metadata.risk == ToolRisk.LOW
        assert metadata.read_only is True

    def test_the_first_public_function_is_the_fallback_entrypoint(self):
        from backend.app.tools.schema import read_custom_tool

        source = "def _private():\n    pass\n\n\ndef convert(x: int) -> int:\n    return x\n"

        assert read_custom_tool("anything", source).entrypoint == "convert"

    def test_source_without_a_function_is_refused(self):
        import pytest
        from backend.app.tools.schema import ToolSourceError, read_custom_tool

        with pytest.raises(ToolSourceError):
            read_custom_tool("empty", "X = 1\n")
        with pytest.raises(ToolSourceError):
            read_custom_tool("broken", "def execute(:\n")
