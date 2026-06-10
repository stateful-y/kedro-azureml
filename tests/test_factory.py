"""Tests for forward-only job factory resolution."""

from types import SimpleNamespace

import pytest

from kedro_azureml_pipeline.config.models import KedroAzureMLConfig
from kedro_azureml_pipeline.factory import (
    InlineTargetProvider,
    PipelinesTargetProvider,
    ProviderContext,
    enumerate_jobs,
    is_factory,
    load_target_provider,
    resolve_jobs,
    resolve_target,
)

_WS = {"__default__": {"subscription_id": "s", "resource_group": "r", "name": "w"}}
_COMPUTE = {"__default__": {"cluster_name": "c"}}


def _pipe(*namespaces):
    """A stand-in Kedro pipeline: an object with ``.nodes``, each with ``.namespace``."""
    return SimpleNamespace(nodes=[SimpleNamespace(namespace=ns) for ns in namespaces])


_NS4 = ["da_energy.hub.champion", "da_energy.hub.challenger", "rt_energy.hub.champion", "rt_energy.hub.challenger"]


def _b2_config(extra_jobs=None):
    jobs = {
        "{product}-{group}-{variant}-training": {
            "schedule": "weekly-monday",
            "pipeline": {"pipeline_name": "training", "node_namespaces": ["{product}.{group}.{variant}"]},
        },
        "{product}-{group}-{variant}-inference": {
            "schedule": ["da-vintages", "da-vintage-930"],
            "pipeline": {"pipeline_name": "inference", "node_namespaces": ["{product}.{group}.{variant}"]},
        },
        "rt_energy-{group}-{variant}-inference": {
            "schedule": "rt-hourly",
            "pipeline": {"pipeline_name": "inference", "node_namespaces": ["{product}.{group}.{variant}"]},
        },
    }
    jobs.update(extra_jobs or {})
    return KedroAzureMLConfig.model_validate({"workspace": _WS, "compute": _COMPUTE, "jobs": jobs})


_PIPES = {"training": _pipe(*_NS4), "inference": _pipe(*_NS4)}


def _config(jobs, job_targets=None, provider="inline"):
    return KedroAzureMLConfig.model_validate({
        "workspace": _WS,
        "compute": _COMPUTE,
        "jobs": jobs,
        "job_targets": job_targets or [],
        "job_target_provider": provider,
    })


def _factories(jobs):
    """Build a config and return its factory entries as validated JobConfig values."""
    cfg = _config(jobs)
    return {k: v for k, v in cfg.jobs.items() if is_factory(k)}


def _inference_factory(schedule="da-vintages", ns="{product_ns}.{group}"):
    return {
        "schedule": schedule,
        "pipeline": {"pipeline_name": "inference", "node_namespaces": [ns], "tags": ["{variant}"]},
    }


DA_TARGET = {
    "product": "da-energy",
    "product_ns": "da_energy",
    "group": "hub",
    "variant": "champion",
    "job": "inference",
}
RT_TARGET = {
    "product": "rt-energy",
    "product_ns": "rt_energy",
    "group": "hub",
    "variant": "champion",
    "job": "inference",
}


def test_is_factory():
    assert is_factory("{product}-{group}-inference")
    assert not is_factory("snapshot")


def test_forward_render_of_a_target():
    factories = {"{product}-{group}-{variant}-inference": _inference_factory()}
    name, job = resolve_target(DA_TARGET, factories)
    assert name == "da-energy-hub-champion-inference"
    assert job.pipeline.node_namespaces == ["da_energy.hub"]
    assert job.pipeline.tags == ["champion"]
    assert job.schedule == "da-vintages"


def test_no_reverse_parse_hyphenated_product():
    # `da-energy` (product) itself contains the '-' separator; forward rendering
    # must still produce the correct namespace, never re-splitting the name.
    factories = {"{product}-{group}-{variant}-inference": _inference_factory()}
    name, job = resolve_target(DA_TARGET, factories)
    assert name == "da-energy-hub-champion-inference"
    assert job.pipeline.node_namespaces == ["da_energy.hub"]


def test_most_specific_consistent_factory_wins():
    factories = {
        "{product}-{group}-{variant}-inference": _inference_factory(schedule="da-vintages"),
        "rt-energy-{group}-{variant}-inference": _inference_factory(schedule="rt-hourly", ns="rt_energy.{group}"),
    }
    # rt target: both factories render the same name; the more-specific rt one wins.
    name, job = resolve_target(RT_TARGET, factories)
    assert name == "rt-energy-hub-champion-inference"
    assert job.schedule == "rt-hourly"
    assert job.pipeline.node_namespaces == ["rt_energy.hub"]
    # da target: the rt factory renders a different name, so the general one applies.
    name, job = resolve_target(DA_TARGET, factories)
    assert job.schedule == "da-vintages"


def test_job_type_not_collapsed():
    # An `inference` target must NOT match an `inference-930` factory.
    factories = {
        "{product}-{group}-{variant}-inference": _inference_factory(schedule="da-vintages"),
        "{product}-{group}-{variant}-inference-930": _inference_factory(schedule="da-vintage-930"),
    }
    _, job = resolve_target(DA_TARGET, factories)
    assert job.schedule == "da-vintages"
    target_930 = {**DA_TARGET, "job": "inference-930"}
    name, job = resolve_target(target_930, factories)
    assert name == "da-energy-hub-champion-inference-930"
    assert job.schedule == "da-vintage-930"


def test_non_string_leaves_pass_through():
    factories = {
        "{product}-{group}-{variant}-inference": {
            "pipeline": {"pipeline_name": "inference", "tags": ["{variant}"]},
            "retry": {"max_retries": 3, "timeout": 3600},
        }
    }
    _, job = resolve_target(DA_TARGET, factories)
    assert job.retry.max_retries == 3
    assert job.retry.timeout == 3600


def test_no_consistent_factory_returns_none():
    factories = {"{product}-{group}-{variant}-training": _inference_factory()}
    assert resolve_target(DA_TARGET, factories) is None  # job-type 'inference' != 'training'


def test_enumerate_includes_literals_and_targets():
    cfg = _config(
        jobs={
            "{product}-{group}-{variant}-inference": _inference_factory(),
            "snapshot": {"pipeline": {"pipeline_name": "snapshot"}},
        },
        job_targets=[DA_TARGET, RT_TARGET],
    )
    jobs = enumerate_jobs(cfg)
    assert set(jobs) == {
        "da-energy-hub-champion-inference",
        "rt-energy-hub-champion-inference",
        "snapshot",
    }
    assert jobs["snapshot"].pipeline.pipeline_name == "snapshot"  # literal preserved verbatim


def test_enumerate_one_job_per_target():
    cfg = _config(
        jobs={"{product}-{group}-{variant}-inference": _inference_factory()},
        job_targets=[DA_TARGET, RT_TARGET],
    )
    assert len(enumerate_jobs(cfg)) == 2


def test_enumerate_unmatched_target_raises():
    cfg = _config(
        jobs={"{product}-{group}-{variant}-training": _inference_factory()},
        job_targets=[DA_TARGET],  # job 'inference' has no matching factory
    )
    with pytest.raises(ValueError, match="No job factory matches target"):
        enumerate_jobs(cfg)


def test_resolve_jobs_by_name():
    cfg = _config(
        jobs={"{product}-{group}-{variant}-inference": _inference_factory()},
        job_targets=[DA_TARGET, RT_TARGET],
    )
    selected = resolve_jobs(cfg, ["rt-energy-hub-champion-inference"])
    assert list(selected) == ["rt-energy-hub-champion-inference"]


def test_resolve_jobs_literal_fast_path():
    cfg = _config(jobs={"snapshot": {"pipeline": {"pipeline_name": "snapshot"}}})
    selected = resolve_jobs(cfg, ["snapshot"])
    assert list(selected) == ["snapshot"]


def test_resolve_jobs_miss_lists_available():
    cfg = _config(
        jobs={"{product}-{group}-{variant}-inference": _inference_factory()},
        job_targets=[DA_TARGET],
    )
    with pytest.raises(ValueError, match="Job\\(s\\) not found"):
        resolve_jobs(cfg, ["does-not-exist"])


def test_literal_takes_precedence_over_factory():
    # A literal job whose name collides with a rendered target wins.
    cfg = _config(
        jobs={
            "{product}-{group}-{variant}-inference": _inference_factory(schedule="da-vintages"),
            "da-energy-hub-champion-inference": {"pipeline": {"pipeline_name": "custom"}},
        },
        job_targets=[DA_TARGET],
    )
    jobs = enumerate_jobs(cfg)
    assert jobs["da-energy-hub-champion-inference"].pipeline.pipeline_name == "custom"


def test_inline_provider_default_and_loading():
    cfg = _config(jobs={}, job_targets=[DA_TARGET])
    provider = load_target_provider("inline")
    assert isinstance(provider, InlineTargetProvider)
    assert provider.targets(ProviderContext(config=cfg)) == [DA_TARGET]


def test_unknown_provider_raises_with_listing():
    with pytest.raises(ValueError, match="Unknown job-target provider 'nope'.*inline"):
        load_target_provider("nope")


def test_render_str_tolerates_unknown_field_and_passthrough():
    from kedro_azureml_pipeline.factory import _render_str

    assert _render_str("plain-text", {"a": "b"}) == "plain-text"  # no braces -> unchanged
    assert _render_str("{variant}", {"variant": "champion"}) == "champion"
    # an unresolved/unknown field is tolerated (returned unchanged), never raising
    assert _render_str("${oc.env:FOO}", {"variant": "champion"}) == "${oc.env:FOO}"
    assert _render_str("{missing}", {"variant": "champion"}) == "{missing}"


def test_matches_job_type_when_target_has_no_job():
    # A target without a 'job' key matches any job-type (job is None -> True).
    factories = _factories({"{product}-{group}-inference": _inference_factory()})
    target = {"product": "da-energy", "product_ns": "da_energy", "group": "hub", "variant": "champion"}
    result = resolve_target(target, factories)
    assert result is not None
    assert result[0] == "da-energy-hub-inference"


def test_entry_point_provider_is_loaded(monkeypatch):
    import kedro_azureml_pipeline.factory as factory_mod

    class _FakeProvider:
        def targets(self, context):
            return [DA_TARGET]

    class _FakeEP:
        name = "fake"

        def load(self):
            return _FakeProvider

    monkeypatch.setattr(factory_mod, "entry_points", lambda group: [_FakeEP()])
    provider = factory_mod.load_target_provider("fake")
    assert isinstance(provider, _FakeProvider)


def test_builtin_inline_used_when_no_entry_point(monkeypatch):
    import kedro_azureml_pipeline.factory as factory_mod

    monkeypatch.setattr(factory_mod, "entry_points", lambda group: [])  # no registered EPs
    assert isinstance(factory_mod.load_target_provider("inline"), InlineTargetProvider)


def test_factory_token_absent_from_target_is_skipped():
    # Factory references {region}, which the target lacks -> not a candidate -> no match.
    factories = _factories({"{product}-{region}-inference": _inference_factory()})
    assert resolve_target(DA_TARGET, factories) is None


def test_unfilled_placeholder_after_render_is_skipped():
    # A token value that itself contains braces leaves an unfilled placeholder.
    factories = _factories({"{product}-{group}-inference": _inference_factory()})
    weird = {**DA_TARGET, "group": "{still}"}
    assert resolve_target(weird, factories) is None


def test_duplicate_target_names_raise():
    cfg = _config(
        jobs={"{product}-{group}-{variant}-inference": _inference_factory()},
        job_targets=[DA_TARGET, dict(DA_TARGET)],  # two targets render the same name
    )
    with pytest.raises(ValueError, match="render to the same job name"):
        enumerate_jobs(cfg)


def test_resolve_multiple_factory_names_reuses_enumeration():
    cfg = _config(
        jobs={"{product}-{group}-{variant}-inference": _inference_factory()},
        job_targets=[DA_TARGET, RT_TARGET],
    )
    selected = resolve_jobs(cfg, ["da-energy-hub-champion-inference", "rt-energy-hub-champion-inference"])
    assert set(selected) == {"da-energy-hub-champion-inference", "rt-energy-hub-champion-inference"}


# --- B2: bindings derived from pipeline namespaces (default) + multi-schedule ---


def test_pipelines_default_derives_from_namespaces():
    cfg = _b2_config()
    assert cfg.job_target_provider == "pipelines"  # default
    jobs = enumerate_jobs(cfg, pipelines=_PIPES)
    # training + inference over 4 namespaces each
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
    assert jobs["da_energy-hub-champion-inference"].pipeline.node_namespaces == ["da_energy.hub.champion"]


def test_multi_schedule_preserved_on_rendered_job():
    jobs = enumerate_jobs(_b2_config(), pipelines=_PIPES)
    assert jobs["da_energy-hub-champion-inference"].schedule == ["da-vintages", "da-vintage-930"]
    assert jobs["rt_energy-hub-champion-inference"].schedule == "rt-hourly"  # rt factory wins


def test_most_specific_factory_wins_under_derivation():
    jobs = enumerate_jobs(_b2_config(), pipelines=_PIPES)
    # rt binding: general + rt factory render the same name; rt (more literal) wins
    assert jobs["rt_energy-hub-champion-inference"].pipeline.node_namespaces == ["rt_energy.hub.champion"]
    assert jobs["da_energy-hub-champion-inference"].schedule == ["da-vintages", "da-vintage-930"]


def test_adding_a_namespace_adds_a_job():
    pipes = {"training": _pipe(*_NS4, "da_energy.zone.champion"), "inference": _pipe(*_NS4)}
    jobs = enumerate_jobs(_b2_config(), pipelines=pipes)
    assert "da_energy-zone-champion-training" in jobs
    assert jobs["da_energy-zone-champion-training"].pipeline.node_namespaces == ["da_energy.zone.champion"]
    assert "da_energy-hub-champion-training" in jobs  # siblings unaffected


def test_run_by_name_uses_derived_set():
    selected = resolve_jobs(_b2_config(), ["rt_energy-hub-champion-inference"], pipelines=_PIPES)
    assert list(selected) == ["rt_energy-hub-champion-inference"]


def test_run_by_name_miss_lists_available():
    with pytest.raises(ValueError, match="Job\\(s\\) not found"):
        resolve_jobs(_b2_config(), ["nope-x-y-inference"], pipelines=_PIPES)


def test_name_token_absent_from_node_namespaces_errors():
    cfg = _b2_config(
        extra_jobs={
            # {job} is in the key but not the node_namespaces template -> unbindable
            "{product}-{group}-{variant}-{job}": {
                "pipeline": {"pipeline_name": "training", "node_namespaces": ["{product}.{group}.{variant}"]},
            }
        }
    )
    with pytest.raises(ValueError, match="absent from its node_namespaces template"):
        enumerate_jobs(cfg, pipelines=_PIPES)


def test_inline_job_targets_override_pipeline_default():
    # Providing job_targets switches to the inline provider even though the
    # default is 'pipelines'; pipelines are then ignored.
    cfg = KedroAzureMLConfig.model_validate({
        "workspace": _WS,
        "compute": _COMPUTE,
        "jobs": {"{product}-{group}-{variant}-inference": _inference_factory()},
        "job_targets": [DA_TARGET],
    })
    jobs = enumerate_jobs(cfg, pipelines=_PIPES)
    assert set(jobs) == {"da-energy-hub-champion-inference"}  # from inline target, not pipelines


def test_bind_namespace_edges():
    from kedro_azureml_pipeline.factory import _bind_namespace

    # full match
    assert _bind_namespace("{product}.{group}.{variant}", "da_energy.hub.champion") == {
        "product": "da_energy",
        "group": "hub",
        "variant": "champion",
    }
    # namespace shallower than the template -> None
    assert _bind_namespace("{product}.{group}.{variant}", "da_energy.hub") is None
    # literal segment must match
    assert _bind_namespace("rt_energy.{group}", "rt_energy.hub") == {"group": "hub"}
    assert _bind_namespace("rt_energy.{group}", "da_energy.hub") is None


def test_factory_with_unregistered_pipeline_contributes_nothing():
    cfg = _b2_config(
        extra_jobs={
            "{product}-{group}-{variant}-ghost": {
                "pipeline": {"pipeline_name": "ghost", "node_namespaces": ["{product}.{group}.{variant}"]},
            }
        }
    )
    jobs = enumerate_jobs(cfg, pipelines=_PIPES)  # 'ghost' pipeline absent
    assert not any(n.endswith("-ghost") for n in jobs)


def test_shallow_namespace_is_skipped():
    # A node namespaced shallower than the template depth is ignored.
    pipes = {"training": _pipe("da_energy.hub.champion", "da_energy.shared"), "inference": _pipe()}
    cfg = _b2_config()
    jobs = enumerate_jobs(cfg, pipelines=pipes)
    assert "da_energy-hub-champion-training" in jobs
    assert not any("shared" in n for n in jobs)


def test_literal_prefix_template_skips_nonmatching_namespace():
    # A node_namespaces template with a literal prefix only binds matching
    # namespaces; a same-depth namespace with a different prefix is skipped.
    cfg = KedroAzureMLConfig.model_validate({
        "workspace": _WS,
        "compute": _COMPUTE,
        "jobs": {
            "rt-only-{group}-{variant}-training": {
                "schedule": "weekly-monday",
                "pipeline": {"pipeline_name": "training", "node_namespaces": ["rt_energy.{group}.{variant}"]},
            },
        },
    })
    pipes = {"training": _pipe("rt_energy.hub.champion", "da_energy.hub.champion")}
    jobs = enumerate_jobs(cfg, pipelines=pipes)
    # only the rt_energy namespace binds; da_energy is skipped by the literal mismatch
    assert set(jobs) == {"rt-only-hub-champion-training"}


def test_pipelines_provider_falls_back_to_global_registry(monkeypatch):
    # context.pipelines=None -> provider imports the global registry
    fake_registry = {"training": _pipe("da_energy.hub.champion"), "inference": _pipe()}
    monkeypatch.setattr("kedro.framework.project.pipelines", fake_registry, raising=False)
    cfg = _b2_config()
    targets = PipelinesTargetProvider().targets(ProviderContext(config=cfg, pipelines=None))
    assert {"product": "da_energy", "group": "hub", "variant": "champion", "job": "training"} in targets
