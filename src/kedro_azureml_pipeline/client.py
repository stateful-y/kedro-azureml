"""Azure ML pipeline client for job submission."""

import json
import logging
import os
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from azure.ai.ml import MLClient
from azure.ai.ml.entities import Job
from azure.ai.ml.entities._assets._artifacts.code import Code
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ServiceRequestError
from azure.identity import CredentialUnavailableError, DefaultAzureCredential, InteractiveBrowserCredential

from kedro_azureml_pipeline.config import WorkspaceConfig

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

logger = logging.getLogger(__name__)


def get_azureml_credentials() -> "TokenCredential":
    """Obtain Azure credentials for Azure ML access.

    Tries ``DefaultAzureCredential`` first (excluding managed identity
    on AzureML compute instances). Falls back to
    ``InteractiveBrowserCredential`` on failure.

    Returns
    -------
    TokenCredential
        Azure credential object.

    See Also
    --------
    [AzureMLPipelinesClient][kedro_azureml_pipeline.client.AzureMLPipelinesClient] : Uses credentials for job submission.
    [AzureMLScheduleClient][kedro_azureml_pipeline.scheduler.AzureMLScheduleClient] : Uses credentials for schedule management.
    """
    try:
        # On a AzureML compute instance, the managed identity will take precedence,
        # while it does not have enough permissions.
        # So, if we are on an AzureML compute instance, we disable the managed identity.
        is_azureml_managed_identity = "MSI_ENDPOINT" in os.environ
        credential = DefaultAzureCredential(exclude_managed_identity_credential=is_azureml_managed_identity)
        # Check if given credential can get token successfully.
        credential.get_token("https://management.azure.com/.default")
    except (ClientAuthenticationError, CredentialUnavailableError):
        # Fall back to InteractiveBrowserCredential in case DefaultAzureCredential not work
        credential = InteractiveBrowserCredential()
    return credential


@contextmanager
def _get_azureml_client(config: WorkspaceConfig):
    """Create a temporary ``MLClient`` scoped to *config*.

    Parameters
    ----------
    config : WorkspaceConfig
        Workspace connection details.

    Yields
    ------
    MLClient
        Authenticated Azure ML client.
    """
    client_config = {
        "subscription_id": config.subscription_id,
        "resource_group": config.resource_group,
        "workspace_name": config.name,
    }

    credential = get_azureml_credentials()

    with TemporaryDirectory() as tmp_dir:
        config_path = Path(tmp_dir) / "config.json"
        config_path.write_text(json.dumps(client_config))
        ml_client = MLClient.from_config(credential=credential, path=str(config_path.absolute()))
        yield ml_client


class BatchClients:
    """One credential and one ``MLClient`` per workspace, shared across a batch.

    ``kedro azureml run`` submits several jobs per invocation. Opening a client
    per job probes the credential chain and fetches a token each time; this
    pool does that once and hands the same client to every submission that
    targets the same workspace.

    See Also
    --------
    [AzureMLPipelinesClient][kedro_azureml_pipeline.client.AzureMLPipelinesClient] : Accepts a pooled client via ``run``.
    [register_code_snapshot][kedro_azureml_pipeline.client.register_code_snapshot] : Registers the snapshot through a pooled client.
    """

    def __init__(self) -> None:
        self._credential: TokenCredential | None = None
        self._clients: dict[tuple[str, str, str], MLClient] = {}

    @staticmethod
    def key(config: WorkspaceConfig) -> tuple[str, str, str]:
        """Return the identity of a workspace: subscription, resource group, name."""
        return (config.subscription_id, config.resource_group, config.name)

    def get(self, config: WorkspaceConfig) -> MLClient:
        """Return the client for *config*, creating it on first use.

        Parameters
        ----------
        config : WorkspaceConfig
            Workspace connection details.

        Returns
        -------
        MLClient
            The shared client for that workspace.
        """
        key = self.key(config)
        if key not in self._clients:
            if self._credential is None:
                self._credential = get_azureml_credentials()
            self._clients[key] = MLClient(
                self._credential,
                subscription_id=config.subscription_id,
                resource_group_name=config.resource_group,
                workspace_name=config.name,
            )
        return self._clients[key]


def _code_operations(ml_client: MLClient):
    """Return the client's code-asset operations.

    ``MLClient`` exposes no public property for them; ``_code`` is the
    attribute its own job submission resolves code through, so it is the
    least likely private name to move.
    """
    operations = getattr(ml_client, "_code", None)
    if operations is None:
        msg = "MLClient exposes no code operations (expected the '_code' attribute); the azure-ai-ml layout changed."
        raise AttributeError(msg)
    return operations


def register_code_snapshot(ml_client: MLClient, staged_dir: str | Path, name: str) -> str:
    """Register *staged_dir* as one code asset and return its id.

    The SDK looks the directory's content hash up before uploading, so an
    unchanged snapshot resolves to the asset it already registered and
    uploads nothing. The version is left to the workspace, which increments
    it per new content under *name*.

    Parameters
    ----------
    ml_client : MLClient
        Client for the workspace the jobs will run in.
    staged_dir : str or Path
        The staged snapshot directory.
    name : str
        Asset name, typically ``<package>-snapshot``.

    Returns
    -------
    str
        The registered asset's id, usable as the ``code`` of any command step.

    See Also
    --------
    [stage_code_snapshot][kedro_azureml_pipeline.snapshot.stage_code_snapshot] : Produces the directory registered here.
    """
    registered = _code_operations(ml_client).create_or_update(Code(path=str(staged_dir), name=name))
    logger.info("Registered code snapshot %s:%s", registered.name, registered.version)
    return str(registered.id)


class AzureMLPipelinesClient:
    """Client wrapper for submitting Azure ML pipeline jobs.

    Parameters
    ----------
    azure_pipeline : Job
        Compiled Azure ML pipeline job.

    See Also
    --------
    [AzureMLPipelineGenerator][kedro_azureml_pipeline.generator.AzureMLPipelineGenerator] : Generates the pipeline job.
    [AzureMLScheduleClient][kedro_azureml_pipeline.scheduler.AzureMLScheduleClient] : Schedule-based submission.
    [WorkspaceConfig][kedro_azureml_pipeline.config.WorkspaceConfig] : Workspace config used by ``run``.
    """

    def __init__(self, azure_pipeline: Job):
        self.azure_pipeline = azure_pipeline

    def run(
        self,
        config: WorkspaceConfig,
        compute_config,
        wait_for_completion=False,
        on_job_scheduled: Callable[[Job], None] | None = None,
        display_name: str | None = None,
        compute_name: str | None = None,
        experiment_name: str | None = None,
        ml_client: MLClient | None = None,
    ) -> bool:
        """Submit the pipeline job to Azure ML.

        Parameters
        ----------
        config : WorkspaceConfig
            Workspace connection details.
        compute_config : ComputeConfig
            Compute cluster definitions.
        wait_for_completion : bool
            If ``True``, block until the run finishes.
        on_job_scheduled : callable or None
            Callback invoked with the ``Job`` after scheduling.
        display_name : str or None
            Override display name in the Azure ML portal.
        compute_name : str or None
            Override compute cluster name.
        experiment_name : str or None
            Azure ML experiment name.
        ml_client : MLClient or None
            Client to submit through. When ``None``, one is opened from
            *config* for this call.

        Returns
        -------
        bool
            ``True`` if the job completed or was submitted successfully.

        Raises
        ------
        ValueError
            If the compute cluster does not exist.
        """
        if not experiment_name:
            logger.warning(
                "No experiment_name provided. Set it in mlflow.yml "
                "(tracking.experiment.name) or pass --experiment-name on the CLI. "
                "Azure ML will use a default experiment name."
            )
        if ml_client is not None:
            return self._submit(
                ml_client,
                compute_config,
                wait_for_completion,
                on_job_scheduled,
                display_name,
                compute_name,
                experiment_name,
            )
        with _get_azureml_client(config) as own_client:
            return self._submit(
                own_client,
                compute_config,
                wait_for_completion,
                on_job_scheduled,
                display_name,
                compute_name,
                experiment_name,
            )

    def _submit(
        self,
        ml_client: MLClient,
        compute_config,
        wait_for_completion: bool,
        on_job_scheduled: Callable[[Job], None] | None,
        display_name: str | None,
        compute_name: str | None,
        experiment_name: str | None,
    ) -> bool:
        """Submit through *ml_client*; return ``True`` on success, or on submission when not waiting."""
        effective_cluster_name = compute_name or compute_config.root["__default__"].cluster_name
        cluster = ml_client.compute.get(effective_cluster_name)
        if not cluster:
            raise ValueError(f"Cluster {effective_cluster_name} does not exist")

        logger.info(
            f"Creating job on cluster {cluster.name} ({cluster.size}, min instances: {cluster.min_instances}, "
            f"max instances: {cluster.max_instances})"
        )

        if display_name:
            self.azure_pipeline.display_name = display_name

        pipeline_job = ml_client.jobs.create_or_update(
            self.azure_pipeline,
            experiment_name=experiment_name,
            compute=cluster,
        )

        if on_job_scheduled:
            on_job_scheduled(pipeline_job)

        if wait_for_completion:
            try:
                ml_client.jobs.stream(pipeline_job.name)
                return True
            except (HttpResponseError, ServiceRequestError):
                logger.exception("Error while running the pipeline", exc_info=True)
                return False
        else:
            return True
