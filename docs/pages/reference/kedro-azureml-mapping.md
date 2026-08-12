# `kedro-azureml` Mapping

Lookup tables mapping [`kedro-azureml`](https://github.com/getindata/kedro-azureml) v1.0.0 to the current `kedro-azureml-pipeline` release. Use these while migrating a project; the step-by-step procedure is in [Migrate from kedro-azureml](../how-to/migrate-from-kedro-azureml.md).

This plugin forked from `kedro-azureml` v1.0.0. Earlier upstream versions are not covered.

---

## Configuration keys

Upstream put everything under a single `azure:` key plus a `docker:` key. This plugin replaces both with three flat sections, and adds `schedules` and `jobs`. Full field documentation is in the [Configuration reference](configuration.md).

| `kedro-azureml` v1.0.0 | `kedro-azureml-pipeline` | Change |
|---|---|---|
| `azure.subscription_id` | `workspace.__default__.subscription_id` | Relocated |
| `azure.resource_group` | `workspace.__default__.resource_group` | Relocated |
| `azure.workspace_name` | `workspace.__default__.name` | Relocated and **renamed** |
| `azure.experiment_name` | `jobs.<name>.experiment_name` | Moved to per-job, now optional |
| `azure.environment_name` | `execution.environment` | **Renamed** |
| `azure.code_directory` | `execution.code_directory` | Relocated |
| `azure.working_directory` | `execution.working_directory` | Relocated |
| `azure.compute` | `compute` | Promoted to top level, shape unchanged |
| `azure.temporary_storage.account_name` | none | **Removed** |
| `azure.temporary_storage.container` | none | **Removed** |
| `azure.pipeline_data_passing.enabled` | none | **Removed**, behavior is always on |
| `docker.image` | `execution.environment` | Section removed, value relocated |
| none | `schedules` | New, see [`schedules`](configuration.md#schedules) |
| none | `jobs` | New, see [`jobs`](configuration.md#jobs) |

Three notes on the less obvious rows:

- **`workspace_name` becomes `name`.** The value is the same Azure ML workspace name, but the key is shorter because it now sits inside a named workspace entry under `workspace`.
- **`experiment_name` is optional.** A job that does not set it falls back to the experiment name in `mlflow.yml`. See [Use MLflow](../how-to/use-mlflow.md).
- **`docker.image` needs no rebuild.** A container image URI placed in `execution.environment` is detected and wrapped in an Azure ML `Environment` object, so the SDK treats it as an anonymous image rather than a named environment reference.

Every configuration model rejects unknown keys. A leftover upstream key raises a validation error naming the key, rather than being silently ignored.

---

## CLI flags

The structural change is that run configuration moved out of command-line flags and into named `jobs` entries in `azureml.yml`, selected with `-j`. Full flag documentation is in the [CLI reference](cli.md).

### `kedro azureml init`

| `kedro-azureml` v1.0.0 | `kedro-azureml-pipeline` | Change |
|---|---|---|
| `init <subscription_id> <resource_group> <workspace_name> <experiment_name> <cluster_name>` | `init` | Positional arguments **removed**; writes a placeholder file to fill in by hand |
| `--azureml-environment` / `--aml-env` | `execution.environment` | Moved to config |
| `-d` / `--docker-image` | `execution.environment` | Moved to config |
| `-a` / `--storage-account-name` | none | **Removed** |
| `-c` / `--storage-container` | none | **Removed** |
| `--use-pipeline-data-passing` | none | **Removed**, behavior is always on |

See [`kedro azureml init`](cli.md#kedro-azureml-init).

### `kedro azureml run`

| `kedro-azureml` v1.0.0 | `kedro-azureml-pipeline` | Change |
|---|---|---|
| `-p` / `--pipeline` | `jobs.<name>.pipeline.pipeline_name`, selected with `-j` / `--job` | Moved to config |
| `-s` / `--subscription-id` | `-w` / `--workspace` | Replaced; selects a named workspace entry |
| `-i` / `--image` | `--aml-env`, or `execution.environment` | Replaced |
| `--params` | `--params` | Unchanged |
| `--env-var` | `--env-var` | Unchanged |
| `--load-versions` / `-lv` | `--load-versions` / `-lv` | Unchanged |
| `--azureml-environment` / `--aml-env` | `--azureml-environment` / `--aml-env` | Unchanged |
| `--wait-for-completion` | `--wait-for-completion` | Unchanged |
| `--on-job-scheduled` | `--on-job-scheduled` | Unchanged |
| none | `--dry-run` | New |

See [`kedro azureml run`](cli.md#kedro-azureml-run).

### `kedro azureml compile`

| `kedro-azureml` v1.0.0 | `kedro-azureml-pipeline` | Change |
|---|---|---|
| `-p` / `--pipeline` | `-j` / `--job`, or `--all` | Moved to config |
| `-i` / `--image` | `--aml-env`, or `execution.environment` | Replaced |
| `-o` / `--output` | `-o` / `--output` | Unchanged; suffixed with the job name when compiling several jobs |
| `--params`, `--env-var`, `--load-versions` / `-lv` | same | Unchanged |
| none | `--all` | New; compiles every resolved job |
| none | `--check` | New; compiles in memory and exits non-zero on failure |

See [`kedro azureml compile`](cli.md#kedro-azureml-compile).

### Group option and new commands

`-e` / `--env` on the `kedro azureml` group is unchanged.

| Command | Status |
|---|---|
| [`kedro azureml schedule`](cli.md#kedro-azureml-schedule) | New; creates, updates, or (with `--delete`) removes Azure ML schedules |
| [`kedro azureml resolve-patterns`](cli.md#kedro-azureml-resolve-patterns) | New |
| [`kedro azureml list-patterns`](cli.md#kedro-azureml-list-patterns) | New |
| [`kedro azureml execute`](cli.md#kedro-azureml-execute) | Unchanged; internal, invoked on the compute node |

!!! note "Flags the changelog lists but upstream never had"

    The `0.1.0-alpha.1` changelog entry states that `kedro azureml run` lost `--display-name`, `--compute-name`, and `--experiment-name`. Those options do not exist in upstream v1.0.0, so there is nothing to migrate. The equivalent settings are available as the `display_name`, `compute`, and `experiment_name` fields of a job. See [`jobs`](configuration.md#jobs).

---

## Modules and classes

The import root changes from `kedro_azureml` to `kedro_azureml_pipeline`, and several modules moved.

| `kedro-azureml` v1.0.0 | `kedro-azureml-pipeline` | Change |
|---|---|---|
| `kedro_azureml.cli` | `kedro_azureml_pipeline.cli.commands` | Moved |
| `kedro_azureml.cli_functions` | `kedro_azureml_pipeline.cli.functions` | Moved |
| `kedro_azureml.config` | `kedro_azureml_pipeline.config.models` | Moved |
| `kedro_azureml.hooks` | `kedro_azureml_pipeline.hooks.local_run`, `kedro_azureml_pipeline.hooks.mlflow` | Split in two |
| `kedro_azureml.generator` | `kedro_azureml_pipeline.generator` | Unchanged path |
| `kedro_azureml.runner` | `kedro_azureml_pipeline.runner` | Unchanged path |
| `kedro_azureml.manager` | `kedro_azureml_pipeline.manager` | Unchanged path |
| `kedro_azureml.distributed` | `kedro_azureml_pipeline.distributed` | Unchanged path |
| none | `kedro_azureml_pipeline.scheduler` | New |
| none | `kedro_azureml_pipeline.factory` | New; job factories |

### Datasets

| `kedro-azureml` v1.0.0 | `kedro-azureml-pipeline` | Change |
|---|---|---|
| `kedro_azureml.datasets.AzureMLPipelineDataset` | [`kedro_azureml_pipeline.datasets.AzureMLPipelineDataset`][kedro_azureml_pipeline.datasets.AzureMLPipelineDataset] | Import path only |
| `kedro_azureml.datasets.AzureMLAssetDataset` | [`kedro_azureml_pipeline.datasets.AzureMLAssetDataset`][kedro_azureml_pipeline.datasets.AzureMLAssetDataset] | Import path only |
| `kedro_azureml.datasets.AzureMLFileDataset` | none | **Removed**, SDK v1 stub |
| `kedro_azureml.datasets.AzureMLPandasDataset` | none | **Removed**, SDK v1 stub |
| `kedro_azureml.datasets.v1_datasets` | none | **Removed** |
| `kedro_azureml.datasets.KedroAzureRunnerDataset` | none | **Removed** with blob data passing |
| `kedro_azureml.datasets.KedroAzureRunnerDistributedDataset` | none | **Removed** with blob data passing |
| `kedro_azureml.datasets.runner_dataset` | none | **Removed** |

Both surviving datasets keep identical constructor parameters (`azureml_dataset`, `dataset`, `root_dir`, `filepath_arg`, `azureml_type`, `version`, `azureml_version`, `metadata`), so a catalog entry needs only its `type:` string updated. Parameter tables are in the [Datasets reference](datasets.md).

### Hooks and entry points

| Entry point | Status |
|---|---|
| `azure_local_run_hook` | Unchanged name; now at `kedro_azureml_pipeline.hooks.local_run` |
| `mlflow_azureml_hook` | New; at `kedro_azureml_pipeline.hooks.mlflow` |

Both are registered automatically on install. Neither needs to be added to `HOOKS` in `settings.py`.

---

## Dependencies

| Dependency | `kedro-azureml` v1.0.0 | `kedro-azureml-pipeline` |
|---|---|---|
| Python | `>=3.9,<3.13` | `>=3.11` |
| `kedro` | `^1.0.0` | `>=1.0.0` |
| `kedro-datasets` | `>=1.0.0` | `>=1.0.0` |
| `pydantic` | `>=2.6.4,<2.11.0` | `>=2.6.4` |
| `cloudpickle` | `^2.1.0` | `>=3.0.0` |
| `azure-ai-ml` | `>=1.2.0` | `>=1.2.0` |
| `pyarrow` | `>=11.0.0` | `>=18.0.0` |
| `adlfs` | `>=2022.2.0` | Removed with blob data passing |
| `backoff` | `^2.2.1` | Removed |

The optional `mlflow` extra changed as well:

| Dependency | `kedro-azureml` v1.0.0 | `kedro-azureml-pipeline` |
|---|---|---|
| `mlflow` | `>2.0.0,<3.0.0` | `>=3.13` |
| `azureml-mlflow` | `>=1.42.0` | `>=1.42.0` |
| `kedro-mlflow` | not included | `>=2.0.0` |

## See also

- [Migrate from kedro-azureml](../how-to/migrate-from-kedro-azureml.md) for the procedure that uses these tables
- [Configuration reference](configuration.md) for every field of the current schema
- [CLI reference](cli.md) for every command and flag
- [Datasets reference](datasets.md) for dataset parameter tables
