# How to Configure Kedro AzureML Pipeline

This guide shows you how to set up and customize Kedro AzureML Pipeline for your project.

## Basic Configuration

```python
from kedro_azureml_pipeline import [MainClass]

instance = [MainClass](
    option_1="value",
    option_2=True,
)
```

## Configuration File

```yaml
# config.yaml
kedro_azureml_pipeline:
  option_1: value
  option_2: true
```

## Advanced Configuration

Describe advanced configuration patterns here: environment variables, multiple environments, runtime overrides.

## See Also

- [Concepts](../explanation/concepts.md) - understand the design
- [API Reference](../reference/api.md) - full list of options
