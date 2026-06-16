# CLI Reference

All commands are available under `kedro azureml`. Run `kedro azureml --help` to list them.

---

## `kedro azureml init`

```text
kedro azureml init
```

Initializes the plugin in the current Kedro project. Creates:

- `conf/base/azureml.yml`: configuration file with placeholder values
- `.amlignore`: file exclusion list for code upload

No flags.

---

## `kedro azureml compile`

```text
kedro azureml compile (-j JOB_NAME | --all) [options]
```

Compiles job(s) into Azure ML pipeline YAML definitions without submitting them. Select jobs with one or more `-j` names, or compile every resolved job with `--all`. Add `--check` to validate that jobs compile without writing any files. See [Compile and inspect](../how-to/compile-and-inspect.md) for a walkthrough.

| Flag | Description |
|---|---|
| `-j JOB_NAME` | Job name from `jobs` in `azureml.yml`. Required unless `--all` is given (provide one of `-j` or `--all`). Repeatable for multiple jobs. |
| `--all` | Compile every resolved job, both literal and factory-derived. Mutually exclusive with `-j`. |
| `--check` | Compile in memory without writing any output, and exit non-zero if any job fails to compile. Needs no Azure credentials and submits nothing, so it is suitable as a CI gate. Combine with `--all` to validate the whole config. |
| `-o OUTPUT` | Output YAML file path (default: `pipeline.yaml`). Ignored with `--check`. |
| `--aml-env ENV` | Override the Azure ML environment for this invocation |
| `--params JSON` | Runtime parameters as a JSON string (e.g. `'{"key": "value"}'`) |
| `--env-var KEY=VALUE` | Inject an environment variable into pipeline steps. Repeatable. |
| `--load-versions KEY:VERSION` | Pin a dataset to a specific Kedro-versioned version. Repeatable. |

---

## `kedro azureml run`

```text
kedro azureml run -j JOB_NAME [options]
```

Submits named job(s) to Azure ML managed compute immediately, ignoring any configured schedule.

| Flag | Description |
|---|---|
| `-j JOB_NAME` | Job name from `jobs` in `azureml.yml`. Required. Repeatable for multiple jobs. |
| `--dry-run` | Preview the pipeline definition without submitting to Azure ML |
| `--wait-for-completion` | Block the terminal until the run finishes |
| `-w WORKSPACE` | Override the workspace for all jobs in this batch |
| `--aml-env ENV` | Override the Azure ML environment for this invocation |
| `--params JSON` | Runtime parameters as a JSON string |
| `--env-var KEY=VALUE` | Inject an environment variable into pipeline steps. Repeatable. |
| `--load-versions KEY:VERSION` | Pin a dataset to a specific Kedro-versioned version. Repeatable. |
| `--on-job-scheduled MODULE:FUNC` | Callback invoked after each job is submitted (e.g. `mymodule:notify`) |

---

## `kedro azureml schedule`

```text
kedro azureml schedule -j JOB_NAME [options]
```

Creates or updates persistent Azure ML schedules for named job(s). Every selected job must have a `schedule` configured in `azureml.yml`.

| Flag | Description |
|---|---|
| `-j JOB_NAME` | Job name from `jobs` in `azureml.yml`. Required. Repeatable for multiple jobs. |
| `--dry-run` | Preview the schedule definition without creating it in Azure ML |
| `-w WORKSPACE` | Override the workspace for all jobs in this batch |
| `--aml-env ENV` | Override the Azure ML environment for this invocation |
| `--params JSON` | Runtime parameters as a JSON string |
| `--env-var KEY=VALUE` | Inject an environment variable into pipeline steps. Repeatable. |
| `--load-versions KEY:VERSION` | Pin a dataset to a specific Kedro-versioned version. Repeatable. |

---

## `kedro azureml resolve-patterns`

```text
kedro azureml resolve-patterns
```

Lists the concrete jobs derived from the [job factories](configuration.md#job-factories) and the pipeline namespaces (the job analogue of `kedro catalog resolve-patterns`). For each job it prints the name, pipeline, node namespaces, and schedule; literal (non-factory) jobs are included. No Azure ML connection is made. Add `-e ENV` to inspect a specific environment.

The names printed here are exactly what you pass to `-j` on `run`, `schedule`, and `compile`.

---

## `kedro azureml list-patterns`

```text
kedro azureml list-patterns
```

Lists the job-factory patterns, the `jobs` keys containing `{placeholder}` markers (the analogue of `kedro catalog list-patterns`). Literal job keys are not listed; use `resolve-patterns` to see the concrete jobs the factories produce.

---

## `kedro azureml execute`

Used internally by Azure ML pipeline steps to run individual Kedro nodes on compute. Not intended for direct use. You may see this command in Azure ML step logs when inspecting pipeline run output in Azure ML Studio.

---

## Common flags

The following flags are accepted by `compile`, `run`, and `schedule`:

| Flag | Description |
|---|---|
| `--params JSON` | Runtime parameters as a JSON string |
| `--env-var KEY=VALUE` | Inject environment variables into pipeline steps (repeatable) |
| `--load-versions KEY:VERSION` | Dataset version overrides (repeatable) |
| `--aml-env ENV` | Override the Azure ML environment |
