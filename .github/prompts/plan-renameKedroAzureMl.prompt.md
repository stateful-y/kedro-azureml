# Plan: Rename kedro-azureml → kedro-azure-ml

Full rename of the package: pip name, Python module directory, GitHub org URLs, environment variable prefixes, author metadata, and all documentation. Historical CHANGELOG links are left as-is; SonarCloud project key deferred.

---

## Phase 1: Python Module Rename (directory + imports) — sequential

1. **Rename directory** `kedro_azureml/` → `kedro_azure_ml/`
2. **Update all internal imports** within the renamed directory — every `from kedro_azureml.X` / `import kedro_azureml.X` becomes `kedro_azure_ml.X`. Affects ~15 source files across cli, generator, runner, client, config, manager, scheduler, hooks, mlflow_hook, datasets, distributed, and auth subpackages.
3. **Update all test imports** and `unittest.mock.patch` target paths in `tests/` — affects all 12+ test files plus `tests/conftest.py` and `tests/utils.py`.

## Phase 2: Environment Variable Rename *(parallel with Phases 3–5)*

4. **Rename constants** in `kedro_azure_ml/constants.py`: `KEDRO_AZUREML_*` → `KEDRO_AZURE_ML_*`, and `__kedroazureml_distributed_config__` → `__kedro_azure_ml_distributed_config__`
5. **Update all env var usages** in `mlflow_hook.py`, `generator.py`, and relevant test files

## Phase 3: Package Metadata *(parallel with Phases 2, 4, 5)*

6. **Update `pyproject.toml`**: package name → `kedro-azure-ml`, author → `Guillaume Tauzin, gtauzin@stateful-y.io`, all URLs → `stateful-y/kedro-azure-ml`, entry point module paths → `kedro_azure_ml.*`
7. **Update `.bumpversion.cfg`**: path → `kedro_azure_ml/__init__.py`

## Phase 4: Documentation *(parallel with Phases 2, 3, 5)*

8. **Update `README.md`**: all badge URLs (PyPI, pepy, GitHub), documentation links → `kedro-azure-ml.readthedocs.io`, org → `stateful-y`
9. **Update `docs/conf.py`**: import path → `kedro_azure_ml`
10. **Update all `docs/source/` files**: pip install commands, catalog type paths (`kedro_azure_ml.datasets.AzureMLAssetDataset`), GitHub URLs, example paths
11. **Update `docs/spellcheck_exceptions.txt`**: add new variant
12. **Update `CONTRIBUTING.md`**: GitHub action URLs → `stateful-y/kedro-azure-ml`

## Phase 5: CI/CD & Tooling *(parallel with Phases 2, 3, 4)*

13. **Update GitHub workflows** (`tests_and_publish.yml`, `prepare-release.yml`): `PYTHON_PACKAGE: kedro_azure_ml`
14. **Update `.github/actions/e2e_test/action.yml`**: tar.gz filenames, Docker image names, remove `GETINDATA=ROCKS!`
15. **Update `dev-utils/run_e2e_tests.sh`**: image names, tar.gz names, comments
16. **Update `tox.ini`**: `--cov kedro_azure_ml`, `known_first_party = kedro_azure_ml`
17. **Update `sonar-project.properties`**: `sonar.sources=kedro_azure_ml/`, GitHub/RTD URLs (project key deferred)
18. **Update `tests/conf/e2e/azureml.yml`**: cluster name if applicable

## Phase 6: Final Verification

19. **Global grep** for any remaining `kedro.azureml`, `kedro_azureml`, `getindata` — should only match CHANGELOG.md
20. **Run `pytest tests/ -v`** — all tests pass
21. **Import check**: `python -c "from kedro_azure_ml import __version__"`
22. **CLI check**: `kedro azureml --help` still works
23. **Build check**: `poetry build` produces `kedro_azure_ml-*.tar.gz`

---

## Relevant Files (~30 files)

| Area | Files |
|------|-------|
| **Package identity** | `pyproject.toml`, `.bumpversion.cfg`, `kedro_azureml/__init__.py`, `kedro_azureml/constants.py` |
| **Source code** | All `.py` under `kedro_azureml/` (15 files + 4 subpackage `__init__.py`) |
| **Tests** | All files under `tests/` (12+ test files, conftest, utils) |
| **CI/CD** | `.github/workflows/*.yml`, `.github/actions/e2e_test/action.yml`, `tox.ini`, `dev-utils/run_e2e_tests.sh` |
| **Documentation** | `README.md`, `CONTRIBUTING.md`, `docs/conf.py`, all `docs/source/*.md`/`*.rst` |
| **Config** | `sonar-project.properties`, `tests/conf/e2e/azureml.yml` |

## Decisions

- **Python import**: `kedro_azureml` → `kedro_azure_ml` (full rename)
- **GitHub org**: `getindata/kedro-azureml` → `stateful-y/kedro-azure-ml`
- **Env vars**: `KEDRO_AZUREML_*` → `KEDRO_AZURE_ML_*` (breaking)
- **Author**: Guillaume Tauzin, gtauzin@stateful-y.io
- **CHANGELOG**: Historical links left untouched
- **SonarCloud project key**: Deferred
- **CLI command name**: Stays `kedro azureml` (no change)
- **ReadTheDocs**: `kedro-azure-ml.readthedocs.io`

## Further Considerations

1. **PyPI ownership** — Register `kedro-azure-ml` on PyPI separately. Consider adding a deprecation notice to the old `kedro-azureml` package pointing users to the new name.
2. **ReadTheDocs project** — A new RTD project `kedro-azure-ml` needs to be created (or the existing one renamed in RTD admin).
3. **Git history** — Use `git mv kedro_azureml kedro_azure_ml` to preserve file history through the directory rename.
