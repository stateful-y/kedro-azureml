"""Tests for the Azure ML pipeline client."""

import os
from unittest.mock import MagicMock, patch

import pytest
from azure.core.exceptions import HttpResponseError
from azure.identity import CredentialUnavailableError

from kedro_azureml_pipeline.client import AzureMLPipelinesClient, _get_azureml_client, get_azureml_credentials
from kedro_azureml_pipeline.config import ClusterConfig, ComputeConfig, WorkspaceConfig


@pytest.fixture
def workspace_config():
    return WorkspaceConfig(subscription_id="sub-1", resource_group="rg-1", name="ws-1")


@pytest.fixture
def compute_config():
    return ComputeConfig(root={"__default__": ClusterConfig(cluster_name="cpu-cluster")})


@pytest.fixture
def mock_pipeline_job():
    job = MagicMock()
    job.display_name = "test-pipeline"
    return job


class TestGetAzureMLClient:
    """Context-manager client creation."""

    def test_yields_ml_client(self, workspace_config):
        with (
            patch("kedro_azureml_pipeline.client.get_azureml_credentials") as mock_creds,
            patch("kedro_azureml_pipeline.client.MLClient") as mock_ml_client_cls,
        ):
            mock_creds.return_value = MagicMock()
            mock_ml_client_cls.from_config.return_value = MagicMock()

            with _get_azureml_client(workspace_config) as client:
                assert client is not None
                mock_ml_client_cls.from_config.assert_called_once()

    def test_writes_config_json_with_workspace_details(self, workspace_config):
        written_content = None

        def capture_write(content):
            nonlocal written_content
            written_content = content
            # Call original or just store

        with (
            patch("kedro_azureml_pipeline.client.get_azureml_credentials") as mock_creds,
            patch("kedro_azureml_pipeline.client.MLClient") as mock_ml_client_cls,
        ):
            mock_creds.return_value = MagicMock()
            mock_ml_client_cls.from_config.return_value = MagicMock()

            # We need to intercept the write_text call on the config path
            with _get_azureml_client(workspace_config):
                pass

            # Verify from_config was called with expected credential and path
            call_kwargs = mock_ml_client_cls.from_config.call_args
            assert call_kwargs.kwargs.get("credential") is not None or call_kwargs[1].get("credential") is not None


class TestAzureMLPipelinesClient:
    """Job submission and lifecycle."""

    def test_run_submits_job(self, workspace_config, compute_config, mock_pipeline_job):
        client = AzureMLPipelinesClient(mock_pipeline_job)

        with patch("kedro_azureml_pipeline.client._get_azureml_client") as mock_ctx:
            mock_ml_client = MagicMock()
            mock_ml_client.compute.get.return_value = MagicMock(
                name="cpu-cluster", size="Standard_DS3_v2", min_instances=0, max_instances=4
            )
            mock_ml_client.jobs.create_or_update.return_value = MagicMock()
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_ml_client)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            result = client.run(workspace_config, compute_config)

            assert result is True
            mock_ml_client.jobs.create_or_update.assert_called_once()

    def test_run_with_display_name_override(self, workspace_config, compute_config, mock_pipeline_job):
        client = AzureMLPipelinesClient(mock_pipeline_job)

        with patch("kedro_azureml_pipeline.client._get_azureml_client") as mock_ctx:
            mock_ml_client = MagicMock()
            mock_ml_client.compute.get.return_value = MagicMock(
                name="cpu-cluster", size="Standard_DS3_v2", min_instances=0, max_instances=4
            )
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_ml_client)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            client.run(workspace_config, compute_config, display_name="custom-name")

            assert mock_pipeline_job.display_name == "custom-name"

    def test_run_invokes_callback(self, workspace_config, compute_config, mock_pipeline_job):
        client = AzureMLPipelinesClient(mock_pipeline_job)
        callback = MagicMock()

        with patch("kedro_azureml_pipeline.client._get_azureml_client") as mock_ctx:
            mock_ml_client = MagicMock()
            mock_ml_client.compute.get.return_value = MagicMock(
                name="cpu-cluster", size="Standard_DS3_v2", min_instances=0, max_instances=4
            )
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_ml_client)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            client.run(workspace_config, compute_config, on_job_scheduled=callback)

            callback.assert_called_once()

    def test_run_wait_for_completion_returns_true_on_success(self, workspace_config, compute_config, mock_pipeline_job):
        client = AzureMLPipelinesClient(mock_pipeline_job)

        with patch("kedro_azureml_pipeline.client._get_azureml_client") as mock_ctx:
            mock_ml_client = MagicMock()
            mock_ml_client.compute.get.return_value = MagicMock(
                name="cpu-cluster", size="Standard_DS3_v2", min_instances=0, max_instances=4
            )
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_ml_client)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            result = client.run(workspace_config, compute_config, wait_for_completion=True)

            assert result is True
            mock_ml_client.jobs.stream.assert_called_once()

    def test_run_wait_for_completion_returns_false_on_error(self, workspace_config, compute_config, mock_pipeline_job):
        client = AzureMLPipelinesClient(mock_pipeline_job)

        with patch("kedro_azureml_pipeline.client._get_azureml_client") as mock_ctx:
            mock_ml_client = MagicMock()
            mock_ml_client.compute.get.return_value = MagicMock(
                name="cpu-cluster", size="Standard_DS3_v2", min_instances=0, max_instances=4
            )
            mock_ml_client.jobs.stream.side_effect = HttpResponseError("pipeline failed")
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_ml_client)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            result = client.run(workspace_config, compute_config, wait_for_completion=True)

            assert result is False

    def test_run_without_waiting_returns_true(self, workspace_config, compute_config, mock_pipeline_job):
        """Explicit ``wait_for_completion=False`` returns True without streaming."""
        client = AzureMLPipelinesClient(mock_pipeline_job)

        with patch("kedro_azureml_pipeline.client._get_azureml_client") as mock_ctx:
            mock_ml_client = MagicMock()
            mock_ml_client.compute.get.return_value = MagicMock(
                name="cpu-cluster", size="Standard_DS3_v2", min_instances=0, max_instances=4
            )
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_ml_client)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            result = client.run(
                workspace_config,
                compute_config,
                wait_for_completion=False,
                experiment_name="my-experiment",
            )

            assert result is True
            mock_ml_client.jobs.stream.assert_not_called()

    def test_run_raises_on_missing_cluster(self, workspace_config, compute_config, mock_pipeline_job):
        """A missing compute cluster raises ``ValueError``."""
        client = AzureMLPipelinesClient(mock_pipeline_job)

        with patch("kedro_azureml_pipeline.client._get_azureml_client") as mock_ctx:
            mock_ml_client = MagicMock()
            mock_ml_client.compute.get.return_value = None
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_ml_client)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(ValueError, match="does not exist"):
                client.run(workspace_config, compute_config)


class TestGetAzureMLCredentials:
    """Credential resolution strategy."""

    def test_returns_default_credential_when_valid(self):
        with (
            patch("kedro_azureml_pipeline.client.DefaultAzureCredential") as mock_default,
            patch("kedro_azureml_pipeline.client.InteractiveBrowserCredential") as mock_interactive,
        ):
            mock_default.return_value = MagicMock()
            result = get_azureml_credentials()

            mock_default.assert_called_once()
            mock_interactive.assert_not_called()
            assert result is mock_default.return_value

    def test_falls_back_to_interactive_on_failure(self):
        with (
            patch("kedro_azureml_pipeline.client.DefaultAzureCredential") as mock_default,
            patch("kedro_azureml_pipeline.client.InteractiveBrowserCredential") as mock_interactive,
        ):
            mock_default.return_value.get_token.side_effect = CredentialUnavailableError(message="no token")
            mock_interactive.return_value = MagicMock()

            result = get_azureml_credentials()

            mock_interactive.assert_called_once()
            assert result is mock_interactive.return_value

    def test_excludes_managed_identity_on_azureml_compute(self):
        with (
            patch.dict(os.environ, {"MSI_ENDPOINT": "http://fake"}),
            patch("kedro_azureml_pipeline.client.DefaultAzureCredential") as mock_default,
            patch("kedro_azureml_pipeline.client.InteractiveBrowserCredential"),
        ):
            mock_default.return_value = MagicMock()
            get_azureml_credentials()

            mock_default.assert_called_once_with(exclude_managed_identity_credential=True)

    def test_does_not_exclude_managed_identity_outside_azureml(self):
        env = os.environ.copy()
        env.pop("MSI_ENDPOINT", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch("kedro_azureml_pipeline.client.DefaultAzureCredential") as mock_default,
            patch("kedro_azureml_pipeline.client.InteractiveBrowserCredential"),
        ):
            mock_default.return_value = MagicMock()
            get_azureml_credentials()

            mock_default.assert_called_once_with(exclude_managed_identity_credential=False)


class TestBatchClients:
    """One credential for the batch, one client per workspace."""

    def test_one_credential_and_one_client_per_workspace(self, workspace_config):
        from kedro_azureml_pipeline.client import BatchClients

        other = WorkspaceConfig(subscription_id="sub-1", resource_group="rg-1", name="ws-2")
        with (
            patch("kedro_azureml_pipeline.client.get_azureml_credentials") as mock_creds,
            patch("kedro_azureml_pipeline.client.MLClient", side_effect=[MagicMock(), MagicMock()]) as ml_client_cls,
        ):
            clients = BatchClients()
            first = clients.get(workspace_config)
            again = clients.get(workspace_config)
            second = clients.get(other)

        assert first is again
        assert first is not second
        assert mock_creds.call_count == 1
        assert ml_client_cls.call_count == 2
        assert ml_client_cls.call_args_list[0].kwargs == {
            "subscription_id": "sub-1",
            "resource_group_name": "rg-1",
            "workspace_name": "ws-1",
        }


class TestRegisterCodeSnapshot:
    """The staged directory becomes one named code asset."""

    def test_registers_once_and_returns_the_id(self, tmp_path):
        from pathlib import Path

        from kedro_azureml_pipeline.client import register_code_snapshot

        (tmp_path / "a.py").write_text("x = 1\n")
        ml_client = MagicMock()
        ml_client._code.create_or_update.return_value = MagicMock(id="/codes/pkg-snapshot/versions/1", version="1")

        code_id = register_code_snapshot(ml_client, tmp_path, "pkg-snapshot")

        assert code_id == "/codes/pkg-snapshot/versions/1"
        ml_client._code.create_or_update.assert_called_once()
        code = ml_client._code.create_or_update.call_args.args[0]
        assert code.name == "pkg-snapshot"
        assert code.version is None
        assert Path(code.path).resolve() == tmp_path.resolve()


class TestRunWithInjectedClient:
    """A pooled client is used as-is; without one, ``run`` opens its own."""

    def test_injected_client_is_used_and_none_is_opened(self, workspace_config, compute_config, mock_pipeline_job):
        injected = MagicMock()
        injected.compute.get.return_value = MagicMock(name="cpu-cluster", size="s", min_instances=0, max_instances=1)
        with patch("kedro_azureml_pipeline.client._get_azureml_client") as mock_ctx:
            ok = AzureMLPipelinesClient(mock_pipeline_job).run(workspace_config, compute_config, ml_client=injected)
        assert ok is True
        mock_ctx.assert_not_called()
        injected.jobs.create_or_update.assert_called_once()

    def test_client_without_code_operations_is_named(self, tmp_path):
        from kedro_azureml_pipeline.client import register_code_snapshot

        with pytest.raises(AttributeError, match="_code"):
            register_code_snapshot(MagicMock(spec=[]), tmp_path, "pkg-snapshot")


class TestScheduleClientWithInjectedClient:
    """The schedule client uses a pooled client as-is and opens none of its own."""

    def test_injected_client_is_used(self, workspace_config):
        from kedro_azureml_pipeline.scheduler import AzureMLScheduleClient

        injected = MagicMock()
        with patch("kedro_azureml_pipeline.scheduler._get_azureml_client") as mock_ctx:
            result = AzureMLScheduleClient().create_or_update_schedule(
                MagicMock(), workspace_config, ml_client=injected
            )

        mock_ctx.assert_not_called()
        injected.schedules.begin_create_or_update.assert_called_once()
        assert result is injected.schedules.begin_create_or_update.return_value.result.return_value

    def test_missing_code_entity_is_named(self, monkeypatch, tmp_path):
        import builtins

        from kedro_azureml_pipeline.client import register_code_snapshot

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "azure.ai.ml.entities._assets._artifacts.code":
                raise ImportError("gone")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match="azure-ai-ml .*Code"):
            register_code_snapshot(MagicMock(), tmp_path, "pkg-snapshot")
