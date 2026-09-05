"""Implementation helpers for CLI commands."""

import importlib
import json
import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import click

from kedro_azureml_pipeline.factory import enumerate_jobs, is_factory, resolve_jobs
from kedro_azureml_pipeline.generator import AzureMLPipelineGenerator
from kedro_azureml_pipeline.manager import KedroContextManager
from kedro_azureml_pipeline.utils import CliContext

if TYPE_CHECKING:
    from kedro_azureml_pipeline.client import BatchClients
    from kedro_azureml_pipeline.config import KedroAzureMLConfig, WorkspaceConfig
    from kedro_azureml_pipeline.config.models import ScheduleConfig

logger = logging.getLogger(__name__)


def _read_mlflow_experiment_name(mgr: KedroContextManager) -> str | None:
    """Read experiment name from ``mlflow.yml`` via the Kedro config loader.

    Parameters
    ----------
    mgr : KedroContextManager
        Active context manager.

    Returns
    -------
    str or None
        Experiment name, or ``None`` if not configured.
    """
    try:
        mlflow_config = mgr.context.config_loader["mlflow"]
        name = mlflow_config.get("tracking", {}).get("experiment", {}).get("name")
        if name:
            logger.info(f"Using experiment name from mlflow.yml: {name}")
            return name
        logger.warning("mlflow.yml found but tracking.experiment.name is not set.")
    except (KeyError, TypeError):
        logger.info("No mlflow.yml configuration found. Experiment name must be provided via --experiment-name.")
    return None


def parse_runtime_params(params, silent=False):
    """Parse a JSON string of runtime parameters.

    Parameters
    ----------
    params : str
        JSON string of parameters, or falsy value.
    silent : bool
        Suppress the report when ``True``.

    Returns
    -------
    dict or None
        Parsed parameters dictionary, or ``None`` when *params* is
        empty or falsy.
    """
    if params and (parameters := json.loads(params.strip("'"))):
        if not silent:
            # Logged rather than echoed: this function is shared by the interactive
            # submit path and by `execute`, which runs a node inside a step
            # container. On stdout the block carried no timestamp, level or node
            # identity, could not be levelled down, and was four unattributable
            # lines per step in the consuming project's merged job log.
            logger.info("Running with extra parameters:\n%s", json.dumps(parameters, indent=4))
    else:
        parameters = None
    return parameters


def _merge_job_params(cli_params: str, job_config) -> str:
    """Merge job-level params with CLI params (CLI wins).

    Parameters
    ----------
    cli_params : str
        Raw CLI ``--params`` JSON string.
    job_config : JobConfig
        Job configuration potentially containing ``params``.

    Returns
    -------
    str
        Merged params as a JSON string, or empty string when none.
    """
    job_params = job_config.params or {}
    cli_parsed = parse_runtime_params(cli_params, silent=True) or {}
    merged = {**job_params, **cli_parsed}
    return json.dumps(merged) if merged else ""


def warn_about_ignore_files():
    """Emit warnings about ``.amlignore`` and ``.gitignore`` files.

    Checks the current working directory for ignore files that control
    which source files are uploaded to Azure ML.
    """
    aml_ignore = Path.cwd().joinpath(".amlignore")
    git_ignore = Path.cwd().joinpath(".gitignore")
    if aml_ignore.exists():
        ignore_contents = aml_ignore.read_text().strip()
        if not ignore_contents:
            click.echo(
                click.style(
                    f".amlignore file is empty, which means all of the files from {Path.cwd()}"
                    "\nwill be uploaded to Azure ML. Make sure that you excluded sensitive files first!",
                    fg="yellow",
                )
            )
    elif git_ignore.exists():
        ignore_contents = git_ignore.read_text().strip()
        if ignore_contents:
            click.echo(
                click.style(
                    ".gitignore file detected, ignored files will not be uploaded to Azure ML"
                    "\nWe recommend to use .amlignore instead of .gitignore when working with Azure ML"
                    "\nSee https://github.com/MicrosoftDocs/azure-docs/blob/047cb7f625920183438f3e66472014ac2ebab098/includes/machine-learning-amlignore-gitignore.md",  # noqa
                    fg="yellow",
                )
            )


def verify_configuration_directory_for_azure(click_context, ctx: CliContext):
    """Check that the Kedro environment config directory is non-empty.

    If the directory is missing, empty, or contains only empty files,
    the user is prompted to continue or abort.

    Parameters
    ----------
    click_context : click.Context
        Active Click context (used for ``exit``).
    ctx : CliContext
        CLI context containing the Kedro environment name.
    """
    conf_dir = Path.cwd().joinpath(f"conf/{ctx.env}")

    exists = conf_dir.exists() and conf_dir.is_dir()
    is_empty = True
    has_only_empty_files = True

    if exists:
        for p in conf_dir.iterdir():
            is_empty = False
            if p.is_file():
                has_only_empty_files = p.lstat().st_size == 0

            if not has_only_empty_files:
                break

    msg = f"Configuration folder for your Kedro environment {conf_dir} "
    if not exists:
        msg += "does not exist or is not a directory,"
    if is_empty:
        msg += "is empty,"
    elif has_only_empty_files:
        msg += "contains only empty files,"

    if is_empty or has_only_empty_files:
        msg += (
            "\nwhich might cause issues when running in Azure ML."
            + "\nEither use different environment or provide non-empty configuration for your env."
            + "\nContinue?"
        )
        if not click.confirm(click.style(msg, fg="yellow")):
            click_context.exit(2)


def parse_extra_env_params(extra_env):
    """Validate and parse ``KEY=VALUE`` environment variable strings.

    Parameters
    ----------
    extra_env : iterable of str
        Strings in ``KEY=VALUE`` format.

    Returns
    -------
    dict of str to str
        Mapping of variable names to values.

    Raises
    ------
    ValueError
        If any entry does not match the expected ``KEY=VALUE`` format.
    """
    for entry in extra_env:
        if not re.match("[A-Za-z0-9_]+=.*", entry):
            raise ValueError(f"Invalid env-var: {entry}, expected format: KEY=VALUE")

    return {(e := entry.split("=", maxsplit=1))[0]: e[1] for entry in extra_env}


def parse_display_name_overrides(entries, job_names) -> dict[str, str]:
    """Parse ``JOB=NAME`` display name overrides against the selected jobs.

    Parameters
    ----------
    entries : iterable of str
        Strings in ``JOB=NAME`` format; the split is on the first ``=`` so a
        name may itself contain ``=``.
    job_names : iterable of str
        The jobs selected for this invocation. Every override must name one
        of them: a misspelled job would otherwise leave that job silently
        unrenamed.

    Returns
    -------
    dict of str to str
        Mapping of job names to their display names.

    Raises
    ------
    click.UsageError
        If an entry has no ``=`` or names a job outside *job_names*.
    """
    selected = set(job_names)
    overrides: dict[str, str] = {}
    for entry in entries:
        job, sep, name = entry.partition("=")
        if not sep or not job or not name:
            raise click.UsageError(f"Invalid --display-name: {entry!r}, expected format: JOB=NAME")
        if job not in selected:
            raise click.UsageError(f"--display-name names job {job!r}, which is not among the selected -j jobs")
        overrides[job] = name
    return overrides


def dynamic_import_job_schedule_func_from_str(
    ctx: click.Context,
    param: click.Parameter,
    import_str: str,
) -> Callable | None:
    """Dynamically import a callback function from a dotted path.

    Parameters
    ----------
    ctx : click.Context
        Active Click context.
    param : click.Parameter
        Click parameter that triggered this callback.
    import_str : str
        Import path in ``module.path:function_name`` format.

    Returns
    -------
    callable or None
        Imported function, or ``None`` if *import_str* is ``None``.

    Raises
    ------
    click.BadParameter
        If the format is invalid, the module cannot be imported,
        or the attribute is not callable.
    """
    # base case: no callback
    if import_str is None:
        return

    # check format
    module_str, _, attrs_str = import_str.partition(":")
    if not module_str or not attrs_str:
        raise click.BadParameter("import_str must be in format <module>:<function>", param=param)

    try:
        module = importlib.import_module(module_str)
        instance = getattr(module, attrs_str)

        # fails if we try to import an attribute that is not a function
        if not callable(instance):
            raise click.BadParameter(f"The attribute '{attrs_str}' is not a callable function.", param=param)

        return instance
    except (ImportError, AttributeError, ValueError) as e:
        # catches errors if module or attribute does not exist
        raise click.BadParameter(f"Error: {e}", param=param) from e


def default_job_callback(job):
    """Print the Azure ML Studio URL after a job is scheduled.

    Parameters
    ----------
    job : Job
        The Azure ML pipeline job that was created.
    """
    click.echo(job.studio_url)


def compile_job_pipelines(
    ctx: CliContext,
    aml_env: str | None,
    params: str,
    extra_env: dict[str, str],
    load_versions: dict[str, str],
    job_names: list[str],
    output: str,
    all_jobs: bool = False,
    check: bool = False,
):
    """Compile pipelines for named jobs into YAML files, or check they compile.

    Parameters
    ----------
    ctx : CliContext
        CLI context containing the Kedro environment and metadata.
    aml_env : str or None
        Azure ML Environment override.
    params : str
        Runtime parameters override as a JSON string.
    extra_env : dict of str to str
        Extra environment variables to inject into steps.
    load_versions : dict of str to str
        Dataset version overrides.
    job_names : list of str
        Names of jobs to compile (must exist in config). Ignored when *all_jobs*.
    output : str
        Output file path. Suffixed with the job name for multiple jobs.
    all_jobs : bool
        Compile every resolved job (literal and factory-derived) instead of
        *job_names*.
    check : bool
        Compile in memory without writing output, attempting every job and
        raising at the end if any failed (does not abort on the first failure).

    Raises
    ------
    click.ClickException
        If no ``jobs`` section is found, a requested job is missing, or (in
        *check* mode) any job fails to compile.
    """
    with KedroContextManager(env=ctx.env, runtime_params=parse_runtime_params(params, True)) as mgr:
        config = mgr.plugin_config

        if not config.jobs:
            raise click.ClickException("No 'jobs' section found in azureml.yml config.")

        from kedro.framework.project import pipelines

        try:
            selected_jobs = (
                enumerate_jobs(config, pipelines) if all_jobs else resolve_jobs(config, job_names, pipelines)
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        output_path = Path(output)
        multi = len(selected_jobs) > 1

        # Read default experiment name from mlflow.yml
        default_experiment_name = _read_mlflow_experiment_name(mgr)

        failures: list[str] = []
        for job_name, job_config in selected_jobs.items():
            pipeline_opts = job_config.pipeline

            # Resolve experiment name: job > mlflow.yml > None
            job_experiment_name = job_config.experiment_name or default_experiment_name
            mlflow_run_name = job_config.display_name or job_name

            try:
                generator = AzureMLPipelineGenerator(
                    pipeline_opts.pipeline_name,
                    ctx.env,
                    config,
                    mgr.context.params,
                    mgr.context.catalog,
                    aml_env,
                    _merge_job_params(params, job_config),
                    extra_env=extra_env,
                    load_versions=load_versions,
                    filter_options=pipeline_opts,
                    mlflow_run_name=mlflow_run_name,
                    experiment_name=job_experiment_name,
                    limits_config=job_config.limits,
                )
                az_pipeline = generator.generate()
            except Exception as exc:  # noqa: BLE001
                if not check:
                    raise
                failures.append(job_name)
                click.echo(click.style(f"Job '{job_name}' failed to compile: {exc}", fg="red"))
                continue

            if check:
                click.echo(f"Compiled job '{job_name}' OK")
            else:
                dest = output_path.with_stem(f"{output_path.stem}_{job_name}") if multi else output_path
                dest.write_text(str(az_pipeline))
                click.echo(f"Compiled job '{job_name}' to {dest}")

        if check:
            if failures:
                raise click.ClickException(f"{len(failures)} job(s) failed to compile: {', '.join(failures)}")
            click.echo(click.style(f"All {len(selected_jobs)} job(s) compiled successfully.", fg="green"))


class SnapshotRegistrar:
    """Stage the code snapshot once and register it once per workspace.

    Used as the ``code_resolver`` of [`_prepare_jobs`][kedro_azureml_pipeline.cli.functions._prepare_jobs]:
    the first job that needs code stages the snapshot, the first job per
    workspace registers it, and every later job reuses the asset id. Nothing
    happens when ``execution.code_directory`` is unset.

    Parameters
    ----------
    stack : ExitStack
        Owns the staged directory, so it lives until the batch is done.
    clients : BatchClients
        Client pool the registration goes through.
    """

    def __init__(self, stack: ExitStack, clients: "BatchClients") -> None:
        self._stack = stack
        self._clients = clients
        self._staged: Path | None = None
        self._code_ids: dict[tuple[str, str, str], str] = {}

    def __call__(self, config: "KedroAzureMLConfig", workspace: "WorkspaceConfig") -> str | None:
        """Return the code asset id for *workspace*, staging and registering on first use."""
        from kedro_azureml_pipeline.client import BatchClients, register_code_snapshot
        from kedro_azureml_pipeline.snapshot import stage_code_snapshot

        code_directory = config.execution.code_directory
        if code_directory is None:
            return None
        key = BatchClients.key(workspace)
        if key not in self._code_ids:
            if self._staged is None:
                # Relative to the working directory, which is where the SDK
                # would have resolved the per-step path.
                self._staged = self._stack.enter_context(stage_code_snapshot(Path.cwd() / code_directory))
            self._code_ids[key] = register_code_snapshot(self._clients.get(workspace), self._staged)
        return self._code_ids[key]


@contextmanager
def _prepare_jobs(
    ctx: CliContext,
    aml_env: str | None,
    params: str,
    extra_env: dict[str, str],
    load_versions: dict[str, str],
    job_names: list[str] | None,
    workspace_override: str | None = None,
    code_resolver: "Callable[[KedroAzureMLConfig, WorkspaceConfig], str | None] | None" = None,
    display_names: dict[str, str] | None = None,
):
    """Context manager that loads config, validates jobs, and generates pipelines.

    Parameters
    ----------
    ctx : CliContext
        CLI context containing the Kedro environment and metadata.
    aml_env : str or None
        Azure ML Environment override.
    params : str
        Runtime parameters override as a JSON string.
    extra_env : dict of str to str
        Extra environment variables to inject into steps.
    load_versions : dict of str to str
        Dataset version overrides.
    job_names : list of str or None
        If given, only prepare these jobs.
    workspace_override : str or None
        Named workspace override for all jobs in this batch.
    code_resolver : callable or None
        Called with the plugin config and each job's resolved workspace
        before that job is generated; its result becomes the ``code`` of
        every step. ``None`` leaves each step on ``execution.code_directory``,
        which is what compile-only commands want.
    display_names : dict of str to str or None
        Per-job display name overrides. A job named here is submitted under
        that name (pipeline display name and MLflow run name both); the rest
        keep their configured ``display_name``.

    Yields
    ------
    tuple
        ``(config, selected_jobs, prepared)`` where *prepared* is a dict
        mapping job names to ``(job_experiment_name, pipeline_job, job_config)``
        tuples.
    """
    with KedroContextManager(env=ctx.env, runtime_params=parse_runtime_params(params, True)) as mgr:
        config = mgr.plugin_config

        if not config.jobs:
            raise click.ClickException(
                "No 'jobs' section found in azureml.yml config. Define jobs to use this command."
            )

        from kedro.framework.project import pipelines

        try:
            selected_jobs = (
                resolve_jobs(config, job_names, pipelines) if job_names else enumerate_jobs(config, pipelines)
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        # Read default experiment name from mlflow.yml
        default_experiment_name = _read_mlflow_experiment_name(mgr)

        prepared: dict[str, tuple] = {}
        for job_name, job_config in selected_jobs.items():
            job_experiment_name = job_config.experiment_name or default_experiment_name
            display_name = (display_names or {}).get(job_name) or job_config.display_name
            mlflow_run_name = display_name or job_name

            code = None
            if code_resolver is not None:
                workspace = config.workspace.resolve(workspace_override or job_config.workspace)
                code = code_resolver(config, workspace)

            pipeline_opts = job_config.pipeline
            generator = AzureMLPipelineGenerator(
                pipeline_opts.pipeline_name,
                ctx.env,
                config,
                mgr.context.params,
                mgr.context.catalog,
                aml_env,
                _merge_job_params(params, job_config),
                extra_env=extra_env,
                load_versions=load_versions,
                filter_options=pipeline_opts,
                mlflow_run_name=mlflow_run_name,
                experiment_name=job_experiment_name,
                limits_config=job_config.limits,
                code=code,
            )
            pipeline_job = generator.generate()

            if display_name:
                pipeline_job.display_name = display_name

            prepared[job_name] = (job_experiment_name, pipeline_job, job_config)

        yield config, selected_jobs, prepared


def run_jobs(
    ctx: CliContext,
    aml_env: str | None,
    params: str,
    extra_env: dict[str, str],
    load_versions: dict[str, str],
    job_names: list[str] | None,
    dry_run: bool,
    wait_for_completion: bool = False,
    on_job_scheduled: Callable | None = None,
    workspace_override: str | None = None,
    concurrent: bool = False,
    display_names: dict[str, str] | None = None,
):
    """Run jobs immediately, ignoring any configured schedule.

    Parameters
    ----------
    ctx : CliContext
        CLI context containing the Kedro environment and metadata.
    aml_env : str or None
        Azure ML Environment override.
    params : str
        Runtime parameters override as a JSON string.
    extra_env : dict of str to str
        Extra environment variables to inject into steps.
    load_versions : dict of str to str
        Dataset version overrides.
    job_names : list of str or None
        If given, only run these jobs.
    dry_run : bool
        Preview mode: print what would happen without calling Azure ML.
    wait_for_completion : bool
        Block until the pipeline run completes.
    on_job_scheduled : callable or None
        Callback invoked after each job is submitted.
    workspace_override : str or None
        Named workspace override for all jobs in this batch.
    concurrent : bool
        Submit the jobs from a pool of ``CONCURRENT_SUBMIT_WORKERS`` threads
        and attempt every one of them. The default submits in order and
        stops at the first failure, which dependent chains rely on.
    display_names : dict of str to str or None
        Per-job display name overrides, as parsed by
        :func:`parse_display_name_overrides`. Lets one invocation submit jobs
        from a shared factory under distinct names.

    Returns
    -------
    bool
        ``True`` if all jobs ran successfully.
    """
    from kedro_azureml_pipeline.client import AzureMLPipelinesClient, BatchClients
    from kedro_azureml_pipeline.constants import CONCURRENT_SUBMIT_WORKERS

    with ExitStack() as stack:
        clients = BatchClients()
        code_resolver = None if dry_run else SnapshotRegistrar(stack, clients)

        with _prepare_jobs(
            ctx,
            aml_env,
            params,
            extra_env,
            load_versions,
            job_names,
            workspace_override,
            code_resolver,
            display_names=display_names,
        ) as (config, selected_jobs, prepared):
            job_order = list(selected_jobs)

            def submit(job_name: str) -> bool:
                """Submit one prepared job; report and return ``False`` on any failure."""
                try:
                    job_experiment_name, pipeline_job, job_config = prepared[job_name]
                    workspace = config.workspace.resolve(workspace_override or job_config.workspace)
                    pipeline_opts = job_config.pipeline

                    if dry_run:
                        # The display name is what the studio and MLflow will show, so
                        # a caller can check a computed override before submitting.
                        click.echo(
                            f"[DRY RUN] Would run job '{job_name}' immediately "
                            f"as '{pipeline_job.display_name or job_name}' "
                            f"(pipeline '{pipeline_opts.pipeline_name}')"
                        )
                        return True

                    job_callback = on_job_scheduled or default_job_callback
                    az_client = AzureMLPipelinesClient(pipeline_job)
                    is_ok = az_client.run(
                        workspace,
                        config.compute,
                        wait_for_completion=wait_for_completion,
                        on_job_scheduled=job_callback,
                        compute_name=job_config.compute,
                        experiment_name=job_experiment_name,
                        ml_client=clients.get(workspace),
                    )
                    if is_ok:
                        click.echo(click.style(f"Job '{job_name}' submitted for immediate execution", fg="green"))
                    return is_ok
                except Exception as e:
                    click.echo(click.style(f"Failed to run job '{job_name}': {e}", fg="red"))
                    logger.exception(f"Error running job '{job_name}'")
                    return False

            results: dict[str, bool] = {}
            if concurrent and not dry_run:
                # Independent jobs: attempt every one, no fail-fast.
                with ThreadPoolExecutor(max_workers=min(CONCURRENT_SUBMIT_WORKERS, len(job_order))) as pool:
                    results = dict(zip(job_order, pool.map(submit, job_order), strict=True))
            else:
                for index, job_name in enumerate(job_order):
                    results[job_name] = submit(job_name)
                    # Fail-fast: jobs in a batch are submitted in order and a later job
                    # typically consumes an earlier one's output (e.g. snapshot -> training
                    # -> inference). Once a job fails there is no point submitting the rest,
                    # so stop and report the ones we skipped.
                    if not results[job_name]:
                        skipped = job_order[index + 1 :]
                        if skipped:
                            click.echo(
                                click.style(
                                    f"Aborting batch: '{job_name}' failed; "
                                    f"skipping {len(skipped)} remaining job(s): {', '.join(skipped)}",
                                    fg="red",
                                )
                            )
                        break

            succeeded = sum(1 for v in results.values() if v)
            failed = sum(1 for v in results.values() if not v)
            skipped_count = len(job_order) - len(results)
            summary = f"\nRun summary: {succeeded} succeeded, {failed} failed"
            if skipped_count:
                summary += f", {skipped_count} skipped"
            summary += f" (out of {len(job_order)} selected)"
            click.echo(summary)

            return all(results.values()) and not skipped_count


def _schedule_entries(
    job_name: str, schedule: "ScheduleConfig | str | list[ScheduleConfig | str] | None"
) -> "list[tuple[str, ScheduleConfig | str]]":
    """Map a job's ``schedule`` (one, a list, or None) to ``(schedule_name, ref)`` pairs.

    A single schedule keeps the job name; a list gets a stable per-entry suffix
    (the named ref for strings, else the index); ``None`` yields no entries.
    Shared by schedule creation and deletion so the two never drift.
    """
    if schedule is None:
        return []
    refs = schedule if isinstance(schedule, list) else [schedule]
    entries: list[tuple[str, ScheduleConfig | str]] = []
    for index, ref in enumerate(refs):
        if len(refs) == 1:
            name = job_name
        elif isinstance(ref, str):
            name = f"{job_name}-{ref}"
        else:
            name = f"{job_name}-{index}"
        entries.append((name, ref))
    return entries


def delete_schedules(
    ctx: CliContext,
    job_names: list[str],
    dry_run: bool,
    workspace_override: str | None = None,
) -> bool:
    """Delete Azure ML schedules for the specified jobs.

    Job names are resolved through the job factories; each job's schedule
    name(s) mirror the creation convention (see ``_schedule_entries``). No
    pipeline compilation is performed.

    Parameters
    ----------
    ctx : CliContext
        CLI context containing the Kedro environment and metadata.
    job_names : list of str
        Job names whose schedules should be deleted.
    dry_run : bool
        Preview mode: print what would happen without calling Azure ML.
    workspace_override : str or None
        Named workspace override for all jobs in this batch.

    Returns
    -------
    bool
        ``True`` if all deletions succeeded (or were no-ops).
    """
    from kedro.framework.project import pipelines

    from kedro_azureml_pipeline.scheduler import AzureMLScheduleClient

    with KedroContextManager(env=ctx.env) as mgr:
        config = mgr.plugin_config

        if not config.jobs:
            raise click.ClickException(
                "No 'jobs' section found in azureml.yml config. Define jobs to use this command."
            )

        try:
            selected_jobs = resolve_jobs(config, job_names, pipelines)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        schedule_client = AzureMLScheduleClient()
        results: dict[str, bool] = {}

        for job_name, job_config in selected_jobs.items():
            try:
                workspace = config.workspace.resolve(workspace_override or job_config.workspace)
                # Mirror the creation naming: delete every schedule the job owns.
                # A job with no schedule config falls back to the bare job name
                # (the legacy 1:1 schedule name), so stale schedules can be pruned.
                schedule_names = [name for name, _ in _schedule_entries(job_name, job_config.schedule)] or [job_name]
                for schedule_name in schedule_names:
                    if dry_run:
                        click.echo(f"[DRY RUN] Would delete schedule '{schedule_name}'")
                    else:
                        schedule_client.delete_schedule(schedule_name, workspace)
                        click.echo(click.style(f"Schedule '{schedule_name}' deleted", fg="green"))
                results[job_name] = True

            except Exception as e:
                click.echo(click.style(f"Failed to delete schedule '{job_name}': {e}", fg="red"))
                logger.exception(f"Error deleting schedule '{job_name}'")
                results[job_name] = False

        succeeded = sum(1 for v in results.values() if v)
        failed = sum(1 for v in results.values() if not v)
        click.echo(f"\nDelete summary: {succeeded} succeeded, {failed} failed (out of {len(results)} schedules)")

        return all(results.values())


def schedule_jobs(
    ctx: CliContext,
    aml_env: str | None,
    params: str,
    extra_env: dict[str, str],
    load_versions: dict[str, str],
    job_names: list[str] | None,
    dry_run: bool,
    workspace_override: str | None = None,
):
    """Create or update persistent Azure ML schedules for jobs.

    Every selected job must have a ``schedule`` configured; otherwise
    a ``ClickException`` is raised.

    Parameters
    ----------
    ctx : CliContext
        CLI context containing the Kedro environment and metadata.
    aml_env : str or None
        Azure ML Environment override.
    params : str
        Runtime parameters override as a JSON string.
    extra_env : dict of str to str
        Extra environment variables to inject into steps.
    load_versions : dict of str to str
        Dataset version overrides.
    job_names : list of str or None
        If given, only schedule these jobs.
    dry_run : bool
        Preview mode: print what would happen without calling Azure ML.
    workspace_override : str or None
        Named workspace override for all jobs in this batch.

    Returns
    -------
    bool
        ``True`` if all schedules were created/updated successfully.
    """
    from kedro_azureml_pipeline.client import BatchClients
    from kedro_azureml_pipeline.scheduler import AzureMLScheduleClient

    with ExitStack() as stack:
        clients = BatchClients()
        code_resolver = None if dry_run else SnapshotRegistrar(stack, clients)
        with _prepare_jobs(
            ctx, aml_env, params, extra_env, load_versions, job_names, workspace_override, code_resolver
        ) as (config, selected_jobs, prepared):
            return _create_schedules_from_prepared(
                config, selected_jobs, prepared, dry_run, workspace_override, AzureMLScheduleClient(), clients
            )


def _create_schedules_from_prepared(
    config, selected_jobs, prepared, dry_run, workspace_override, schedule_client, clients
):
    """Create one Azure ML schedule per schedule entry of every prepared job; return whether all succeeded.

    *clients* is the batch's client pool, so every schedule of a workspace goes
    through the client that registered its code snapshot.
    """
    from kedro_azureml_pipeline.scheduler import build_job_schedule, build_trigger, resolve_schedule

    # Validate that all selected jobs have a schedule configured (None or [])
    missing_schedule = [name for name, cfg in selected_jobs.items() if not cfg.schedule]
    if missing_schedule:
        raise click.ClickException(
            f"Job(s) have no schedule configured: {', '.join(sorted(missing_schedule))}. "
            f"Add a schedule to the job config or use 'kedro azureml run' instead."
        )

    results: dict[str, bool] = {}

    for job_name in selected_jobs:
        try:
            job_experiment_name, pipeline_job, job_config = prepared[job_name]
            workspace = config.workspace.resolve(workspace_override or job_config.workspace)
            pipeline_opts = job_config.pipeline

            # Set experiment_name on the pipeline job so AzureML uses it
            # for scheduled runs (analogous to run_jobs passing it to
            # ml_client.jobs.create_or_update).
            if job_experiment_name:
                pipeline_job.experiment_name = job_experiment_name

            # A job may declare one schedule or a list; deploy one Azure ML
            # schedule (trigger) per entry, named via the shared convention.
            for schedule_name, schedule_ref in _schedule_entries(job_name, job_config.schedule):
                schedule_cfg = resolve_schedule(schedule_ref, config.schedules)
                trigger = build_trigger(schedule_cfg)
                job_schedule = build_job_schedule(
                    name=schedule_name,
                    trigger=trigger,
                    pipeline_job=pipeline_job,
                    display_name=job_config.display_name,
                    description=job_config.description,
                )

                if dry_run:
                    if schedule_cfg.cron:
                        trigger_desc = f"cron: {schedule_cfg.cron.expression}"
                    else:
                        rec = schedule_cfg.recurrence
                        assert rec is not None  # noqa: S101 -- ScheduleConfig invariant: exactly one of cron/recurrence
                        trigger_desc = f"recurrence: every {rec.interval} {rec.frequency}(s)"
                    click.echo(
                        f"[DRY RUN] Would create schedule '{schedule_name}' "
                        f"({trigger_desc}) for pipeline '{pipeline_opts.pipeline_name}'"
                    )
                else:
                    result = schedule_client.create_or_update_schedule(
                        job_schedule, workspace, ml_client=clients.get(workspace)
                    )
                    click.echo(click.style(f"Schedule '{result.name}' created/updated successfully", fg="green"))
            results[job_name] = True

        except Exception as e:
            click.echo(click.style(f"Failed to schedule job '{job_name}': {e}", fg="red"))
            logger.exception(f"Error scheduling job '{job_name}'")
            results[job_name] = False

    succeeded = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    click.echo(f"\nSchedule summary: {succeeded} succeeded, {failed} failed (out of {len(results)} jobs)")

    return all(results.values())


def _format_schedule(schedule: "ScheduleConfig | str | list[ScheduleConfig | str]") -> str:
    """Render a job's ``schedule`` (one, a list, or a named ref) as a compact summary.

    Named references print as-is; inline schedules print as ``cron:<expr>`` or
    ``recurrence:every <n> <freq>(s)``. Used only for the ``resolve-patterns``
    listing, so the output is human-readable rather than round-trippable.
    """
    refs = schedule if isinstance(schedule, list) else [schedule]
    parts: list[str] = []
    for ref in refs:
        if isinstance(ref, str):
            parts.append(ref)
        elif ref.cron:
            parts.append(f"cron:{ref.cron.expression}")
        else:
            rec = ref.recurrence
            assert rec is not None  # noqa: S101 -- ScheduleConfig requires exactly one of cron/recurrence
            parts.append(f"recurrence:every {rec.interval} {rec.frequency}(s)")
    return ", ".join(parts)


def resolve_patterns(ctx: CliContext) -> None:
    """Print the concrete jobs derived from the job factories and pipelines.

    The analogue of ``kedro catalog resolve-patterns``: renders every job factory
    against the active pipeline namespaces (plus any literal jobs) and lists the
    resulting job names with their schedule and node namespaces. No Azure ML
    connection is made.

    Parameters
    ----------
    ctx : CliContext
        CLI context containing the Kedro environment and metadata.
    """
    from kedro.framework.project import pipelines

    with KedroContextManager(env=ctx.env) as mgr:
        jobs = enumerate_jobs(mgr.plugin_config, pipelines)
        if not jobs:
            click.echo("No jobs resolved (no job factories or literal jobs in azureml.yml).")
            return
        for name in sorted(jobs):
            job = jobs[name]
            namespaces = ", ".join(job.pipeline.node_namespaces or [])
            detail = [f"pipeline={job.pipeline.pipeline_name}"]
            if namespaces:
                detail.append(f"namespaces=[{namespaces}]")
            if job.schedule:
                detail.append(f"schedule={_format_schedule(job.schedule)}")
            click.echo(f"{name}  ({'; '.join(detail)})")


def list_patterns(ctx: CliContext) -> None:
    """List the job-factory keys (``jobs`` keys containing ``{placeholder}`` markers).

    The analogue of ``kedro catalog list-patterns``. Literal (non-factory) job
    keys are not listed.

    Parameters
    ----------
    ctx : CliContext
        CLI context containing the Kedro environment and metadata.
    """
    with KedroContextManager(env=ctx.env) as mgr:
        patterns = sorted(key for key in mgr.plugin_config.jobs if is_factory(key))
        if not patterns:
            click.echo("No job factory patterns defined in azureml.yml.")
            return
        for pattern in patterns:
            click.echo(pattern)
