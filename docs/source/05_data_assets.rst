Azure Data Assets
=================

``kedro-azure-ml`` adds support for two datasets that can be used in the Kedro catalog.

``AzureMLAssetDataset`` enables using Azure ML Folder/File datasets for remote and local runs.
Currently only the `uri_file` and `uri_folder` types are supported. Because of limitations of the Azure ML SDK, the `uri_file` type can only be used for pipeline inputs,
not for outputs. The `uri_folder` type can be used for both inputs and outputs.

The ``AzureMLAssetDataset`` supports specifying exact dataset versions using the ``azureml_version`` parameter. If not specified, the latest version will be used automatically.

Apart from these, ``kedro-azure-ml`` also adds the ``AzureMLPipelineDataset`` which is used to pass data between
pipeline nodes when the pipeline is run on Azure ML.
By default, data is then saved and loaded using the ``PickleDataset`` as underlying dataset.
Any other underlying dataset can be used instead by adding a ``AzureMLPipelineDataset`` to the catalog.

All of these can be found under the `kedro_azure_ml.datasets`_ module.

For details on usage, see the :ref:`API Reference` below

Dataset Versioning
^^^^^^^^^^^^^^^^^^^

The ``AzureMLAssetDataset`` supports specifying exact Azure ML dataset versions using the ``azureml_version`` parameter in your ``catalog.yml``:

.. code-block:: yaml

    # Use a specific version
    my_dataset:
      type: kedro_azure_ml.datasets.AzureMLAssetDataset
      azureml_dataset: my-dataset-from-azureml
      azureml_version: "100"
      root_dir: data/01_raw/some_data
      dataset:
        type: pandas.ParquetDataset
        filepath: .

    # Use latest version (default behavior)
    my_latest_dataset:
      type: kedro_azure_ml.datasets.AzureMLAssetDataset
      azureml_dataset: my-dataset-from-azureml
      root_dir: data/01_raw/some_data
      dataset:
        type: pandas.ParquetDataset
        filepath: .

**Note**: The ``azureml_version`` parameter accepts both string and integer values (e.g., ``"100"`` or ``100``). If omitted, the latest available version will be used.

.. _`kedro_azure_ml.datasets`: https://github.com/stateful-y/kedro-azure-ml/blob/master/kedro_azure_ml/datasets

.. _`API Reference`:

API Reference
-------------

Pipeline data passing
^^^^^^^^^^^^^

⚠️ Cannot be used when run locally.

.. autoclass:: kedro_azure_ml.datasets.AzureMLPipelineDataset
    :members:

-----------------


Asset Dataset
^^^^^^^^^^^^^

✅ Can be used for both remote and local runs.

.. autoclass:: kedro_azure_ml.datasets.asset_dataset.AzureMLAssetDataset
    :members:

