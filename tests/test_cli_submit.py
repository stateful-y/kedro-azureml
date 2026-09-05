"""Tests for the submit path: one staged snapshot, one registration, shared clients, concurrent mode."""

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

import kedro_azureml_pipeline.cli.commands as cli
import kedro_azureml_pipeline.snapshot as snapshot_module
from kedro_azureml_pipeline.cli.functions import run_jobs
from kedro_azureml_pipeline.client import AzureMLPipelinesClient
from kedro_azureml_pipeline.config import JobConfig, PipelineFilterOptions, WorkspaceConfig
from kedro_azureml_pipeline.constants import CONCURRENT_SUBMIT_WORKERS
from kedro_azureml_pipeline.generator import AzureMLPipelineGenerator
from kedro_azureml_pipeline.manager import KedroContextManager
from tests.utils import create_kedro_conf_dirs

CODE_ID = (
    "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.MachineLearningServices"
    "/workspaces/ws/codes/tests-snapshot/versions/1"
)


def _jobs(names, **kwargs):
    return {
        name: JobConfig(pipeline=PipelineFilterOptions(pipeline_name="__default__"), display_name=name, **kwargs)
        for name in names
    }


def _registered(code_id):
    asset = MagicMock(id=code_id, version="1")
    asset.name = "tests-snapshot"
    return asset


def _submitted_steps(ml_client):
    """Yield the steps of every pipeline job submitted through *ml_client*."""
    for call in ml_client.jobs.create_or_update.call_args_list:
        yield from call.args[0].jobs.values()


@pytest.fixture
def submit_env(patched_kedro_package, dummy_pipeline, dummy_plugin_config, cli_context, tmp_path):
    """A project on disk, a Kedro context mock, and patched Azure entry points."""
    create_kedro_conf_dirs(tmp_path)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x = 1\n")

    mock_mgr = MagicMock(spec=KedroContextManager)
    mock_mgr.plugin_config = dummy_plugin_config
    mock_mgr.context.params = {}
    mock_mgr.context.catalog = MagicMock()
    mock_mgr.context.config_loader.__getitem__ = MagicMock(side_effect=KeyError("mlflow"))

    with (
        patch.dict("kedro.framework.project.pipelines", {"__default__": dummy_pipeline}),
        patch.object(Path, "cwd", return_value=tmp_path),
        patch.object(KedroContextManager, "__enter__", return_value=mock_mgr),
        patch.object(KedroContextManager, "__exit__", return_value=False),
        patch.object(AzureMLPipelineGenerator, "get_kedro_pipeline", return_value=dummy_pipeline),
        patch("kedro_azureml_pipeline.client.DefaultAzureCredential") as credential_cls,
        patch("kedro_azureml_pipeline.client.MLClient") as ml_client_cls,
        patch(
            "kedro_azureml_pipeline.snapshot.stage_code_snapshot", wraps=snapshot_module.stage_code_snapshot
        ) as staging,
    ):
        ml_client = ml_client_cls.return_value
        ml_client._code.create_or_update.return_value = _registered(CODE_ID)
        yield SimpleNamespace(
            config=dummy_plugin_config,
            ctx=cli_context,
            tmp_path=tmp_path,
            credential_cls=credential_cls,
            ml_client_cls=ml_client_cls,
            ml_client=ml_client,
            staging=staging,
        )


class TestSnapshotOnSubmit:
    """The snapshot is staged once per invocation and registered once per workspace."""

    def test_batch_stages_once_registers_once_and_steps_carry_the_id(self, submit_env):
        submit_env.config.jobs = _jobs(["a", "b", "c"])

        result = CliRunner().invoke(cli.run, ["-j", "a", "-j", "b", "-j", "c"], obj=submit_env.ctx)

        assert result.exit_code == 0, result.output
        assert submit_env.staging.call_count == 1
        assert submit_env.ml_client._code.create_or_update.call_count == 1
        assert submit_env.credential_cls.call_count == 1
        assert submit_env.ml_client_cls.call_count == 1
        registered = submit_env.ml_client._code.create_or_update.call_args.args[0]
        assert registered.name == "tests-snapshot"
        assert submit_env.ml_client.jobs.create_or_update.call_count == 3
        steps = list(_submitted_steps(submit_env.ml_client))
        assert steps
        assert all(step.component.code == CODE_ID for step in steps)

    def test_staged_directory_is_gone_after_the_batch(self, submit_env):
        submit_env.config.jobs = _jobs(["a"])
        staged_paths = []

        def register(code):
            staged = Path(code.path)
            assert (staged / "src" / "app.py").exists()
            assert not (staged / ".amlignore").exists()
            staged_paths.append(staged)
            return _registered(CODE_ID)

        submit_env.ml_client._code.create_or_update.side_effect = register

        result = CliRunner().invoke(cli.run, ["-j", "a"], obj=submit_env.ctx)

        assert result.exit_code == 0, result.output
        assert len(staged_paths) == 1
        assert not staged_paths[0].exists()

    def test_two_workspaces_register_once_each(self, submit_env):
        submit_env.config.workspace.root["other"] = WorkspaceConfig(
            subscription_id="sub-2", resource_group="rg-2", name="ws-2"
        )
        submit_env.config.jobs = {**_jobs(["a"]), **_jobs(["b"], workspace="other")}
        client_a, client_b = MagicMock(), MagicMock()
        client_a._code.create_or_update.return_value = _registered("/codes/a/versions/1")
        client_b._code.create_or_update.return_value = _registered("/codes/b/versions/1")
        submit_env.ml_client_cls.side_effect = [client_a, client_b]

        result = CliRunner().invoke(cli.run, ["-j", "a", "-j", "b"], obj=submit_env.ctx)

        assert result.exit_code == 0, result.output
        assert submit_env.staging.call_count == 1
        assert client_a._code.create_or_update.call_count == 1
        assert client_b._code.create_or_update.call_count == 1
        assert all(step.component.code == "/codes/a/versions/1" for step in _submitted_steps(client_a))
        assert all(step.component.code == "/codes/b/versions/1" for step in _submitted_steps(client_b))

    def test_dry_run_neither_stages_nor_authenticates(self, submit_env):
        submit_env.config.jobs = _jobs(["a", "b"])

        result = CliRunner().invoke(cli.run, ["-j", "a", "-j", "b", "--dry-run"], obj=submit_env.ctx)

        assert result.exit_code == 0, result.output
        assert result.output.count("[DRY RUN]") == 2
        submit_env.staging.assert_not_called()
        submit_env.ml_client_cls.assert_not_called()
        submit_env.credential_cls.assert_not_called()

    def test_no_code_directory_registers_nothing(self, submit_env):
        submit_env.config.execution.code_directory = None
        submit_env.config.jobs = _jobs(["a"])

        result = CliRunner().invoke(cli.run, ["-j", "a"], obj=submit_env.ctx)

        assert result.exit_code == 0, result.output
        submit_env.staging.assert_not_called()
        submit_env.ml_client._code.create_or_update.assert_not_called()
        assert all(step.component.code is None for step in _submitted_steps(submit_env.ml_client))

    def test_registration_failure_submits_nothing(self, submit_env):
        submit_env.config.jobs = _jobs(["a", "b"])
        submit_env.ml_client._code.create_or_update.side_effect = RuntimeError("upload refused")

        result = CliRunner().invoke(cli.run, ["-j", "a", "-j", "b"], obj=submit_env.ctx)

        assert result.exit_code != 0
        submit_env.ml_client.jobs.create_or_update.assert_not_called()


class TestCompileStaysOffline:
    """Compile-only commands neither stage nor register and keep the configured path."""

    def test_compile_emits_a_local_path_and_touches_nothing(self, submit_env):
        submit_env.config.jobs = _jobs(["a"])
        output = submit_env.tmp_path / "out.yml"

        result = CliRunner().invoke(cli.compile, ["-j", "a", "-o", str(output)], obj=submit_env.ctx)

        assert result.exit_code == 0, result.output
        submit_env.staging.assert_not_called()
        submit_env.ml_client_cls.assert_not_called()
        submit_env.credential_cls.assert_not_called()
        steps = yaml.safe_load(output.read_text())["jobs"].values()
        codes = [step["component"]["code"] for step in steps]
        assert codes
        assert all("/codes/" not in code and Path(code).is_dir() for code in codes)

    def test_compile_check_touches_nothing(self, submit_env):
        submit_env.config.jobs = _jobs(["a", "b"])

        result = CliRunner().invoke(cli.compile, ["--all", "--check"], obj=submit_env.ctx)

        assert result.exit_code == 0, result.output
        submit_env.staging.assert_not_called()
        submit_env.ml_client_cls.assert_not_called()


class TestConcurrentSubmission:
    """``--concurrent`` attempts every independent job from a bounded pool."""

    @staticmethod
    def _fails_for(failing):
        def fake_run(self, *args, **kwargs):
            return self.azure_pipeline.display_name not in failing

        return fake_run

    def test_concurrent_attempts_every_job(self, submit_env):
        submit_env.config.jobs = _jobs(["a", "b", "c"])
        with patch.object(AzureMLPipelinesClient, "run", autospec=True, side_effect=self._fails_for({"b"})) as run:
            result = CliRunner().invoke(cli.run, ["--concurrent", "-j", "a", "-j", "b", "-j", "c"], obj=submit_env.ctx)

        assert result.exit_code == 1
        assert run.call_count == 3
        assert "2 succeeded, 1 failed (out of 3 selected)" in result.output
        assert "skipped" not in result.output
        assert "Aborting batch" not in result.output

    def test_serial_default_still_fails_fast(self, submit_env):
        submit_env.config.jobs = _jobs(["a", "b", "c"])
        with patch.object(AzureMLPipelinesClient, "run", autospec=True, side_effect=self._fails_for({"b"})) as run:
            result = CliRunner().invoke(cli.run, ["-j", "a", "-j", "b", "-j", "c"], obj=submit_env.ctx)

        assert result.exit_code == 1
        assert run.call_count == 2
        assert "1 succeeded, 1 failed, 1 skipped (out of 3 selected)" in result.output

    def test_pool_is_bounded(self, submit_env):
        names = [f"job{i}" for i in range(2 * CONCURRENT_SUBMIT_WORKERS)]
        submit_env.config.jobs = _jobs(names)
        lock = threading.Lock()
        in_flight = {"now": 0, "max": 0}

        def slow_run(self, *args, **kwargs):
            with lock:
                in_flight["now"] += 1
                in_flight["max"] = max(in_flight["max"], in_flight["now"])
            time.sleep(0.02)
            with lock:
                in_flight["now"] -= 1
            return True

        with patch.object(AzureMLPipelinesClient, "run", autospec=True, side_effect=slow_run) as run:
            result = CliRunner().invoke(
                cli.run, ["--concurrent", *[flag for n in names for flag in ("-j", n)]], obj=submit_env.ctx
            )

        assert result.exit_code == 0, result.output
        assert run.call_count == len(names)
        assert 1 <= in_flight["max"] <= CONCURRENT_SUBMIT_WORKERS

    def test_shared_client_and_callback_once_per_job(self, submit_env):
        submit_env.config.jobs = _jobs(["a", "b", "c"])
        callback = MagicMock()

        ok = run_jobs(
            ctx=submit_env.ctx,
            aml_env=None,
            params="",
            extra_env={},
            load_versions={},
            job_names=["a", "b", "c"],
            dry_run=False,
            on_job_scheduled=callback,
            concurrent=True,
        )

        assert ok is True
        assert callback.call_count == 3
        assert submit_env.ml_client_cls.call_count == 1
        assert submit_env.credential_cls.call_count == 1
        assert submit_env.ml_client.jobs.create_or_update.call_count == 3

    def test_concurrent_rejects_wait_for_completion(self, submit_env):
        submit_env.config.jobs = _jobs(["a"])

        result = CliRunner().invoke(cli.run, ["--concurrent", "--wait-for-completion", "-j", "a"], obj=submit_env.ctx)

        assert result.exit_code == 2
        assert "mutually exclusive" in result.output
        submit_env.ml_client_cls.assert_not_called()
        submit_env.staging.assert_not_called()

    def test_concurrent_dry_run_lists_every_job(self, submit_env):
        submit_env.config.jobs = _jobs(["a", "b"])

        result = CliRunner().invoke(cli.run, ["--concurrent", "--dry-run", "-j", "a", "-j", "b"], obj=submit_env.ctx)

        assert result.exit_code == 0, result.output
        assert result.output.count("[DRY RUN]") == 2
        submit_env.ml_client_cls.assert_not_called()
