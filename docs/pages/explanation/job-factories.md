# Job Factories

A job factory lets you define many similar Azure ML jobs with a single templated
entry, instead of writing one `jobs` block per pipeline variant. If you have a
handful of distinct jobs, write them out. If you have a *family* of jobs that
differ only by a namespace, such as one per region or model, a factory expresses
the whole family at once, and the concrete jobs are derived from your pipelines.

## The dataset-factory analogy

If you have used Kedro [dataset factories](https://docs.kedro.org/en/stable/data/kedro_dataset_factories.html),
you already know the idea. A dataset factory is a catalog entry whose key is a
pattern containing `{placeholder}` markers. Kedro never asks you to list the
concrete datasets, because they are determined by the pipelines: a factory
produces exactly the datasets its nodes reference.

Job factories apply the same principle to Azure ML jobs:

```text
  dataset factory:  catalog pattern  +  node references     ->  datasets
  job factory:      jobs pattern     +  pipeline namespaces  ->  jobs
```

A `jobs` key that contains `{placeholder}` markers is a job factory. The jobs it
produces are derived from the namespaces of its pipeline, so there is no list of
concrete jobs to maintain. Add a namespaced variant to your pipelines and its
jobs appear automatically.

## Connection to dynamic pipelines

Job factories are the deployment counterpart to
[Kedro dynamic pipelines](https://docs.kedro.org/en/stable/build/namespaces/).
A dynamic pipeline is built programmatically, wrapping a reusable pipeline in a
namespace once per variant, so the graph gains one namespaced instance per
variant (for example `europe.forecast`, `america.forecast`). A job factory reads
those namespaces and produces the matching Azure ML job for each.

The two patterns pair up: the dynamic pipeline decides which variants exist, and
the job factory turns each into a job. Adding a variant to the pipeline factory
adds its job with no `azureml.yml` edit, so the two stay in lockstep.

## Jobs are derived from the pipeline namespaces

Each factory declares a `node_namespaces` template. The placeholders in that
template, and their depth, tell the plugin how to read the pipeline's namespaces:

```yaml
jobs:
  "{region}-{model}-training":
    pipeline:
      pipeline_name: training
      node_namespaces: ["{region}.{model}"]   # 2 placeholders, 2 namespace levels
    schedule: nightly
```

Given a `training` pipeline whose nodes are namespaced `europe.lgbm`,
`america.lgbm`, and so on, the plugin reads the first two namespace levels of each
and binds `region` and `model` positionally. Every distinct namespace becomes one
job, named by rendering the factory pattern: `europe-lgbm-training`, and so on.
The namespace alone identifies the job, so no `tags` filter is needed.

## Resolution is forward-only

Names are produced only by rendering placeholders into a pattern; the plugin
never parses a job name back into placeholders. This matters because a
placeholder value can itself contain the `-` separator (a region named
`north-america` is one value), which makes reverse-parsing ambiguous. Going
forward, from placeholders to name, is always unambiguous.

When you run `kedro azureml run -j <name>`, the plugin renders the full set of
derived jobs and looks the name up. An unknown name is an error that lists the
available jobs.

## Most-specific pattern wins

You can add a more-specific factory to override part of a family. When more than
one pattern would render the same name, the one with the most literal
(non-placeholder) characters supplies the configuration:

```yaml
jobs:
  "{region}-{model}-inference":          # general: nightly
    schedule: nightly
    pipeline: {pipeline_name: inference, node_namespaces: ["{region}.{model}"]}

  "america-{model}-inference":           # more specific: hourly for america
    schedule: hourly
    pipeline: {pipeline_name: inference, node_namespaces: ["{region}.{model}"]}
```

Here every region runs nightly except `america`, which runs hourly, with no
override table. This is the same precedence a more-specific dataset factory
pattern has over a general one.

## Multiple schedules on one job

A job's `schedule` accepts a single value or a list. A list deploys one Azure ML
schedule trigger per entry against the same job, so a single inference job can run
on several cron triggers (for example a nightly and a midday run) without being
split into separate jobs.

## Seeing what a factory produces

Because the concrete jobs are derived, they are not written in `azureml.yml`. To
see them, mirroring `kedro catalog resolve-patterns`, use:

```bash
kedro azureml resolve-patterns   # the concrete jobs (names, namespaces, schedules)
kedro azureml list-patterns      # the factory patterns themselves
```

`resolve-patterns` answers the question "what can I pass to `run -j`?".

## When to use a factory

| Use a literal job when... | Use a factory when... |
|---|---|
| you have a few distinct jobs | you have a family of jobs differing only by namespace |
| each job is configured by hand | the set should track the pipelines automatically |
| there is no namespaced variation | adding a variant should not require editing `azureml.yml` |

Literal and factory jobs coexist in the same `jobs` section. A literal key always
takes precedence over a factory that would render the same name.

## See also

- [Configuration Reference](../reference/configuration.md#job-factories): the `jobs` schema and factory fields
- [Define jobs with factories](../how-to/define-job-factories.md): the task-oriented guide
- [Schedule Pipelines](../how-to/schedule-pipelines.md): cron and multi-trigger schedules
- [CLI Reference](../reference/cli.md): `resolve-patterns` and `list-patterns`
