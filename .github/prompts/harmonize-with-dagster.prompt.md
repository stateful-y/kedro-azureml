---
mode: agent
description: >
  Harmonize kedro-azureml-pipeline tooling, config model patterns, and CI with
  kedro-dagster conventions.  Adds SIM ignores, ty rules, pytest filterwarnings,
  MLflow version matrix, and upgrades Pydantic config models to use ConfigDict +
  Field(description=...).
---

# Harmonize kedro-azureml-pipeline with kedro-dagster

Apply all changes described below to the kedro-azureml-pipeline repository.
Work through each section sequentially. After each section, run the verification
commands listed at the bottom before continuing to the next section.

## Reference: kedro-dagster target patterns

The sibling repository `stateful-y/kedro-dagster` (branch `chore/project-restructure`)
is the reference for structural patterns. You do NOT need to clone it - the target
patterns are described inline below with code examples.

## Shared conventions

Both Kedro orchestration plugins share these conventions. Preserve them in every
change you make:

- **Naming**: snake_case for modules and functions, PascalCase for classes
- **Imports**: `from __future__ import annotations` at top of every file
- **Docstrings**: NumPy style with `Parameters`, `Returns`, `See Also`, `Examples` sections
- **Test classes**: `TestClassName` mirrors the source class being tested
- **Fixture naming**: lowercase with underscores, descriptive (e.g., `dummy_pipeline`, not `pipeline1`)
- **Pydantic models**: `ConfigDict(extra="forbid")`, `Field(description=...)`, minimal validators
- **Config template**: `CONFIG_TEMPLATE_YAML` string + `_CONFIG_TEMPLATE` validated instance at EOF of `models.py`
- **`[ADDITION]` comments**: Only modify `pyproject.toml` sections marked with `# [ADDITION]` to avoid conflicting with copier template updates

---

## 1. Add SIM102 and SIM105 to ruff lint ignores

**File:** `pyproject.toml`

Add `SIM102` and `SIM105` to the `[tool.ruff.lint]` ignore list. These suppress
"collapsible if" and "use contextlib.suppress" suggestions - both repos prefer
explicit clarity over brevity.

**Target:**

```toml
ignore = [
    "E203",    # space before : (needed for how black formats slicing)
    "E501",    # line too long (handled by formatter)
    "B008",    # do not perform function calls in argument defaults
    "PLC0415", # import outside top-level (lazy imports used intentionally)
    "SIM102",  # collapsible if (clarity over brevity)
    "SIM105",  # contextlib.suppress (comments on except clauses preferred)
    "PLR0913", # too many args in func def
    "PLR0915", # too many statements
    "PLR0912", # too many branches
    "PLR2004", # magic value used in comparison
]
```

**After applying**, run `uv run ruff check src/ tests/` to confirm no new errors.

---

## 2. Add missing ty rules

**File:** `pyproject.toml`

Add five additional ty rules at "warn" level to match kedro-dagster's superset.
Keep the existing eight rules and add these five:

```toml
[tool.ty.rules]
# -- existing rules (keep) --
invalid-assignment = "warn"
invalid-argument-type = "warn"
invalid-parameter-default = "warn"
invalid-return-type = "warn"
unresolved-attribute = "warn"
unresolved-import = "warn"
no-matching-overload = "warn"
invalid-method-override = "warn"
# -- new rules from kedro-dagster --
not-subscriptable = "warn"
call-non-callable = "warn"
unsupported-operator = "warn"
missing-argument = "warn"
invalid-type-form = "warn"
```

**After applying**, run `uv run ty check src/`. Fix any genuine bugs. For
false positives from third-party libraries (azure-ai-ml, kedro), add
`# type: ignore[rule-name]` inline comments rather than reverting to "ignore".

---

## 3. Add pytest filterwarnings

**File:** `pyproject.toml`

Add a `filterwarnings` list to `[tool.pytest.ini_options]` to suppress
framework-specific deprecation warnings that clutter test output. This matches
kedro-dagster's pattern of explicitly filtering known third-party warnings.

Add this block inside `[tool.pytest.ini_options]`, after the `markers` list:

```toml
filterwarnings = [
    # Ignore Pydantic v2 deprecation warnings during tests
    "ignore::pydantic.warnings.PydanticDeprecatedSince20",
    "ignore::pydantic.warnings.PydanticDeprecatedSince212",
    # Silence Kedro framework warnings about missing pipelines in test scenarios
    "ignore:Your project does not contain any pipelines with nodes:UserWarning:kedro.framework.cli.starters",
    # Ignore azure-ai-ml SDK deprecation warnings
    "ignore::DeprecationWarning:azure",
    "ignore::FutureWarning:azure",
]
```

---

## 4. Add `with_mlflow` to test_versions nox session

**File:** `noxfile.py`

Add a `with_mlflow` boolean parametrization to the `test_versions` session,
matching kedro-dagster's pattern. When `True`, install the `mlflow` extra.

**Current:**

```python
@nox.session(python=PYTHON_VERSIONS, venv_backend="uv")
@nox.parametrize("kedro_spec", KEDRO_SPECS)
@nox.parametrize("azureml_spec", AZUREML_SPECS)
def test_versions(session: nox.Session, azureml_spec: str, kedro_spec: str) -> None:
    """Run the test suite across a matrix of Kedro and azure-ai-ml versions."""
    session.run_install(
        "uv",
        "sync",
        "--no-default-groups",
        "--group",
        "tests",
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )

    session.run_install(
        "uv",
        "pip",
        "install",
        kedro_spec,
        azureml_spec,
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )

    session.run("pytest", "tests", *session.posargs)
```

**Target:**

```python
@nox.session(python=PYTHON_VERSIONS, venv_backend="uv")
@nox.parametrize("kedro_spec", KEDRO_SPECS)
@nox.parametrize("azureml_spec", AZUREML_SPECS)
@nox.parametrize("with_mlflow", [False, True])
def test_versions(session: nox.Session, azureml_spec: str, kedro_spec: str, with_mlflow: bool) -> None:
    """Run the test suite across a matrix of Kedro and azure-ai-ml versions."""
    sync_args = [
        "uv",
        "sync",
        "--no-default-groups",
        "--group",
        "tests",
    ]
    if with_mlflow:
        sync_args += ["--extra", "mlflow"]
    session.run_install(*sync_args, env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location})

    session.run_install(
        "uv",
        "pip",
        "install",
        kedro_spec,
        azureml_spec,
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )

    session.run("pytest", "tests", *session.posargs)
```

**Also update** `.github/workflows/tests-versions.yml` to add `with_mlflow`
to the matrix:

```yaml
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
        os: [ubuntu-latest]
        kedro_spec: ["kedro>=1.0,<2.0"]
        azureml_spec: ["azure-ai-ml>=1.2,<1.20", "azure-ai-ml>=1.20"]
        with_mlflow: [false, true]
```

And update the nox invocation step to pass the `with_mlflow` parameter:

```yaml
      - name: Run versioned tests
        shell: bash
        run: |
          nox -s "test_versions-${{ matrix.python-version }}(azureml_spec='${{ matrix.azureml_spec }}', kedro_spec='${{ matrix.kedro_spec }}', with_mlflow=${{ matrix.with_mlflow }})"
        env:
          PYTEST_XDIST_AUTO_NUM_WORKERS: 0
```

---

## 5. Upgrade config models to use ConfigDict and Field

**File:** `src/kedro_azureml_pipeline/config/models.py`

Upgrade all Pydantic models to use `ConfigDict(extra="forbid")` and
`Field(description=...)` for each attribute. This catches typos in YAML config
files and enables auto-generated reference documentation via mkdocstrings.

### 5a. Update imports

Add `ConfigDict` and `Field` to the pydantic import:

```python
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator
```

### 5b. Add section dividers

Add section divider comments between model groups for scannability:

```python
# ---------------------------------------------------------------------------
# Workspace models
# ---------------------------------------------------------------------------

class WorkspaceConfig(BaseModel):
    ...

# ---------------------------------------------------------------------------
# Compute models
# ---------------------------------------------------------------------------

class ClusterConfig(BaseModel):
    ...

# ---------------------------------------------------------------------------
# Execution models
# ---------------------------------------------------------------------------

class ExecutionConfig(BaseModel):
    ...

# ---------------------------------------------------------------------------
# Schedule models
# ---------------------------------------------------------------------------

class CronScheduleConfig(BaseModel):
    ...

# ---------------------------------------------------------------------------
# Pipeline and Job models
# ---------------------------------------------------------------------------

class PipelineFilterOptions(BaseModel):
    ...

# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------

class KedroAzureMLConfig(BaseModel):
    ...

# ---------------------------------------------------------------------------
# Config template
# ---------------------------------------------------------------------------

CONFIG_TEMPLATE_YAML = ...
```

### 5c. Add ConfigDict to all user-facing BaseModel classes

Add `model_config = ConfigDict(extra="forbid")` to these classes:

- `WorkspaceConfig`
- `ClusterConfig`
- `ExecutionConfig`
- `CronScheduleConfig`
- `RecurrencePatternConfig`
- `RecurrenceScheduleConfig`
- `ScheduleConfig`
- `PipelineFilterOptions`
- `JobConfig`
- `KedroAzureMLConfig`

Do NOT add `ConfigDict` to `RootModel` subclasses (`WorkspacesConfig`,
`ComputeConfig`) - `RootModel` does not support `extra="forbid"`.

**Example transformation for `WorkspaceConfig`:**

Before:

```python
class WorkspaceConfig(BaseModel):
    """Azure ML workspace identity.

    Parameters
    ----------
    subscription_id : str
        Azure subscription ID.
    resource_group : str
        Azure resource group name.
    name : str
        Azure ML workspace name.

    See Also
    --------
    ...
    """

    subscription_id: str = ""
    resource_group: str = ""
    name: str = ""
```

After:

```python
class WorkspaceConfig(BaseModel):
    """Azure ML workspace identity.

    Parameters
    ----------
    subscription_id : str
        Azure subscription ID.
    resource_group : str
        Azure resource group name.
    name : str
        Azure ML workspace name.

    See Also
    --------
    ...
    """

    model_config = ConfigDict(extra="forbid")

    subscription_id: str = Field(default="", description="Azure subscription ID.")
    resource_group: str = Field(default="", description="Azure resource group name.")
    name: str = Field(default="", description="Azure ML workspace name.")
```

### 5d. Add YAML Examples to top-level models

Add an `Examples` section to the docstrings of `KedroAzureMLConfig` and
`JobConfig` showing YAML syntax. Follow NumPy docstring style:

```python
class KedroAzureMLConfig(BaseModel):
    """Top-level plugin configuration loaded from ``azureml.yml``.

    Parameters
    ----------
    ...

    Examples
    --------
    ```yaml
    # conf/base/azureml.yml
    workspace:
      __default__:
        subscription_id: "abc-123"
        resource_group: "my-rg"
        name: "my-workspace"

    compute:
      __default__:
        cluster_name: "cpu-cluster"

    execution:
      environment: "my-env@latest"

    jobs:
      __default__:
        pipeline:
          pipeline_name: __default__
    ```

    See Also
    --------
    ...
    """
```

### 5e. Rules

- Keep existing `@model_validator` calls intact - they enforce real business
  logic (`__default__` key presence, XOR validation for schedules).
- Do NOT add `@field_validator` for type normalization - let Pydantic's type
  system handle it.
- Keep `Parameters` docstring section style (not `Attributes`).
- Keep `See Also` cross-references.
- Sort `Field()` arguments: `default` first, then `description`.
- Apply the transformation to ALL BaseModel classes in the file, not just the
  examples shown above.

---

## Verification

Run these commands after each section to catch regressions early:

```bash
# Lint
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Type check
uv run ty check src/

# Docstring coverage
uv run interrogate -vv src/

# Tests
uv run pytest tests/ --no-cov -x

# Import smoke tests
uv run python -c "from kedro_azureml_pipeline import KedroAzureMLConfig"
uv run python -c "from kedro_azureml_pipeline import AzureMLPipelineGenerator"
uv run python -c "from kedro_azureml_pipeline import CliContext"
uv run python -c "from kedro_azureml_pipeline.config import CONFIG_TEMPLATE_YAML"

# Docs build
uv run mkdocs build --strict
```
