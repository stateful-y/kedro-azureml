"""Stage the code snapshot once per CLI invocation.

Azure ML resolves the ``code`` of every pipeline step on its own: it walks the
whole directory, matches every entry against the ignore file, and hashes what
survives. With ``code_directory: "."`` that walk covers the entire checkout
(virtual environment, data, git objects) once per step, and a batch of jobs
repeats it hundreds of times. Staging copies the files the ignore rules keep
into a small temporary directory once, so every later walk covers a few hundred
entries instead.

The selection reuses the SDK's own matcher, so the staged set is exactly what
the SDK would have uploaded from ``code_directory`` directly, whitelist
``.amlignore`` semantics included.

See Also
--------
[register_code_snapshot][kedro_azureml_pipeline.client.register_code_snapshot] : Registers the staged directory once per workspace.
[AzureMLPipelineGenerator][kedro_azureml_pipeline.generator.AzureMLPipelineGenerator] : Consumes the resulting code reference.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

logger = logging.getLogger(__name__)

#: Ignore files are not staged: the SDK reads them from the directory it uploads,
#: and an empty matcher on the staged directory is what makes every staged file
#: travel. Only the root copies matter, since the SDK ignores nested ones.
ROOT_IGNORE_FILES = frozenset({".amlignore", ".gitignore"})


def _sdk_selection_helpers():
    """Return the SDK's ignore-file and enumeration helpers.

    Returns
    -------
    tuple
        ``(get_ignore_file, get_upload_files_from_folder, get_content_hash)``.

    Raises
    ------
    ImportError
        If the installed ``azure-ai-ml`` no longer exposes the helpers this
        module relies on. The message names the installed version so the
        upgrade that moved them is easy to identify.
    """
    try:
        from azure.ai.ml._utils._asset_utils import get_content_hash, get_ignore_file, get_upload_files_from_folder
    except ImportError as exc:
        from azure.ai.ml import __version__ as sdk_version

        msg = (
            f"azure-ai-ml {sdk_version} does not expose the snapshot helpers "
            "(azure.ai.ml._utils._asset_utils.get_ignore_file / get_upload_files_from_folder / get_content_hash) "
            "that kedro-azureml-pipeline uses to stage the code snapshot."
        )
        raise ImportError(msg) from exc
    return get_ignore_file, get_upload_files_from_folder, get_content_hash


def snapshot_content_hash(staged_dir: str | Path) -> str:
    """Return the SDK's content hash of *staged_dir*, the value it files code assets under.

    Parameters
    ----------
    staged_dir : str or Path
        A staged snapshot directory, which holds no ignore file.

    Returns
    -------
    str
        Hex digest of the directory's file list and contents.
    """
    _, _, get_content_hash = _sdk_selection_helpers()
    return str(get_content_hash(Path(staged_dir).resolve()))


def select_snapshot_files(code_directory: str | Path) -> list[tuple[Path, str]]:
    """Return the files the Azure ML SDK would upload from *code_directory*.

    Parameters
    ----------
    code_directory : str or Path
        The configured ``execution.code_directory``, resolved against the
        current working directory when relative.

    Returns
    -------
    list of tuple
        ``(source, relative_path)`` pairs: the absolute source file and its
        POSIX path relative to *code_directory*. Root ``.amlignore`` and
        ``.gitignore`` files are left out.
    """
    get_ignore_file, get_upload_files_from_folder, _ = _sdk_selection_helpers()
    root = Path(code_directory).resolve()
    ignore_file = get_ignore_file(root)
    selected = get_upload_files_from_folder(root, ignore_file=ignore_file)
    return [(Path(source), relative) for source, relative in selected if relative not in ROOT_IGNORE_FILES]


@contextmanager
def stage_code_snapshot(code_directory: str | Path) -> Iterator[Path]:
    """Copy the snapshot files of *code_directory* into a temporary directory.

    The directory exists for the lifetime of the context and is removed on
    exit, whether the block completed or raised.

    Parameters
    ----------
    code_directory : str or Path
        The configured ``execution.code_directory``.

    Yields
    ------
    Path
        The staged directory, holding the selected files at their original
        relative paths and no ignore file.
    """
    root = Path(code_directory).resolve()
    files = select_snapshot_files(root)
    with TemporaryDirectory(prefix="kedro-azureml-snapshot-") as tmp:
        staged = Path(tmp)
        for source, relative in files:
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        logger.info("Staged %d snapshot files from %s into %s", len(files), root, staged)
        yield staged
