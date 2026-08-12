# How to Migrate from `kedro-azureml`

This guide migrates a working project from [`kedro-azureml`](https://github.com/getindata/kedro-azureml) v1.0.0 to `kedro-azureml-pipeline`. This plugin is a fork of that project, and its first release rewrote the configuration schema, the CLI, and the module layout, so an existing project needs changes in four places: its dependencies, its `azureml.yml`, its catalog, and the commands used to submit pipelines.

Every lookup table this guide refers to lives in the [`kedro-azureml` mapping reference](../reference/kedro-azureml-mapping.md). The guide gives the procedure and the reasoning; the reference gives the exhaustive key-by-key and flag-by-flag tables.

!!! note "Migrating a plain Kedro project instead?"

    If your project has never used an Azure ML plugin, you do not need this guide. Follow [Adopt Azure ML in an Existing Project](../tutorials/migrate-existing-project.md) instead.

## Prerequisites

Check these before you start, because two of them can block the migration outright:

- **Python 3.11 or newer.** Upstream supported 3.9 to 3.12; this plugin requires `>=3.11`.
- **MLflow 3.13 or newer**, if you use the `mlflow` extra. Upstream pinned `mlflow >2.0.0,<3.0.0`, and this plugin requires `>=3.13`. The extra also now installs [`kedro-mlflow`](https://kedro-mlflow.readthedocs.io/).
- **A working `kedro-azureml` v1.0.0 project.** Earlier upstream versions are not covered; migrate to v1.0.0 first.

`adlfs` and `backoff` are no longer required, since the blob storage data passing that needed them has been removed. The full dependency comparison is in the [mapping reference](../reference/kedro-azureml-mapping.md#dependencies).

!!! tip "A partial migration fails loudly"

    Every configuration model in this plugin rejects unknown keys. If you leave an upstream key such as `azure:` or `temporary_storage:` in `azureml.yml`, loading the config raises a validation error naming that key rather than silently ignoring it. You cannot half-migrate the configuration without noticing.

## Step 1: Replace the package

Uninstall the upstream plugin and install this one:

=== "pip"
    ```bash
    pip uninstall kedro-azureml
    pip install kedro-azureml-pipeline
    ```
=== "uv"
    ```bash
    uv remove kedro-azureml
    uv add kedro-azureml-pipeline
    ```

If you use MLflow, install the extra instead: `kedro-azureml-pipeline[mlflow]`.

Both plugins register a `kedro azureml` command group, so do not keep them installed side by side.

## Step 2: Update imports

The import root changes from `kedro_azureml` to `kedro_azureml_pipeline`. Most projects import nothing directly and can skip to the next step, since catalog entries are handled in [Step 4](#step-4-update-the-catalog) and both hooks are registered automatically.

If you do import from the plugin, note that three modules also moved:

| Upstream | This plugin |
|---|---|
| `kedro_azureml.config` | `kedro_azureml_pipeline.config.models` |
| `kedro_azureml.cli_functions` | `kedro_azureml_pipeline.cli.functions` |
| `kedro_azureml.hooks` | `kedro_azureml_pipeline.hooks.local_run` and `.hooks.mlflow` |

Everything else keeps its module path under the new root. The [module and class table](../reference/kedro-azureml-mapping.md#modules-and-classes) lists them all.

If you use distributed training, `@distributed_job` is unchanged apart from its import root. See [Run Distributed Training](run-distributed-training.md).

## Step 3: Rewrite `azureml.yml`

This is the bulk of the migration. Upstream nested everything under one `azure:` key plus a `docker:` key. This plugin replaces both with three flat sections (`workspace`, `compute`, `execution`) and adds two new ones (`schedules`, `jobs`).

Here is a complete upstream file:

```yaml
# conf/base/azureml.yml -- kedro-azureml v1.0.0
azure:
  subscription_id: "00000000-0000-0000-0000-000000000000"
  resource_group: "rg-dev"
  workspace_name: "aml-dev"
  experiment_name: "my-project"
  environment_name: "my-env@latest"
  code_directory: "."
  working_directory: /home/kedro_docker
  pipeline_data_passing:
    enabled: true
  temporary_storage:
    account_name: "mystorageaccount"
    container: "kedro-temp"
  compute:
    __default__:
      cluster_name: "cpu-cluster"
    gpu-nodes:
      cluster_name: "gpu-cluster"
docker:
  image: null
```

And the same configuration after migration:

```yaml
# conf/base/azureml.yml -- kedro-azureml-pipeline
workspace:
  __default__:
    subscription_id: "00000000-0000-0000-0000-000000000000"
    resource_group: "rg-dev"
    name: "aml-dev"

compute:
  __default__:
    cluster_name: "cpu-cluster"
  gpu-nodes:
    cluster_name: "gpu-cluster"

execution:
  environment: "my-env@latest"
  code_directory: "."
  working_directory: /home/kedro_docker

jobs:
  __default__:
    pipeline:
      pipeline_name: "__default__"
    experiment_name: "my-project"
```

Work through the [configuration key table](../reference/kedro-azureml-mapping.md#configuration-keys) to confirm you have moved everything. Three rows are easy to get wrong:

- **`workspace_name` becomes `name`.** It is the same value, but the key is shorter now that it sits inside a named entry under `workspace`. This is the single most common mistake in this migration.
- **`experiment_name` moves into the job**, and is now optional. A job without one falls back to the experiment name configured in `mlflow.yml`, so projects using [`kedro-mlflow`](use-mlflow.md) can leave it out entirely and keep one source of truth.
- **`docker.image` becomes `execution.environment`**, with no rebuild and no re-registration. A container image URI in that field is detected and wrapped in an Azure ML `Environment` object, so the SDK treats it as an anonymous image rather than as the name of a registered environment. If you set both `environment_name` and `docker.image` upstream, keep the environment name and drop the image.

`temporary_storage` and `pipeline_data_passing` have no replacement; delete them. [Step 7](#step-7-review-what-was-removed) explains what that means for your data.

Tag-based compute routing is unchanged: a node tagged `gpu-nodes` still runs on the cluster under that key. See [Tag-based routing](../reference/configuration.md#tag-based-routing).

## Step 4: Update the catalog

Only the `type:` string changes. Both datasets keep identical constructor parameters, so the rest of each entry stays as it is:

```yaml
# Before
preprocessed_shuttles:
  type: kedro_azureml.datasets.AzureMLPipelineDataset
  dataset:
    type: pandas.ParquetDataset
    filepath: preprocessed_shuttles.pq

# After
preprocessed_shuttles:
  type: kedro_azureml_pipeline.datasets.AzureMLPipelineDataset
  dataset:
    type: pandas.ParquetDataset
    filepath: preprocessed_shuttles.pq
```

The same one-word change applies to [`AzureMLAssetDataset`][kedro_azureml_pipeline.datasets.AzureMLAssetDataset]. A find and replace of `kedro_azureml.datasets` with `kedro_azureml_pipeline.datasets` across `conf/` handles most projects.

If your catalog references `AzureMLFileDataset` or `AzureMLPandasDataset`, those are removed. See [Step 7](#step-7-review-what-was-removed).

## Step 5: Translate your commands

The structural change is that **run configuration moved out of command-line flags and into named jobs**. Upstream, you told `kedro azureml run` what to do with flags. Here, you declare each way of running the project as an entry under `jobs` in `azureml.yml`, and the CLI selects one with `-j`.

That is why so many upstream flags have no direct equivalent: they did not disappear, they became configuration. A command like this:

```bash
kedro azureml run -p training -s 00000000-0000-0000-0000-000000000000
```

becomes a job:

```yaml
jobs:
  training:
    pipeline:
      pipeline_name: "training"
    experiment_name: "my-project"
```

submitted with:

```bash
kedro azureml run -j training
```

The payoff is that the job is now reviewable, version-controlled, and reusable across ad-hoc runs, schedules, and CI. The flags that survive unchanged are the per-invocation ones: `--params`, `--env-var`, `--load-versions`, `--aml-env`, `--wait-for-completion`, and `--on-job-scheduled`. `-e`/`--env` on the command group is unchanged too.

Two replacements are worth memorising:

- `-s`/`--subscription-id` becomes `-w`/`--workspace`, which names an entry under `workspace` rather than passing a bare subscription ID.
- `-p`/`--pipeline` becomes the job's `pipeline.pipeline_name`, selected with `-j`.

The [CLI flag tables](../reference/kedro-azureml-mapping.md#cli-flags) cover `init`, `run`, and `compile` in full.

## Step 6: Verify without touching Azure

Before submitting anything, check that every job in your migrated configuration actually compiles:

```bash
kedro azureml compile --check
```

This resolves every job, compiles each one in memory, writes no files, and exits non-zero if any job fails. It needs no Azure ML credentials and submits nothing, so it is also safe to run in CI on every pull request. See [Compile and inspect](compile-and-inspect.md#validate-that-every-job-compiles).

Combined with the strict validation described in the prerequisites, this is the fastest way to find anything left behind: a stale key fails validation by name, and a mis-migrated job fails to compile.

Then confirm the project still runs locally, which exercises the catalog changes without involving Azure ML at all:

```bash
kedro run
```

Finally, submit a job:

```bash
kedro azureml run -j __default__
```

Add `--dry-run` first if you want to see what would be submitted without creating anything.

## Step 7: Review what was removed

Four things are gone with no replacement.

**Blob storage data passing.** `temporary_storage`, the `--use-pipeline-data-passing` flag, and the `KedroAzureRunnerDataset` and `KedroAzureRunnerDistributedDataset` classes are all removed. Azure ML pipeline data passing is now the only mode.

This is less disruptive than it sounds. **Datasets absent from your catalog still pass between steps automatically.** The runner wraps each one in an [`AzureMLPipelineDataset`][kedro_azureml_pipeline.datasets.AzureMLPipelineDataset] with a pickle backend, which is exactly what upstream did when `pipeline_data_passing.enabled` was `true`. You do not need to add catalog entries for intermediate data that worked before.

What changes is where that data physically lives: Azure ML managed storage rather than the storage account you configured. If you ran upstream with `pipeline_data_passing.enabled: true`, nothing changes at all. Either way, **the storage account and container you created for temporary data can be decommissioned** once no other system depends on them.

**SDK v1 dataset stubs.** `AzureMLFileDataset` and `AzureMLPandasDataset` are removed along with the `v1_datasets` module. Replace catalog entries using them with [`AzureMLAssetDataset`][kedro_azureml_pipeline.datasets.AzureMLAssetDataset], which is the SDK v2 equivalent. See [Use Data Assets](use-data-assets.md).

**The `docker:` section.** Removed as a section; the image URI moves to `execution.environment` as described in [Step 3](#step-3-rewrite-azuremlyml).

**`kedro azureml init` arguments.** The command no longer takes positional arguments or `--aml-env`. It writes `conf/base/azureml.yml` with placeholders for you to fill in. If you are migrating an existing project you already have this file and do not need to run `init` at all.

## What you gain

The rewrite also added capabilities that have no upstream equivalent. None are required to complete the migration, but they are the reason most of the configuration moved:

- **[Named workspaces](configure-multiple-workspaces.md).** Define dev, staging, and production workspaces alongside `__default__`, and target one with `-w`.
- **[Schedules](schedule-pipelines.md).** Declare cron or recurrence triggers in config and deploy them with `kedro azureml schedule -j <job>`. Upstream had no scheduling support.
- **Retry settings.** Give a job a [`retry`](../reference/configuration.md#retry) block to apply `max_retries` and `timeout` to every step.
- **Job-level params.** Set [`params`](../reference/configuration.md#params) on a job so every step receives them, with CLI `--params` taking precedence.
- **[Job factories](define-job-factories.md).** If your pipelines are namespaced per variant, define one templated `jobs` entry and let the concrete jobs be derived from the pipeline namespaces instead of writing near-identical blocks.

## Next steps

- [Schedule Pipelines](schedule-pipelines.md) to replace any external scheduler you used with upstream
- [Define Job Factories](define-job-factories.md) if you have namespaced pipelines
- [Configure Multiple Workspaces](configure-multiple-workspaces.md) to promote across environments
- [Deploy from CI/CD](deploy-from-cicd.md) to run `compile --check` on every pull request
- [Troubleshoot](troubleshoot.md) if a migrated job fails to compile or submit
- [`kedro-azureml` mapping reference](../reference/kedro-azureml-mapping.md) for the complete tables
