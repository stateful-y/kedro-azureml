# Kedro Azure ML Pipelines plugin

[![Python Version](https://img.shields.io/pypi/pyversions/kedro-azure-ml)](https://github.com/stateful-y/kedro-azure-ml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![SemVer](https://img.shields.io/badge/semver-2.0.0-green)](https://semver.org/)
[![PyPI version](https://badge.fury.io/py/kedro-azure-ml.svg)](https://pypi.org/project/kedro-azure-ml/)
[![Downloads](https://pepy.tech/badge/kedro-azure-ml)](https://pepy.tech/project/kedro-azure-ml)

## About
Following plugin enables running Kedro pipelines on Azure ML Pipelines service.

We support 2 workflows for running Kedro pipelines on Azure ML Pipelines, both backed by Azure ML Environments:
* For Data Scientists: fast, iterative development with **code upload** (only dependencies in the image, code uploaded at runtime)
* For MLOps: stable, repeatable workflows with **Docker image** (code baked into the image)

## Documentation

For detailed documentation refer to https://kedro-azure-ml.readthedocs.io/

## Usage guide

```
Usage: kedro azureml [OPTIONS] COMMAND [ARGS]...

Options:
  -e, --env TEXT  Environment to use.
  -h, --help      Show this message and exit.

Commands:
  compile  Compile job pipeline(s) into YAML format
  init     Creates basic configuration for Kedro AzureML plugin
  submit   Submit named jobs to Azure ML
```

## Quickstart
Follow **quickstart** section on [kedro-azure-ml.readthedocs.io](https://kedro-azure-ml.readthedocs.io/) to get up to speed with plugin usage.