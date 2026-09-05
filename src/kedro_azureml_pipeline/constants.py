"""Shared constants for the Kedro AzureML Pipeline plugin.

See Also
--------
[KedroAzureMLConfig][kedro_azureml_pipeline.config.KedroAzureMLConfig] : Top-level plugin configuration.
[AzureMLPipelineGenerator][kedro_azureml_pipeline.generator.AzureMLPipelineGenerator] : Reads constants during pipeline generation.
[MlflowAzureMLHook][kedro_azureml_pipeline.mlflow_hook.MlflowAzureMLHook] : Uses MLflow env var constants.
"""

DISTRIBUTED_CONFIG_FIELD = "__kedro_azureml_distributed_config__"
PARAMS_PREFIX = "params:"

# Worker pool size for `kedro azureml run --concurrent`. A constant rather than
# a flag: one number to tune, and small enough that a batch stays well under the
# workspace's request limits.
CONCURRENT_SUBMIT_WORKERS = 4

# MLflow integration env vars (set by generator, read by MlflowAzureMLHook)
KEDRO_AZUREML_MLFLOW_ENABLED = "KEDRO_AZUREML_MLFLOW_ENABLED"
KEDRO_AZUREML_MLFLOW_RUN_NAME = "KEDRO_AZUREML_MLFLOW_RUN_NAME"
KEDRO_AZUREML_MLFLOW_EXPERIMENT_NAME = "KEDRO_AZUREML_MLFLOW_EXPERIMENT_NAME"
KEDRO_AZUREML_MLFLOW_NODE_NAME = "KEDRO_AZUREML_MLFLOW_NODE_NAME"

# Run-outcome notification env vars (set by generator, read by NotificationHook)
KEDRO_AZUREML_NOTIFY = "KEDRO_AZUREML_NOTIFY"
KEDRO_AZUREML_NOTIFY_START = "KEDRO_AZUREML_NOTIFY_START"
KEDRO_AZUREML_NOTIFY_OUTCOME = "KEDRO_AZUREML_NOTIFY_OUTCOME"
KEDRO_AZUREML_NOTIFY_SIBLINGS = "KEDRO_AZUREML_NOTIFY_SIBLINGS"
