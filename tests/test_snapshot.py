"""Tests for staging the code snapshot once per invocation."""

import builtins

import pytest

from kedro_azureml_pipeline.snapshot import ROOT_IGNORE_FILES, select_snapshot_files, stage_code_snapshot

# A whitelist ignore file: ignore everything, re-include the application files,
# then re-ignore bytecode inside them. This is the shape that makes directory
# pruning wrong, so it is the shape the staging must reproduce exactly.
WHITELIST = "*\n!/pyproject.toml\n!src/**\n!conf/**\n*.pyc\n"

EXPECTED = {"pyproject.toml", "src/pkg/__init__.py", "src/pkg/nodes.py", "conf/base/catalog.yml"}


@pytest.fixture
def project_tree(tmp_path):
    """A project root with whitelisted files and heavy unlisted directories."""
    (tmp_path / ".amlignore").write_text(WHITELIST)
    (tmp_path / ".gitignore").write_text("*.log\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "README.md").write_text("not staged\n")
    (tmp_path / "src" / "pkg" / "__pycache__").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("")
    (tmp_path / "src" / "pkg" / "nodes.py").write_text("x = 1\n")
    (tmp_path / "src" / "pkg" / "__pycache__" / "nodes.pyc").write_bytes(b"\x00")
    (tmp_path / "conf" / "base").mkdir(parents=True)
    (tmp_path / "conf" / "base" / "catalog.yml").write_text("a: 1\n")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "big.py").write_text("not staged\n")
    (tmp_path / "data" / "01_raw").mkdir(parents=True)
    (tmp_path / "data" / "01_raw" / "x.csv").write_text("1\n")
    return tmp_path


class TestSelectSnapshotFiles:
    """The selection is the SDK's own, minus the root ignore files."""

    def test_matches_the_sdk_selection(self, project_tree):
        from azure.ai.ml._utils._asset_utils import get_ignore_file, get_upload_files_from_folder

        sdk = {
            relative
            for _, relative in get_upload_files_from_folder(project_tree, ignore_file=get_ignore_file(project_tree))
        }
        ours = {relative for _, relative in select_snapshot_files(project_tree)}
        assert ours == sdk - ROOT_IGNORE_FILES
        assert ours == EXPECTED

    def test_relative_directory_resolves_against_cwd(self, project_tree, monkeypatch):
        monkeypatch.chdir(project_tree)
        assert {relative for _, relative in select_snapshot_files(".")} == EXPECTED

    def test_sources_are_absolute_files(self, project_tree):
        for source, relative in select_snapshot_files(project_tree):
            assert source.is_absolute()
            assert source == project_tree / relative

    def test_missing_sdk_helpers_raise_a_named_import_error(self, monkeypatch, tmp_path):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "azure.ai.ml._utils._asset_utils":
                raise ImportError("gone")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match="azure-ai-ml .* snapshot helpers"):
            select_snapshot_files(tmp_path)


class TestStageCodeSnapshot:
    """The staged directory holds the selection and nothing else, then disappears."""

    def test_copies_selected_files_at_their_relative_paths(self, project_tree):
        with stage_code_snapshot(project_tree) as staged:
            found = {path.relative_to(staged).as_posix() for path in staged.rglob("*") if path.is_file()}
            assert found == EXPECTED
            assert (staged / "src" / "pkg" / "nodes.py").read_text() == "x = 1\n"

    def test_root_ignore_files_are_not_staged(self, project_tree):
        with stage_code_snapshot(project_tree) as staged:
            assert not (staged / ".amlignore").exists()
            assert not (staged / ".gitignore").exists()

    def test_nested_ignore_files_are_ordinary_files(self, project_tree):
        (project_tree / "src" / "pkg" / ".gitignore").write_text("*.tmp\n")
        with stage_code_snapshot(project_tree) as staged:
            assert (staged / "src" / "pkg" / ".gitignore").exists()

    def test_directory_removed_on_success(self, project_tree):
        with stage_code_snapshot(project_tree) as staged:
            assert staged.is_dir()
        assert not staged.exists()

    def test_directory_removed_on_error(self, project_tree):
        with pytest.raises(RuntimeError, match="boom"), stage_code_snapshot(project_tree) as staged:
            raise RuntimeError("boom")
        assert not staged.exists()


class TestSnapshotContentHash:
    """The version of the registered asset is the SDK's own content hash."""

    def test_matches_the_sdk_hash_and_tracks_content(self, project_tree):
        from azure.ai.ml._utils._asset_utils import get_content_hash

        from kedro_azureml_pipeline.snapshot import snapshot_content_hash

        with stage_code_snapshot(project_tree) as staged:
            first = snapshot_content_hash(staged)
            assert first == get_content_hash(staged)
            assert len(first) == 64
            (staged / "src" / "pkg" / "nodes.py").write_text("x = 2\n")
            assert snapshot_content_hash(staged) != first
