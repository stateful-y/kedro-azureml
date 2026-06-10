"""Tests for forward-only job factory resolution (pipeline-derived bindings)."""

from types import SimpleNamespace

import pytest

from kedro_azureml_pipeline.config.models import KedroAzureMLConfig
from kedro_azureml_pipeline.factory import (
    _bind_namespace,
    _job_suffix,
    _matches_job_type,
    _render_job,
    _render_str,
    enumerate_jobs,
    is_factory,
    resolve_jobs,
    resolve_target,
)

_WS = {"__default__": {"subscription_id": "s", "resource_group": "r", "name": "w"}}
_COMPUTE = {"__default__": {"cluster_name": "c"}}

_NS4 = ["da_energy.hub.champion", "da_energy.hub.challenger", "rt_energy.hub.champion", "rt_energy.hub.challenger"]


def _pipe(*namespaces):
    """A stand-in Kedro pipeline: an object with ``.nodes``, each with ``.namespace``."""
    return SimpleNamespace(nodes=[SimpleNamespace(namespace=ns) for ns in namespaces])


def _config(jobs):
    return KedroAzureMLConfig.model_validate({"workspace": _WS, "compute": _COMPUTE, "jobs": jobs})


def _factories(jobs):
    """Build a config and return its factory entries as validated JobConfig values."""
    return {k: v for k, v in _config(jobs).jobs.items() if is_factory(k)}


def _inference(schedule="da-vintages"):
    return {
        "schedule": schedule,
        "pipeline": {"pipeline_name": "inference", "node_namespaces": ["{product}.{group}.{variant}"]},
    }


def _base_jobs():
    return {
        "{product}-{group}-{variant}-training": {
            "schedule": "weekly-monday",
            "pipeline": {"pipeline_name": "training", "node_namespaces": ["{product}.{group}.{variant}"]},
        },
        "{product}-{group}-{variant}-inference": _inference(["da-vintages", "da-vintage-930"]),
        "rt_energy-{group}-{variant}-inference": _inference("rt-hourly"),
    }


_PIPES = {"training": _pipe(*_NS4), "inference": _pipe(*_NS4)}

# A complete binding (incl. job-type) as produced by _derive_bindings.
DA = {"product": "da_energy", "group": "hub", "variant": "champion", "job": "inference"}
RT = {"product": "rt_energy", "group": "hub", "variant": "champion", "job": "inference"}


# --- small helpers -----------------------------------------------------------


def test_is_factory():
    assert is_factory("{product}-{group}-inference")
    assert not is_factory("snapshot")


def test_job_suffix():
    assert _job_suffix("{product}-{group}-{variant}-inference") == "inference"
    assert _job_suffix("{product}-{group}-{variant}-inference-930") == "inference-930"
    assert _job_suffix("rt_energy-{group}-{variant}-inference") == "inference"


def test_bind_namespace():
    assert _bind_namespace("{product}.{group}.{variant}", "da_energy.hub.champion") == {
        "product": "da_energy",
        "group": "hub",
        "variant": "champion",
    }
    assert _bind_namespace("{product}.{group}.{variant}", "da_energy.hub") is None  # too shallow
    assert _bind_namespace("rt_energy.{group}", "rt_energy.hub") == {"group": "hub"}  # literal match
    assert _bind_namespace("rt_energy.{group}", "da_energy.hub") is None  # literal mismatch


def test_render_str_tolerates_unknown_and_passthrough():
    assert _render_str("plain", {"a": "b"}) == "plain"
    assert _render_str("{variant}", {"variant": "champion"}) == "champion"
    assert _render_str("${oc.env:FOO}", {"variant": "x"}) == "${oc.env:FOO}"  # not a {token}
    assert _render_str("{missing}", {"variant": "x"}) == "{missing}"  # KeyError tolerated


def test_render_job_accepts_dict_and_jobconfig():
    job = _render_job({"pipeline": {"pipeline_name": "p", "tags": ["{variant}"]}}, {"variant": "champion"})
    assert job.pipeline.tags == ["champion"]


def test_render_job_passes_through_non_string_leaves():
    job = _render_job({"pipeline": {"pipeline_name": "p"}, "retry": {"max_retries": 3, "timeout": 3600}}, {})
    assert job.retry.max_retries == 3  # int leaves are passed through unchanged


def test_matches_job_type():
    assert _matches_job_type("x-inference", None) is True  # no job constraint
    assert _matches_job_type("x-inference", "inference") is True
    assert _matches_job_type("x-inference-930", "inference") is False
    assert _matches_job_type("inference", "inference") is True  # exact


# --- resolve_target (the forward engine) -------------------------------------


def test_resolve_target_renders_forward():
    factories = _factories({"{product}-{group}-{variant}-inference": _inference()})
    name, job = resolve_target(DA, factories)
    assert name == "da_energy-hub-champion-inference"
    assert job.pipeline.node_namespaces == ["da_energy.hub.champion"]


def test_resolve_target_most_specific_wins():
    factories = _factories({
        "{product}-{group}-{variant}-inference": _inference("da-vintages"),
        "rt_energy-{group}-{variant}-inference": _inference("rt-hourly"),
    })
    _, job = resolve_target(RT, factories)
    assert job.schedule == "rt-hourly"  # rt factory is more specific
    _, job = resolve_target(DA, factories)
    assert job.schedule == "da-vintages"  # rt render != canonical -> excluded


def test_resolve_target_job_type_not_collapsed():
    factories = _factories({
        "{product}-{group}-{variant}-inference": _inference("da-vintages"),
        "{product}-{group}-{variant}-inference-930": _inference("da-vintage-930"),
    })
    _, job = resolve_target(DA, factories)
    assert job.schedule == "da-vintages"
    name, job = resolve_target({**DA, "job": "inference-930"}, factories)
    assert name == "da_energy-hub-champion-inference-930"
    assert job.schedule == "da-vintage-930"


def test_resolve_target_none_when_no_consistent_factory():
    factories = _factories({"{product}-{group}-{variant}-training": _inference()})
    assert resolve_target(DA, factories) is None  # job 'inference' != 'training'


def test_resolve_target_skips_factory_with_extra_token():
    # factory needs {region}, which the binding lacks -> not a candidate
    factories = _factories({"{product}-{group}-{variant}-{region}-inference": _inference()})
    assert resolve_target(DA, factories) is None


def test_resolve_target_skips_when_render_leaves_a_brace():
    # a binding value containing a brace leaves an unfilled placeholder
    factories = _factories({"{product}-{group}-inference": _inference()})
    assert resolve_target({"product": "da_energy", "group": "{oops}", "job": "inference"}, factories) is None


# --- enumerate_jobs (pipeline-derived) ---------------------------------------


def test_enumerate_derives_one_job_per_namespace():
    jobs = enumerate_jobs(_config(_base_jobs()), pipelines=_PIPES)
    assert set(jobs) == {
        "da_energy-hub-champion-training",
        "da_energy-hub-challenger-training",
        "rt_energy-hub-champion-training",
        "rt_energy-hub-challenger-training",
        "da_energy-hub-champion-inference",
        "da_energy-hub-challenger-inference",
        "rt_energy-hub-champion-inference",
        "rt_energy-hub-challenger-inference",
    }
    assert jobs["da_energy-hub-champion-inference"].schedule == ["da-vintages", "da-vintage-930"]
    assert jobs["rt_energy-hub-champion-inference"].schedule == "rt-hourly"  # most-specific + dedup
    assert jobs["da_energy-hub-champion-inference"].pipeline.node_namespaces == ["da_energy.hub.champion"]


def test_enumerate_adding_a_namespace_adds_a_job():
    pipes = {"training": _pipe(*_NS4, "da_energy.zone.champion"), "inference": _pipe(*_NS4)}
    jobs = enumerate_jobs(_config(_base_jobs()), pipelines=pipes)
    assert "da_energy-zone-champion-training" in jobs
    assert jobs["da_energy-zone-champion-training"].pipeline.node_namespaces == ["da_energy.zone.champion"]


def test_enumerate_includes_literals_with_precedence():
    jobs_cfg = {**_base_jobs(), "snapshot": {"pipeline": {"pipeline_name": "snapshot"}}}
    jobs = enumerate_jobs(_config(jobs_cfg), pipelines=_PIPES)
    assert jobs["snapshot"].pipeline.pipeline_name == "snapshot"  # literal preserved


def test_enumerate_literal_overrides_rendered_name():
    jobs_cfg = {
        "{product}-{group}-{variant}-inference": _inference(),
        "da_energy-hub-champion-inference": {"pipeline": {"pipeline_name": "custom"}},  # literal collision
    }
    jobs = enumerate_jobs(_config(jobs_cfg), pipelines={"inference": _pipe("da_energy.hub.champion")})
    assert jobs["da_energy-hub-champion-inference"].pipeline.pipeline_name == "custom"


def test_enumerate_name_token_absent_from_namespaces_raises():
    cfg = _config({
        "{product}-{group}-{variant}-{job}": {
            "pipeline": {"pipeline_name": "training", "node_namespaces": ["{product}.{group}.{variant}"]},
        }
    })
    with pytest.raises(ValueError, match="absent from its node_namespaces template"):
        enumerate_jobs(cfg, pipelines={"training": _pipe("da_energy.hub.champion")})


def test_enumerate_skips_unregistered_pipeline_and_shallow_namespace():
    cfg = _config({
        "{product}-{group}-{variant}-ghost": {
            "pipeline": {"pipeline_name": "ghost", "node_namespaces": ["{product}.{group}.{variant}"]},
        },
        "{product}-{group}-{variant}-training": {
            "pipeline": {"pipeline_name": "training", "node_namespaces": ["{product}.{group}.{variant}"]},
        },
    })
    pipes = {"training": _pipe("da_energy.hub.champion", "da_energy.shared")}  # 'ghost' absent; 'shared' too shallow
    jobs = enumerate_jobs(cfg, pipelines=pipes)
    assert set(jobs) == {"da_energy-hub-champion-training"}


def test_enumerate_literal_prefix_template_skips_nonmatching_namespace():
    cfg = _config({
        "rt-only-{group}-{variant}-training": {
            "pipeline": {"pipeline_name": "training", "node_namespaces": ["rt_energy.{group}.{variant}"]},
        }
    })
    pipes = {"training": _pipe("rt_energy.hub.champion", "da_energy.hub.champion")}
    jobs = enumerate_jobs(cfg, pipelines=pipes)
    assert set(jobs) == {"rt-only-hub-champion-training"}  # da_energy skipped by literal mismatch


def test_enumerate_factory_with_no_node_namespaces_is_skipped():
    cfg = _config({"{product}-{group}-{variant}-training": {"pipeline": {"pipeline_name": "training"}}})
    assert enumerate_jobs(cfg, pipelines={"training": _pipe("da_energy.hub.champion")}) == {}


def test_enumerate_falls_back_to_global_pipelines(monkeypatch):
    monkeypatch.setattr(
        "kedro.framework.project.pipelines", {"training": _pipe("da_energy.hub.champion")}, raising=False
    )
    cfg = _config({
        "{product}-{group}-{variant}-training": {
            "pipeline": {"pipeline_name": "training", "node_namespaces": ["{product}.{group}.{variant}"]}
        }
    })
    jobs = enumerate_jobs(cfg)  # pipelines=None -> global registry
    assert "da_energy-hub-champion-training" in jobs


# --- resolve_jobs ------------------------------------------------------------


def test_resolve_jobs_by_name():
    selected = resolve_jobs(_config(_base_jobs()), ["rt_energy-hub-champion-inference"], pipelines=_PIPES)
    assert list(selected) == ["rt_energy-hub-champion-inference"]


def test_resolve_jobs_multiple_names_reuse_enumeration():
    names = ["da_energy-hub-champion-inference", "rt_energy-hub-champion-inference"]
    selected = resolve_jobs(_config(_base_jobs()), names, pipelines=_PIPES)
    assert set(selected) == set(names)


def test_resolve_jobs_literal_fast_path():
    cfg = _config({"snapshot": {"pipeline": {"pipeline_name": "snapshot"}}})
    assert list(resolve_jobs(cfg, ["snapshot"])) == ["snapshot"]


def test_resolve_jobs_miss_lists_available():
    with pytest.raises(ValueError, match="Job\\(s\\) not found"):
        resolve_jobs(_config(_base_jobs()), ["nope-x-y-inference"], pipelines=_PIPES)
