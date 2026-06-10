# Job Factories

A job factory lets you define many similar Azure ML jobs with a single templated
entry, instead of writing one `jobs` block per pipeline variant. If you have a
handful of distinct jobs, write them literally. If you have a *family* of jobs
that differ only by a namespace — one per product, group, or model variant — a
factory expresses the whole family at once, and the concrete jobs are derived
from your pipelines.

## The dataset-factory analogy

If you have used Kedro [dataset factories](https://docs.kedro.org/en/stable/data/kedro_dataset_factories.html),
you already know the idea. A dataset factory is a catalog entry whose key is a
pattern; Kedro never asks you to list the concrete datasets, because the *demand*
comes from the pipelines — the datasets a factory produces are exactly those its
nodes reference.

Job factories apply the same principle to Azure ML jobs:

```text
  dataset factory:  catalog pattern  +  node references     ⟶  datasets
  job factory:      jobs pattern     +  pipeline namespaces  ⟶  jobs
```

A `jobs` key that contains `{token}` placeholders is a job factory. The jobs it
produces are derived from the **namespaces of its pipeline** — there is no list
of concrete jobs to maintain. Add a namespaced variant to your pipelines and its
jobs appear automatically.

## Demand comes from the pipeline namespaces

Each factory declares a `node_namespaces` template. The tokens in that template,
and their depth, tell the plugin how to read the pipeline's namespaces:

```yaml
jobs:
  "{product}-{group}-{variant}-inference":
    pipeline:
      pipeline_name: inference
      node_namespaces: ["{product}.{group}.{variant}"]   # 3 tokens, 3 namespace levels
    schedule: da-vintages
```

Given an `inference` pipeline whose nodes are namespaced
`da_energy.hub.champion`, `rt_energy.hub.champion`, … the plugin reads the first
three namespace levels of each and binds `product`, `group`, `variant`
positionally. Every distinct namespace becomes one job, named by rendering the
factory key: `da_energy-hub-champion-inference`, and so on.

Because the variant is itself a namespace level, no `tags` filter is needed — the
namespace alone identifies the job.

## Resolution is forward-only

Names are produced *only* by rendering tokens into a factory; the plugin never
parses a job name back into tokens. This matters because token values can contain
the `-` separator (`da_energy` is one product), which makes reverse-parsing
ambiguous. Going forward — tokens to name — is always unambiguous.

When you run `kedro azureml run -j <name>`, the plugin renders the full set of
derived jobs and looks the name up; an unknown name is an error that lists the
available jobs.

## Most-specific factory wins

You can add a more-specific factory to override part of a family. When more than
one factory would render the same name, the one with the most literal
(non-token) characters supplies the configuration:

```yaml
jobs:
  "{product}-{group}-{variant}-inference":          # general: day-ahead vintages
    schedule: [da-vintages, da-vintage-930]
    pipeline: {pipeline_name: inference, node_namespaces: ["{product}.{group}.{variant}"]}

  "rt_energy-{group}-{variant}-inference":          # more specific: hourly for rt_energy
    schedule: rt-hourly
    pipeline: {pipeline_name: inference, node_namespaces: ["{product}.{group}.{variant}"]}
```

This expresses "everything runs on the day-ahead vintages, except `rt_energy`,
which runs hourly" without an override table — the same way a more-specific
dataset factory pattern overrides a general one.

## Multiple schedules on one job

A job's `schedule` accepts a single value or a list. A list deploys one Azure ML
schedule trigger per entry against the same job, so a single inference job can run
on several cron triggers (e.g. a day-ahead and a 9:30 vintage) rather than being
split into separate jobs.

## Seeing what a factory produces

Because the concrete jobs are derived, they are not written in `azureml.yml`. To
see them — mirroring `kedro catalog resolve-patterns` — use:

```bash
kedro azureml resolve-patterns   # the concrete jobs (names, namespaces, schedules)
kedro azureml list-patterns      # the factory keys themselves
```

`resolve-patterns` is the answer to "what can I pass to `run -j`?".

## When to use a factory

| Use a literal job when… | Use a factory when… |
|---|---|
| you have a few distinct jobs | you have a family of jobs differing only by namespace |
| each job is configured by hand | the set should track the pipelines automatically |
| there is no namespaced variation | adding a variant should not require editing `azureml.yml` |

Literal and factory jobs coexist in the same `jobs` section; a literal key always
takes precedence over a factory that would render the same name.

## See also

- [Configuration Reference](../reference/configuration.md#job-factories): the `jobs` schema and factory fields
- [Define jobs with factories](../how-to/define-job-factories.md): the task-oriented guide
- [Schedule Pipelines](../how-to/schedule-pipelines.md): cron and multi-trigger schedules
- [CLI Reference](../reference/cli.md): `resolve-patterns` and `list-patterns`
