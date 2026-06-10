# How to Define Jobs with Factories

This guide shows how to define a *family* of Azure ML jobs with a single
templated entry — a job factory — instead of writing one `jobs` block per
pipeline variant. Reach for this when your pipelines are namespaced per product,
group, or model variant and you want the job set to track them automatically. For
the background, see [Job Factories](../explanation/job-factories.md).

## Prerequisites

- A Kedro pipeline whose nodes are namespaced (e.g. `da_energy.hub.champion`)
- The target workspace, compute, and environment configured in `azureml.yml`

## 1. Write a factory keyed by tokens

Give a `jobs` entry a key containing `{token}` placeholders, and a
`node_namespaces` template with the same tokens at the depth of your pipeline's
namespaces:

```yaml
jobs:
  "{product}-{group}-{variant}-inference":
    pipeline:
      pipeline_name: inference
      node_namespaces: ["{product}.{group}.{variant}"]   # 3 tokens, 3 namespace levels
    schedule: da-vintages
    experiment_name: prod-inference
    display_name: "{product}-{group}-{variant}"
```

The plugin reads the first three namespace levels of each node in the `inference`
pipeline and produces one job per distinct `(product, group, variant)`, rendering
the key into a concrete name like `da_energy-hub-champion-inference`. Every string
field in the entry is interpolated with the same tokens. No `tags` are needed —
the namespace identifies the job.

!!! tip "Token names are your choice"
    `product`/`group`/`variant` are just the token names used here. Use whatever
    matches your namespace structure; the template's token count sets the depth.

## 2. Override part of the family with a more-specific factory

To give one slice of the family different settings, add a factory with more
literal characters in its key. When two factories would render the same name, the
most-specific one wins:

```yaml
jobs:
  "{product}-{group}-{variant}-inference":        # general
    schedule: [da-vintages, da-vintage-930]
    pipeline: {pipeline_name: inference, node_namespaces: ["{product}.{group}.{variant}"]}

  "rt_energy-{group}-{variant}-inference":        # wins for rt_energy
    schedule: rt-hourly
    pipeline: {pipeline_name: inference, node_namespaces: ["{product}.{group}.{variant}"]}
```

Here every product runs on the day-ahead vintages except `rt_energy`, which runs
hourly — no override table required.

## 3. Keep literal jobs alongside factories

Cross-cutting jobs that are not part of a family (a one-off snapshot, a validation
job) stay as literal `jobs` entries. A literal key always takes precedence over a
factory that would render the same name:

```yaml
jobs:
  "{product}-{group}-{variant}-training": { ... }   # factory
  snapshot:                                          # literal — kept verbatim
    pipeline: {pipeline_name: snapshot}
```

## 4. Verify the derived jobs

Because the concrete jobs are derived, they are not written in `azureml.yml`. List
what the factories resolve to:

```bash
kedro azureml resolve-patterns         # the concrete job names (+ namespaces, schedules)
kedro azureml list-patterns            # the factory keys themselves
```

Use the environment flag to check a specific environment, e.g.
`kedro azureml -e prod resolve-patterns`. The names printed by `resolve-patterns`
are exactly what you pass to `run` and `schedule`:

```bash
kedro azureml run -j da_energy-hub-champion-inference
```

## See also

- [Job Factories](../explanation/job-factories.md): the concept and the dataset-factory analogy
- [Configuration reference](../reference/configuration.md#job-factories): the `jobs` schema
- [Schedule Pipelines](schedule-pipelines.md): cron, recurrence, and multiple triggers per job
- [Compile and Inspect](compile-and-inspect.md): inspecting and debugging job definitions
