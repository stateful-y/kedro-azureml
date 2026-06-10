# How to Define Jobs with Factories

This guide shows how to define a *family* of Azure ML jobs with a single
templated entry, a job factory, instead of writing one `jobs` block per pipeline
variant. Reach for this when your pipelines are namespaced per variant (the
[dynamic pipelines](https://docs.kedro.org/en/stable/build/namespaces/) pattern)
and you want the job set to track them automatically. For the background, see
[Job Factories](../explanation/job-factories.md).

## Prerequisites

- A Kedro pipeline whose nodes are namespaced (for example `europe.lgbm`)
- The target workspace, compute, and environment configured in `azureml.yml`

## 1. Write a factory pattern

Give a `jobs` entry a key containing `{placeholder}` markers, and a
`node_namespaces` template with the same placeholders at the depth of your
pipeline's namespaces:

```yaml
jobs:
  "{region}-{model}-inference":
    pipeline:
      pipeline_name: inference
      node_namespaces: ["{region}.{model}"]   # 2 placeholders, 2 namespace levels
    schedule: nightly
    experiment_name: forecasting
    display_name: "{region}-{model}"
```

The plugin reads the first two namespace levels of each node in the `inference`
pipeline and produces one job per distinct `(region, model)`, rendering the
pattern into a concrete name such as `europe-lgbm-inference`. Every string field
in the entry is interpolated with the same placeholders. No `tags` are needed:
the namespace identifies the job.

!!! tip "Placeholder names are your choice"
    `region` and `model` are just the names used here. Use whatever matches your
    namespace structure; the template's placeholder count sets the depth.

## 2. Override part of the family with a more-specific pattern

To give one slice of the family different settings, add a pattern with more
literal characters in its key. When two patterns would render the same name, the
most-specific one wins:

```yaml
jobs:
  "{region}-{model}-inference":        # general
    schedule: nightly
    pipeline: {pipeline_name: inference, node_namespaces: ["{region}.{model}"]}

  "america-{model}-inference":         # wins for the america region
    schedule: hourly
    pipeline: {pipeline_name: inference, node_namespaces: ["{region}.{model}"]}
```

Here every region runs nightly except `america`, which runs hourly, with no
override table.

## 3. Keep literal jobs alongside factories

Cross-cutting jobs that are not part of a family (a one-off snapshot, a validation
job) stay as literal `jobs` entries. A literal key always takes precedence over a
factory that would render the same name:

```yaml
jobs:
  "{region}-{model}-training": { ... }   # factory
  snapshot:                               # literal, kept verbatim
    pipeline: {pipeline_name: snapshot}
```

## 4. Verify the derived jobs

Because the concrete jobs are derived, they are not written in `azureml.yml`. List
what the factories resolve to:

```bash
kedro azureml resolve-patterns         # the concrete job names (with namespaces, schedules)
kedro azureml list-patterns            # the factory patterns themselves
```

Use the environment flag to check a specific environment, for example
`kedro azureml -e prod resolve-patterns`. The names printed by `resolve-patterns`
are exactly what you pass to `run` and `schedule`:

```bash
kedro azureml run -j europe-lgbm-inference
```

## See also

- [Job Factories](../explanation/job-factories.md): the concept and the dataset-factory analogy
- [Configuration reference](../reference/configuration.md#job-factories): the `jobs` schema
- [Schedule Pipelines](schedule-pipelines.md): cron, recurrence, and multiple triggers per job
- [Compile and Inspect](compile-and-inspect.md): inspecting and debugging job definitions
