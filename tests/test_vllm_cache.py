from pathlib import Path

import pytest

from backend.app.vllm.cache import (
    CachePathError,
    directory_size_bytes,
    list_cached_models,
    repo_dir_to_repo_id,
    repo_id_to_repo_dir,
    resolve_cache_entry,
)


def _make_repo(cache_root: Path, repo_id: str, blob_sizes: list[int]) -> Path:
    """Builds a Hugging Face style cache entry: real blobs, symlinked snapshots."""
    repo_dir = cache_root / "hub" / repo_id_to_repo_dir(repo_id)
    blobs = repo_dir / "blobs"
    snapshot = repo_dir / "snapshots" / "abc123"
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)

    for index, size in enumerate(blob_sizes):
        blob = blobs / f"blob{index}"
        blob.write_bytes(b"x" * size)
        (snapshot / f"model-{index}.safetensors").symlink_to(blob)

    return repo_dir


class TestRepoIdRoundTrip:
    def test_directory_name_maps_to_repository_id(self):
        assert repo_dir_to_repo_id("models--Qwen--Qwen2.5-Coder-7B") == "Qwen/Qwen2.5-Coder-7B"

    def test_repository_id_maps_to_directory_name(self):
        assert repo_id_to_repo_dir("Qwen/Qwen2.5-Coder-7B") == "models--Qwen--Qwen2.5-Coder-7B"

    def test_unprefixed_directories_are_not_cache_entries(self):
        assert repo_dir_to_repo_id("datasets--squad") is None
        assert repo_dir_to_repo_id(".locks") is None


class TestDirectorySize:
    def test_symlinks_are_not_counted_twice(self, tmp_path: Path):
        # Every snapshot file links into blobs/, so following links would double the
        # reported size of every cached model.
        repo = _make_repo(tmp_path, "Org/Model", [1000, 2000])
        assert directory_size_bytes(repo) == 3000

    def test_missing_directory_is_zero(self, tmp_path: Path):
        assert directory_size_bytes(tmp_path / "absent") == 0


class TestListCachedModels:
    def test_reports_each_repository_with_its_size(self, tmp_path: Path):
        _make_repo(tmp_path, "Org/Small", [10])
        _make_repo(tmp_path, "Org/Large", [500])

        models = list_cached_models(tmp_path)

        assert [model.repo_id for model in models] == ["Org/Large", "Org/Small"]
        assert models[0].size_bytes == 500

    def test_empty_cache_lists_nothing(self, tmp_path: Path):
        assert list_cached_models(tmp_path) == []

    def test_ignores_non_model_entries(self, tmp_path: Path):
        (tmp_path / "hub").mkdir()
        (tmp_path / "hub" / ".locks").mkdir()
        (tmp_path / "hub" / "version.txt").write_text("1")

        assert list_cached_models(tmp_path) == []


class TestResolveCacheEntry:
    def test_resolves_inside_the_cache(self, tmp_path: Path):
        (tmp_path / "hub").mkdir()
        resolved = resolve_cache_entry(tmp_path, "Org/Model")
        assert resolved.name == "models--Org--Model"

    @pytest.mark.parametrize(
        "repo_id",
        ["../../etc", "Org/../../..", "/etc/passwd", "", "Org/Model;rm -rf /"],
    )
    def test_rejects_ids_that_escape_the_cache(self, tmp_path: Path, repo_id: str):
        (tmp_path / "hub").mkdir()
        with pytest.raises(CachePathError):
            resolve_cache_entry(tmp_path, repo_id)
