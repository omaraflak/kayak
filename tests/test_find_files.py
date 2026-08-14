"""Tests for the recursive, case-insensitive workspace file finder.

Born from a real transcript: the coding agent spent four tool calls scanning
the entire filesystem for `*.jpg` while the user's uploads sat in the workspace
root as `*.JPG`.
"""

from pathlib import Path

from backend.app.tools.builtins.file_tools import iter_matching_files


def build_tree(root: Path) -> None:
    (root / "DSC04942.JPG").touch()
    (root / "DSC04943.jpg").touch()
    (root / "notes.md").touch()
    (root / "src").mkdir()
    (root / "src" / "main.py").touch()
    (root / "src" / "util.PY").touch()
    (root / "data").mkdir()
    (root / "data" / "set.csv").touch()


class TestIterMatchingFiles:
    def test_matching_ignores_case_in_both_directions(self, tmp_path: Path):
        build_tree(tmp_path)
        assert iter_matching_files(tmp_path, "*.jpg") == [
            "DSC04942.JPG",
            "DSC04943.jpg",
        ]
        assert iter_matching_files(tmp_path, "*.PY") == ["src/main.py", "src/util.PY"]

    def test_search_is_recursive(self, tmp_path: Path):
        build_tree(tmp_path)
        assert iter_matching_files(tmp_path, "*.csv") == ["data/set.csv"]

    def test_a_slash_in_the_pattern_matches_the_relative_path(self, tmp_path: Path):
        build_tree(tmp_path)
        assert iter_matching_files(tmp_path, "src/*.py") == [
            "src/main.py",
            "src/util.PY",
        ]

    def test_no_matches_is_an_empty_list(self, tmp_path: Path):
        build_tree(tmp_path)
        assert iter_matching_files(tmp_path, "*.wav") == []

    def test_results_are_sorted_for_stable_ordering(self, tmp_path: Path):
        build_tree(tmp_path)
        results = iter_matching_files(tmp_path, "*")
        assert results == sorted(results)
