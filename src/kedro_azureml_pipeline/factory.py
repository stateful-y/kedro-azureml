"""Forward-only job factory resolution.

A ``jobs`` key containing ``{token}`` placeholders is a *job factory*: a templated
job entry sharing the config surface of a Kedro dataset factory. Job names are
produced **only** by rendering a *job target* (a complete token binding, e.g.
``{"product": "da-energy", "group": "hub", "variant": "champion", "job": "inference"}``)
into a factory. Names are never reverse-parsed: token contents include the ``-``
separator (``da-energy`` is one product), which makes reverse-parsing ambiguous.

Resolution is therefore forward-only:

* **enumerate** (schedule deploy, listing): render every target into its job.
* **run -j <name>**: render all targets (plus literal jobs) into a name -> job
  map and look the requested name up.

A target's ``job`` value selects the job-type: a factory is a candidate only if
its rendered name ends at that ``job`` value (so an ``inference`` target never
matches an ``inference-930`` factory). Among candidates, the most-general factory
(most tokens) defines the canonical name; factories that render to that same name
are applicable, and the most-specific one (most literal characters) supplies the
config.
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from kedro_azureml_pipeline.config.models import JobConfig

if TYPE_CHECKING:
    from kedro_azureml_pipeline.config.models import KedroAzureMLConfig

JOB_TARGET_PROVIDERS_GROUP = "kedro_azureml_pipeline.job_target_providers"


@dataclass(frozen=True)
class ProviderContext:
    """Inputs handed to a job-target provider.

    Parameters
    ----------
    config : KedroAzureMLConfig
        The loaded plugin configuration.
    env : str or None
        The active Kedro environment, when known.
    pipelines : Mapping[str, Any] or None
        The Kedro pipeline registry (name -> Pipeline), used by the default
        ``pipelines`` provider to derive bindings from node namespaces. When
        ``None`` the provider falls back to ``kedro.framework.project.pipelines``.
    """

    config: KedroAzureMLConfig
    env: str | None = None
    pipelines: Mapping[str, Any] | None = None


@runtime_checkable
class JobTargetProvider(Protocol):
    """Yields the set of job targets that exist.

    A target is a flat mapping of token name to value, including a ``job`` key
    naming the job-type (e.g. ``inference``, ``inference-930``). The provider
    yields targets, not job configurations; each target is rendered through its
    matching job factory by :func:`enumerate_jobs`.
    """

    def targets(self, context: ProviderContext) -> list[dict[str, str]]:
        """Return the list of job targets."""
        ...


class InlineTargetProvider:
    """Escape-hatch provider: returns the literal ``job_targets`` list from config."""

    def targets(self, context: ProviderContext) -> list[dict[str, str]]:
        return [dict(t) for t in context.config.job_targets]


def _job_suffix(factory_key: str) -> str:
    """Job-type suffix of a factory key (literal text after the last ``{token}``)."""
    return factory_key.rsplit("}", 1)[-1].lstrip("-")


def _ordered_tokens(template: str) -> list[str]:
    """Field names in *template*, in order of appearance."""
    return [name for _, name, _, _ in string.Formatter().parse(template) if name]


def _bind_namespace(template: str, namespace: str) -> dict[str, str] | None:
    """Bind a node *namespace* to the tokens of a `node_namespaces` *template*.

    The template and namespace are split on ``.``; each template segment is either
    a single ``{token}`` (bound to the namespace part) or a literal (which must
    match the namespace part). Returns the binding, or ``None`` if the namespace is
    shallower than the template or a literal segment does not match.
    """
    segments = template.split(".")
    parts = namespace.split(".")
    if len(parts) < len(segments):
        return None
    binding: dict[str, str] = {}
    for segment, part in zip(segments, parts, strict=False):  # parts may be deeper than the template
        tokens = _ordered_tokens(segment)
        if tokens:
            binding[tokens[0]] = part
        elif segment != part:
            return None
    return binding


class PipelinesTargetProvider:
    """Default provider: derive bindings from the Kedro pipeline namespaces.

    For each job factory, the factory's ``pipeline.node_namespaces`` template
    defines the binding tokens and their namespace depth; the distinct namespaces
    of the factory's ``pipeline_name`` pipeline at that depth become the bindings.
    Each binding is tagged with the factory's job-type suffix so the existing
    resolver can select the most-specific factory. This is the job analogue of a
    dataset factory taking its demand from pipeline node references.
    """

    def targets(self, context: ProviderContext) -> list[dict[str, str]]:
        pipelines = context.pipelines
        if pipelines is None:
            from kedro.framework.project import pipelines  # noqa: PLC0415

        targets: list[dict[str, str]] = []
        seen: set[frozenset[tuple[str, str]]] = set()
        for key, job in context.config.jobs.items():
            if not is_factory(key):
                continue
            templates = job.pipeline.node_namespaces or []
            pipe = pipelines.get(job.pipeline.pipeline_name)
            if not templates or pipe is None:
                continue
            template = templates[0]
            # Every token in the factory name must be bindable from the namespace
            # template; otherwise the rendered name would keep an unfilled `{token}`.
            unbindable = _tokens(key) - _tokens(template)
            if unbindable:
                raise ValueError(
                    f"Job factory '{key}' references token(s) {sorted(unbindable)} "
                    f"absent from its node_namespaces template '{template}'."
                )
            depth = len(template.split("."))
            namespaces = {
                ".".join(node.namespace.split(".")[:depth])
                for node in pipe.nodes
                if node.namespace and len(node.namespace.split(".")) >= depth
            }
            suffix = _job_suffix(key)
            for namespace in namespaces:
                binding = _bind_namespace(template, namespace)
                if binding is None:
                    continue
                binding["job"] = suffix
                marker = frozenset(binding.items())
                if marker in seen:
                    continue
                seen.add(marker)
                targets.append(binding)
        return targets


_BUILTIN_PROVIDERS: dict[str, type] = {
    "pipelines": PipelinesTargetProvider,
    "inline": InlineTargetProvider,
}


def _available_providers() -> list[str]:
    names = set(_BUILTIN_PROVIDERS)
    names.update(ep.name for ep in entry_points(group=JOB_TARGET_PROVIDERS_GROUP))
    return sorted(names)


def load_target_provider(name: str) -> JobTargetProvider:
    """Resolve a job-target provider by name.

    Entry points registered under ``kedro_azureml_pipeline.job_target_providers``
    take precedence over the built-in ``inline`` provider, allowing a project to
    override or extend the set (for example, a topology-sourced provider).
    """
    for ep in entry_points(group=JOB_TARGET_PROVIDERS_GROUP):
        if ep.name == name:
            return ep.load()()
    if name in _BUILTIN_PROVIDERS:
        return _BUILTIN_PROVIDERS[name]()
    raise ValueError(f"Unknown job-target provider '{name}'. Registered providers: {', '.join(_available_providers())}")


def is_factory(key: str) -> bool:
    """True if a ``jobs`` key is a factory (contains ``{token}`` placeholders)."""
    return "{" in key and "}" in key


def _tokens(template: str) -> set[str]:
    """Field names referenced by ``{token}`` placeholders in *template*."""
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def _literal_len(template: str) -> int:
    """Total length of the literal (non-placeholder) text in *template*."""
    return sum(len(literal or "") for literal, _, _, _ in string.Formatter().parse(template))


def _render_str(value: str, tokens: dict[str, str]) -> str:
    """``str.format`` *value* with *tokens*, but only if it has a fillable field.

    Strings without ``{...}`` are returned unchanged. A leftover OmegaConf
    ``${...}`` (already resolved at config-load time, so not expected here) or any
    unknown field is tolerated by returning the original string rather than
    raising, so ``{token}`` rendering never disturbs other interpolation syntax.
    """
    if "{" not in value or "}" not in value:
        return value
    try:
        return value.format(**tokens)
    except (KeyError, IndexError, ValueError):
        return value


def _render_value(value: Any, tokens: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _render_str(value, tokens)
    if isinstance(value, list):
        return [_render_value(item, tokens) for item in value]
    if isinstance(value, dict):
        return {key: _render_value(item, tokens) for key, item in value.items()}
    return value


def _render_job(config: JobConfig | dict[str, Any], tokens: dict[str, str]) -> JobConfig:
    """Render a factory's job config (``JobConfig`` or raw dict) by interpolating leaves."""
    data = config.model_dump(exclude_none=True) if isinstance(config, JobConfig) else config
    return JobConfig.model_validate(_render_value(data, tokens))


def _matches_job_type(name: str, job: str | None) -> bool:
    """True if rendered *name* ends at the target's *job* value on a separator."""
    if job is None:
        return True
    return name == job or name.endswith(f"-{job}")


def resolve_target(target: dict[str, str], factories: dict[str, JobConfig]) -> tuple[str, JobConfig] | None:
    """Render a single target into its ``(name, JobConfig)``, or ``None``.

    Selects the most-specific factory consistent with the target (see module
    docstring). Returns ``None`` if no factory is consistent with the target.
    """
    target_keys = set(target)
    job = target.get("job")

    candidates: list[tuple[str, JobConfig, str, int, int]] = []
    for key, config in factories.items():
        toks = _tokens(key)
        if not toks <= target_keys:
            continue
        name = _render_str(key, target)
        if "{" in name:  # unfilled placeholder remained
            continue
        if not _matches_job_type(name, job):
            continue
        candidates.append((key, config, name, _literal_len(key), len(toks)))

    if not candidates:
        return None

    # The canonical name is set by the most-general candidate (most tokens; ties
    # broken by most literal characters, then key) so it reflects every token of
    # the target (e.g. the full product, not a specific factory's literal prefix).
    canonical = max(candidates, key=lambda c: (c[4], c[3], c[0]))[2]
    applicable = [c for c in candidates if c[2] == canonical]
    # Most-specific applicable factory (most literal characters) supplies the body.
    key, config, name, *_ = max(applicable, key=lambda c: (c[3], c[0]))
    return name, _render_job(config, target)


def _effective_provider(config: KedroAzureMLConfig) -> str:
    """Provider name to use: an inline ``job_targets`` list overrides the default."""
    if config.job_targets:
        return "inline"
    return config.job_target_provider


def enumerate_jobs(
    config: KedroAzureMLConfig, env: str | None = None, pipelines: Mapping[str, Any] | None = None
) -> dict[str, JobConfig]:
    """Render every job target into a ``name -> JobConfig`` map, overlaying literals.

    Bindings come from the configured target provider — by default the
    ``pipelines`` provider, which derives them from the Kedro pipeline namespaces;
    a non-empty ``job_targets`` list overrides it with the ``inline`` provider.
    Literal (non-factory) ``jobs`` entries are included and take precedence over
    rendered targets on a name collision. Raises if a target matches no factory or
    if two targets render to the same name.
    """
    factories = {k: v for k, v in config.jobs.items() if is_factory(k)}
    literals = {k: v for k, v in config.jobs.items() if not is_factory(k)}

    provider = load_target_provider(_effective_provider(config))
    targets = provider.targets(ProviderContext(config=config, env=env, pipelines=pipelines))

    rendered: dict[str, JobConfig] = {}
    for target in targets:
        result = resolve_target(target, factories)
        if result is None:
            raise ValueError(f"No job factory matches target {target!r}.")
        name, job_config = result
        if name in rendered:
            raise ValueError(f"Two job targets render to the same job name '{name}'.")
        rendered[name] = job_config

    rendered.update(literals)  # literal jobs win over rendered ones
    return rendered


def resolve_jobs(
    config: KedroAzureMLConfig, job_names: list[str], env: str | None = None, pipelines: Mapping[str, Any] | None = None
) -> dict[str, JobConfig]:
    """Resolve requested job names to ``JobConfig`` via literals + rendered targets.

    Literal jobs are matched directly; any remaining name is looked up in the
    enumerated job set (see :func:`enumerate_jobs`). Raises a ``ValueError`` listing
    available names if a requested name is neither a literal job nor a rendered one.
    """
    literals = {k: v for k, v in config.jobs.items() if not is_factory(k)}
    has_factories = any(is_factory(k) for k in config.jobs)

    resolved: dict[str, JobConfig] = {}
    all_jobs: dict[str, JobConfig] | None = None
    missing: list[str] = []
    for name in job_names:
        if name in literals:
            resolved[name] = literals[name]
            continue
        if not has_factories:  # nothing to render; only literal jobs exist
            missing.append(name)
            continue
        if all_jobs is None:
            all_jobs = enumerate_jobs(config, env, pipelines)
        if name in all_jobs:
            resolved[name] = all_jobs[name]
        else:
            missing.append(name)

    if missing:
        available = sorted(all_jobs if all_jobs is not None else literals)
        raise ValueError(
            f"Job(s) not found in config: {', '.join(sorted(missing))}. Available jobs: {', '.join(available)}"
        )
    return resolved
