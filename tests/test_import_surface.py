"""The package's import surface: lazy public names, framework-free leaf submodules."""

import subprocess
import sys

import pytest

import kedro_azureml_pipeline

_PUBLIC = [name for name in kedro_azureml_pipeline.__all__ if name != "__version__"]


def _run(code: str) -> str:
    """Run *code* in a fresh interpreter and return its stdout."""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_importing_the_distributed_helpers_does_not_load_the_kedro_framework():
    """A project's logging handler imports these while ``kedro.framework.project`` is still
    initializing; an eager framework import from the package init is a circular import there.
    """
    loaded = _run(
        "import sys\n"
        "import kedro_azureml_pipeline.distributed\n"
        "print(sorted(m for m in sys.modules if m.startswith('kedro.framework')))"
    )
    assert loaded == "[]"


def test_importing_the_package_alone_does_not_load_the_kedro_framework():
    loaded = _run(
        "import sys\nimport kedro_azureml_pipeline\n"
        "print(sorted(m for m in sys.modules if m.startswith('kedro.framework')))"
    )
    assert loaded == "[]"


@pytest.mark.parametrize("name", _PUBLIC)
def test_public_names_resolve_on_access(name):
    """Every advertised name still resolves, from its defining module, on first access."""
    value = getattr(kedro_azureml_pipeline, name)
    assert value is not None
    assert name in dir(kedro_azureml_pipeline)


def test_unknown_attribute_raises_attribute_error():
    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        kedro_azureml_pipeline.nope  # noqa: B018 - the access is the assertion
