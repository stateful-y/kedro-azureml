"""Hook that posts a job's run events to a webhook, once per job.

Every Kedro node runs as its own Azure ML step, so Kedro's pipeline hooks fire
once per node. The generator therefore stamps each step with the job's
notification definition and marks one root step as the *announcer* and one
leaf step as the *poster*. The announcer posts ``start``. A step that raises
posts ``failure``. The poster, once its own node is done, waits until every
sibling leaf has reached a terminal state and then posts ``success``, or a
failure summary for a sibling that died without reporting.

The hook is inert unless ``KEDRO_AZUREML_NOTIFY`` is present, which only the
generator sets, so a local ``kedro run`` never posts.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from kedro.framework.hooks import hook_impl

from kedro_azureml_pipeline.constants import (
    KEDRO_AZUREML_MLFLOW_EXPERIMENT_NAME,
    KEDRO_AZUREML_MLFLOW_NODE_NAME,
    KEDRO_AZUREML_NOTIFY,
    KEDRO_AZUREML_NOTIFY_OUTCOME,
    KEDRO_AZUREML_NOTIFY_SIBLINGS,
    KEDRO_AZUREML_NOTIFY_START,
)

logger = logging.getLogger(__name__)

POST_TIMEOUT_SECONDS = 10
POLL_INTERVAL_SECONDS = 15
ERROR_HEAD_CHARS = 250
TERMINAL_RUN_STATUSES = frozenset({"FINISHED", "FAILED", "KILLED"})
ROOT_RUN_TAG = "mlflow.rootRunId"
NODE_NAME_TAG = "kedro.node_name"
ERROR_TAG = "kedro.error"
_IDENTIFIER_VARS = (
    "AZUREML_RUN_ID",
    "AZUREML_ROOT_RUN_ID",
    "AZUREML_ARM_SUBSCRIPTION",
    "AZUREML_ARM_RESOURCEGROUP",
    "AZUREML_ARM_WORKSPACE_NAME",
)


@dataclass(frozen=True)
class NotificationEvent:
    """What a payload builder receives.

    Parameters
    ----------
    event : str
        ``"start"``, ``"success"``, ``"failure"``, or ``"unknown"`` when the
        poster gave up waiting for its siblings.
    job_name : str
        Name of the job in ``azureml.yml``.
    display_name : str
        Display name of the job.
    pipeline_name : str
        Registered Kedro pipeline name.
    kedro_env : str
        Kedro environment the step runs with.
    node : str or None
        Node the step ran; for ``failure`` the node that raised, or the sibling
        that died without reporting.
    error : str or None
        First characters of the exception for ``failure``; the pending sibling
        names for ``unknown``.
    root_run_id : str or None
        Azure ML root run id of the job.
    studio_url : str or None
        Azure ML Studio URL of the job, when the workspace identifiers are present.
    elapsed_seconds : float or None
        Seconds since the step's pipeline started, for outcome events.
    environment : Mapping of str to str
        Raw Azure ML identifiers present in the step environment.

    See Also
    --------
    [NotificationHook][kedro_azureml_pipeline.hooks.NotificationHook] : Builds and posts the event.
    [NotificationConfig][kedro_azureml_pipeline.config.NotificationConfig] : Names the payload builder.
    """

    event: str
    job_name: str
    display_name: str
    pipeline_name: str
    kedro_env: str
    node: str | None = None
    error: str | None = None
    root_run_id: str | None = None
    studio_url: str | None = None
    elapsed_seconds: float | None = None
    environment: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SiblingOutcome:
    """Result of waiting for the poster's sibling leaves.

    Parameters
    ----------
    finished : bool
        Every sibling reached ``FINISHED``.
    pending : tuple of str
        Siblings still not terminal when the wait cap expired.
    reported : tuple of str
        Siblings that failed after running their own error hook.
    unreported : tuple of str
        Siblings that failed or were cancelled without running their error hook.

    See Also
    --------
    [NotificationHook][kedro_azureml_pipeline.hooks.NotificationHook] : Turns this into the outcome message.
    """

    finished: bool
    pending: tuple[str, ...] = ()
    reported: tuple[str, ...] = ()
    unreported: tuple[str, ...] = ()


def studio_url(env: Mapping[str, str]) -> str | None:
    """Build the Azure ML Studio URL of the root run from the step environment.

    Parameters
    ----------
    env : Mapping of str to str
        The step's environment variables.

    Returns
    -------
    str or None
        The URL, or ``None`` when any identifier is missing.
    """
    root = env.get("AZUREML_ROOT_RUN_ID")
    subscription = env.get("AZUREML_ARM_SUBSCRIPTION")
    resource_group = env.get("AZUREML_ARM_RESOURCEGROUP")
    workspace = env.get("AZUREML_ARM_WORKSPACE_NAME")
    if not all((root, subscription, resource_group, workspace)):
        return None
    return (
        f"https://ml.azure.com/runs/{root}?wsid=/subscriptions/{subscription}"
        f"/resourcegroups/{resource_group}/workspaces/{workspace}"
    )


def default_payload(event: NotificationEvent) -> dict[str, Any]:
    """Render the plain ``{"text": ...}`` payload Slack incoming webhooks accept.

    Parameters
    ----------
    event : NotificationEvent
        The event to render.

    Returns
    -------
    dict of str to Any
        A mapping with a single ``text`` field.
    """
    where = f"{event.display_name} (pipeline {event.pipeline_name}, env {event.kedro_env})"
    if event.event == "start":
        text = f"[start] {where} started."
    elif event.event == "success":
        text = f"[success] {where} finished{_elapsed(event)}."
    elif event.event == "failure":
        text = f"[failure] {where} failed in node {event.node}{_elapsed(event)}: {event.error}"
    else:
        text = f"[unknown] {where}: outcome unknown{_elapsed(event)}. Still pending: {event.error}"
    link = event.studio_url or event.root_run_id
    if link:
        text += f" Run: {link}"
    return {"text": text}


def _elapsed(event: NotificationEvent) -> str:
    """Format the elapsed time of an outcome event as a suffix.

    Parameters
    ----------
    event : NotificationEvent
        The event whose ``elapsed_seconds`` to format.

    Returns
    -------
    str
        `` after Ns`` or an empty string.
    """
    if event.elapsed_seconds is None:
        return ""
    return f" after {int(event.elapsed_seconds)}s"


class NotificationHook:
    """Post a job's ``start``, ``success``, and ``failure`` events to a webhook.

    Lifecycle
    ---------
    1. ``before_pipeline_run``: records the start time; the announcer posts ``start``.
    2. ``on_pipeline_error``: the raising step posts ``failure``.
    3. ``after_pipeline_run``: the poster waits for its sibling leaves through
       MLflow, then posts ``success``, a failure summary for a sibling that died
       without reporting, or ``unknown`` when the wait cap expires.

    Posting never raises: any error is logged at WARNING and swallowed, and the
    webhook URL is never logged.

    See Also
    --------
    [AzureMLPipelineGenerator][kedro_azureml_pipeline.generator.AzureMLPipelineGenerator] : Stamps the definition and roles.
    [NotificationConfig][kedro_azureml_pipeline.config.NotificationConfig] : The definition shape.
    [MlflowAzureMLHook][kedro_azureml_pipeline.hooks.MlflowAzureMLHook] : Writes the tags the sibling wait reads.
    """

    def __init__(self) -> None:
        self._started_at: float | None = None
        self._clock: Callable[[], float] = time.monotonic
        self._sleep: Callable[[float], None] = time.sleep

    @staticmethod
    def _definition() -> dict[str, Any] | None:
        """Read the stamped definition, or ``None`` when the hook is inert.

        Returns
        -------
        dict of str to Any or None
            The definition the generator stamped, or ``None``.
        """
        raw = os.environ.get(KEDRO_AZUREML_NOTIFY)
        if not raw:
            return None
        return json.loads(raw)

    @hook_impl
    def before_pipeline_run(self) -> None:
        """Record the start time and, on the announcer step, post ``start``."""
        definition = self._definition()
        if definition is None:
            return
        self._started_at = self._clock()
        if os.environ.get(KEDRO_AZUREML_NOTIFY_START) == "1" and "start" in definition["events"]:
            self._post(definition, self._event(definition, "start"))

    @hook_impl
    def on_pipeline_error(self, error: Exception) -> None:
        """Post ``failure`` from the step that raised.

        Parameters
        ----------
        error : Exception
            The exception that ended the step.
        """
        definition = self._definition()
        if definition is None or "failure" not in definition["events"]:
            return
        event = self._event(
            definition,
            "failure",
            node=os.environ.get(KEDRO_AZUREML_MLFLOW_NODE_NAME) or None,
            error=str(error)[:ERROR_HEAD_CHARS],
        )
        self._post(definition, event)

    @hook_impl
    def after_pipeline_run(self) -> None:
        """On the poster step, wait for the sibling leaves and post the outcome."""
        definition = self._definition()
        if definition is None or os.environ.get(KEDRO_AZUREML_NOTIFY_OUTCOME) != "1":
            return
        if "success" not in definition["events"] and "failure" not in definition["events"]:
            return
        siblings = tuple(name for name in os.environ.get(KEDRO_AZUREML_NOTIFY_SIBLINGS, "").split(",") if name)
        outcome = self._await_siblings(siblings, int(definition["wait_timeout"]))
        if outcome.finished:
            if "success" in definition["events"]:
                self._post(definition, self._event(definition, "success"))
            return
        if outcome.pending:
            self._post(
                definition,
                self._event(definition, "unknown", error=", ".join(outcome.pending)),
            )
            return
        if outcome.unreported and "failure" in definition["events"]:
            self._post(
                definition,
                self._event(
                    definition,
                    "failure",
                    node=", ".join(outcome.unreported),
                    error="step ended without reporting (killed or cancelled)",
                ),
            )

    def _await_siblings(self, siblings: tuple[str, ...], wait_timeout: int) -> SiblingOutcome:
        """Poll MLflow until every sibling leaf run is terminal or the cap expires.

        Sibling runs are the step runs under the same Azure ML root run tagged
        with the sibling node names by the MLflow hook. A sibling with no run
        yet has not started and counts as pending.

        Parameters
        ----------
        siblings : tuple of str
            Node names of the other leaf steps.
        wait_timeout : int
            Seconds to wait before giving up.

        Returns
        -------
        SiblingOutcome
            What became of the siblings.
        """
        if not siblings:
            return SiblingOutcome(finished=True)
        try:
            import mlflow
        except ImportError:
            logger.warning(
                "kedro-azureml-pipeline: mlflow is not installed; posting the outcome without checking siblings"
            )
            return SiblingOutcome(finished=True)
        client = mlflow.MlflowClient()
        experiment_name = os.environ.get("MLFLOW_EXPERIMENT_NAME") or os.environ.get(
            KEDRO_AZUREML_MLFLOW_EXPERIMENT_NAME
        )
        experiment = client.get_experiment_by_name(experiment_name) if experiment_name else None
        root_run_id = os.environ.get("AZUREML_ROOT_RUN_ID", "")
        pending = set(siblings)
        reported: list[str] = []
        unreported: list[str] = []
        deadline = self._clock() + wait_timeout
        while True:
            if experiment is not None:
                runs = client.search_runs(
                    [experiment.experiment_id], filter_string=f"tags.{ROOT_RUN_TAG} = '{root_run_id}'"
                )
                for run in runs:
                    name = run.data.tags.get(NODE_NAME_TAG)
                    if name not in pending or run.info.status not in TERMINAL_RUN_STATUSES:
                        continue
                    pending.discard(name)
                    if run.info.status != "FINISHED":
                        (reported if ERROR_TAG in run.data.tags else unreported).append(name)
            if not pending:
                break
            if self._clock() >= deadline:
                logger.warning("kedro-azureml-pipeline: gave up waiting for sibling steps %s", sorted(pending))
                return SiblingOutcome(
                    finished=False,
                    pending=tuple(sorted(pending)),
                    reported=tuple(reported),
                    unreported=tuple(unreported),
                )
            self._sleep(POLL_INTERVAL_SECONDS)
        return SiblingOutcome(
            finished=not reported and not unreported, reported=tuple(reported), unreported=tuple(unreported)
        )

    def _event(self, definition: dict[str, Any], kind: str, **extra: Any) -> NotificationEvent:
        """Assemble a ``NotificationEvent`` from the definition and the step environment.

        Parameters
        ----------
        definition : dict of str to Any
            The stamped definition.
        kind : str
            The event name.
        **extra : Any
            ``node`` and ``error`` for failure and unknown events.

        Returns
        -------
        NotificationEvent
            The event to render and post.
        """
        identifiers = {name: os.environ[name] for name in _IDENTIFIER_VARS if name in os.environ}
        elapsed = None if kind == "start" or self._started_at is None else self._clock() - self._started_at
        return NotificationEvent(
            event=kind,
            job_name=definition["job_name"],
            display_name=definition["display_name"],
            pipeline_name=definition["pipeline_name"],
            kedro_env=os.environ.get("KEDRO_ENV", ""),
            root_run_id=identifiers.get("AZUREML_ROOT_RUN_ID"),
            studio_url=studio_url(identifiers),
            elapsed_seconds=elapsed,
            environment=identifiers,
            **extra,
        )

    def _post(self, definition: dict[str, Any], event: NotificationEvent) -> None:
        """Render *event* and send it to the webhook, swallowing every error.

        Parameters
        ----------
        definition : dict of str to Any
            The stamped definition naming the webhook variable and the builder.
        event : NotificationEvent
            The event to send.
        """
        url = os.environ.get(definition["webhook_env"])
        if not url:
            logger.warning(
                "kedro-azureml-pipeline: %s is unset; not posting the '%s' notification",
                definition["webhook_env"],
                event.event,
            )
            return
        if not url.startswith(("https://", "http://")):
            logger.warning(
                "kedro-azureml-pipeline: %s is not an http(s) URL; not posting the '%s' notification",
                definition["webhook_env"],
                event.event,
            )
            return
        payload = self._render(definition.get("payload"), event)
        request = urllib.request.Request(  # noqa: S310
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=POST_TIMEOUT_SECONDS):  # noqa: S310
                pass
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kedro-azureml-pipeline: posting the '%s' notification failed: %s", event.event, type(exc).__name__
            )
            return
        logger.info("kedro-azureml-pipeline: posted the '%s' notification for job %s", event.event, event.job_name)

    @staticmethod
    def _render(builder_ref: str | None, event: NotificationEvent) -> Mapping[str, Any]:
        """Run the payload builder, falling back to the default payload on any error.

        Parameters
        ----------
        builder_ref : str or None
            ``module.path:function_name`` reference, or ``None`` for the default.
        event : NotificationEvent
            The event to render.

        Returns
        -------
        Mapping of str to Any
            The request body.
        """
        if builder_ref is None:
            return default_payload(event)
        from kedro_azureml_pipeline.cli.functions import import_callable

        try:
            payload = import_callable(builder_ref)(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kedro-azureml-pipeline: payload builder %s failed (%s); posting the default payload", builder_ref, exc
            )
            return default_payload(event)
        if not isinstance(payload, Mapping):
            logger.warning(
                "kedro-azureml-pipeline: payload builder %s returned %s, not a mapping; posting the default payload",
                builder_ref,
                type(payload).__name__,
            )
            return default_payload(event)
        return payload


notify_hook = NotificationHook()
