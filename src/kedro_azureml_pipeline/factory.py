"""Forward-only job factory resolution.

A ``jobs`` key containing ``{token}`` placeholders is a *job factory*: a templated
job entry sharing the config surface of a Kedro dataset factory. The concrete jobs
are derived from the **Kedro pipeline namespaces** — for each factory, its
``node_namespaces`` template defines the binding tokens and their namespace depth,
and the distinct namespaces of its pipeline become the jobs (the job analogue of a
dataset factory taking its demand from pipeline node references).

Names are produced only by rendering bindings forward; they are never
reverse-parsed (token contents include the ``-`` separator, e.g. ``da_energy``,
which would make reverse-parsing ambiguous).

* **enumerate** (schedule deploy, listing): render every binding into its job.
* **run -j <name>**: render all bindings (plus literal jobs) into a name -> job
  map and look the requested name up.

Each binding is tagged with its factory's job-type suffix, so an ``inference``
binding never matches an ``inference-930`` factory. When several factories render
the same name, the most-specific one (most literal characters) supplies the config.
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from kedro_azureml_pipeline.config.models import JobConfig

if TYPE_CHECKING:
    from kedro_azureml_pipeline.config.models import KedroAzureMLConfig


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


def _derive_bindings(config: KedroAzureMLConfig, pipelines: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Derive job bindings from each factory's pipeline namespaces.

    For each job factory, the factory's ``pipeline.node_namespaces`` template
    defines the binding tokens and their namespace depth; the distinct namespaces
    of the factory's ``pipeline_name`` pipeline at that depth become the bindings,
    each tagged with the factory's job-type suffix. *pipelines* is the Kedro
    pipeline registry; when ``None`` the global registry is used.
    """
    if pipelines is None:
        from kedro.framework.project import pipelines  # noqa: PLC0415

    bindings: list[dict[str, str]] = []
    seen: set[frozenset[tuple[str, str]]] = set()
    for key, job in config.jobs.items():
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
            bindings.append(binding)
    return bindings


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
    """Recursively interpolate string leaves of *value* (str/list/dict) with *tokens*."""
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


def enumerate_jobs(config: KedroAzureMLConfig, pipelines: Mapping[str, Any] | None = None) -> dict[str, JobConfig]:
    """Render every derived binding into a ``name -> JobConfig`` map, overlaying literals.

    Bindings are derived from the Kedro pipeline namespaces (see
    :func:`_derive_bindings`). Literal (non-factory) ``jobs`` entries are included
    and take precedence over rendered ones on a name collision.
    """
    factories = {k: v for k, v in config.jobs.items() if is_factory(k)}
    literals = {k: v for k, v in config.jobs.items() if not is_factory(k)}

    rendered: dict[str, JobConfig] = {}
    for binding in _derive_bindings(config, pipelines):
        result = resolve_target(binding, factories)
        if result is None:  # pragma: no cover - defensive; a derived binding always has its own factory
            raise ValueError(f"No job factory matches binding {binding!r}.")
        name, job_config = result
        if name in rendered:  # pragma: no cover - defensive; bindings are de-duplicated before rendering
            raise ValueError(f"Two bindings render to the same job name '{name}'.")
        rendered[name] = job_config

    rendered.update(literals)  # literal jobs win over rendered ones
    return rendered


def resolve_jobs(
    config: KedroAzureMLConfig, job_names: list[str], pipelines: Mapping[str, Any] | None = None
) -> dict[str, JobConfig]:
    """Resolve requested job names to ``JobConfig`` via literals + rendered bindings.

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
            all_jobs = enumerate_jobs(config, pipelines)
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
