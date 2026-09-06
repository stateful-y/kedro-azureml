"""Tests for NotificationHook: once-per-job webhook posting from Azure ML steps."""

import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kedro_azureml_pipeline.constants import (
    KEDRO_AZUREML_MLFLOW_EXPERIMENT_NAME,
    KEDRO_AZUREML_MLFLOW_NODE_NAME,
    KEDRO_AZUREML_NOTIFY,
    KEDRO_AZUREML_NOTIFY_OUTCOME,
    KEDRO_AZUREML_NOTIFY_SIBLINGS,
    KEDRO_AZUREML_NOTIFY_START,
)
from kedro_azureml_pipeline.hooks.notify import (
    SLACK_POST_MESSAGE_URL,
    THREAD_TAG,
    NotificationEvent,
    NotificationHook,
    SiblingOutcome,
    default_payload,
    studio_url,
)

WEBHOOK_VAR = "HOOK_URL"
WEBHOOK_URL = "https://hooks.example.test/services/secret-token"
TOKEN_VAR = "BOT_TOKEN"
TOKEN = "xoxb-secret-bot-token"
CHANNEL = "C0123456789"
ENV_VARS = [
    KEDRO_AZUREML_NOTIFY,
    KEDRO_AZUREML_NOTIFY_START,
    KEDRO_AZUREML_NOTIFY_OUTCOME,
    KEDRO_AZUREML_NOTIFY_SIBLINGS,
    KEDRO_AZUREML_MLFLOW_NODE_NAME,
    KEDRO_AZUREML_MLFLOW_EXPERIMENT_NAME,
    "MLFLOW_EXPERIMENT_NAME",
    "KEDRO_ENV",
    "AZUREML_RUN_ID",
    "AZUREML_ROOT_RUN_ID",
    "AZUREML_ARM_SUBSCRIPTION",
    "AZUREML_ARM_RESOURCEGROUP",
    "AZUREML_ARM_WORKSPACE_NAME",
    WEBHOOK_VAR,
    TOKEN_VAR,
]
IDENTIFIERS = {
    "AZUREML_RUN_ID": "step-1",
    "AZUREML_ROOT_RUN_ID": "root-1",
    "AZUREML_ARM_SUBSCRIPTION": "sub",
    "AZUREML_ARM_RESOURCEGROUP": "rg",
    "AZUREML_ARM_WORKSPACE_NAME": "ws",
}


@pytest.fixture(autouse=True)
def clean_env():
    """Remove every variable the hook reads before and after each test."""
    saved = {k: os.environ.pop(k, None) for k in ENV_VARS}
    yield
    for k in ENV_VARS:
        os.environ.pop(k, None)
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


def stamp(
    *,
    events=("start", "success", "failure"),
    payload=None,
    wait_timeout=60,
    start=False,
    poster=False,
    siblings="",
    api=False,
    webhook=True,
):
    """Put the generator's stamped definition and markers into the environment.

    ``api=True`` names the Slack API transport and puts the token in the
    environment; ``webhook=False`` leaves the webhook out of the definition.
    """
    os.environ[KEDRO_AZUREML_NOTIFY] = json.dumps({
        "webhook_env": WEBHOOK_VAR if webhook else None,
        "token_env": TOKEN_VAR if api else None,
        "channel": CHANNEL if api else None,
        "events": list(events),
        "payload": payload,
        "wait_timeout": wait_timeout,
        "job_name": "job-a",
        "display_name": "Job A",
        "pipeline_name": "training",
    })
    os.environ["KEDRO_ENV"] = "prod"
    os.environ[WEBHOOK_VAR] = WEBHOOK_URL
    if api:
        os.environ[TOKEN_VAR] = TOKEN
    os.environ.update(IDENTIFIERS)
    if start:
        os.environ[KEDRO_AZUREML_NOTIFY_START] = "1"
    if poster:
        os.environ[KEDRO_AZUREML_NOTIFY_OUTCOME] = "1"
        os.environ[KEDRO_AZUREML_NOTIFY_SIBLINGS] = siblings


@pytest.fixture
def hook():
    """A hook with a controllable clock and a no-op sleep."""
    h = NotificationHook()
    ticks = iter(range(0, 100_000, 10))
    h._clock = lambda: next(ticks)
    h._sleep = MagicMock()
    return h


class _Posts(list):
    """A list of posts with the attributes the ``urlopen`` fixture sets."""


@pytest.fixture
def urlopen():
    """Capture every post as ``(url, body, timeout)``; the response body is ``posts.reply``."""
    posts = _Posts()
    posts.reply = b"ok"
    posts.headers = []

    def fake(request, timeout):
        posts.append((request.full_url, json.loads(request.data.decode("utf-8")), timeout))
        posts.headers.append(dict(request.header_items()))
        response = MagicMock()
        response.__enter__.return_value.read.return_value = posts.reply
        return response

    with patch("kedro_azureml_pipeline.hooks.notify.urllib.request.urlopen", side_effect=fake):
        yield posts


@pytest.fixture
def slack(monkeypatch):
    """A fake ``mlflow`` module whose client stores tags per run, patched into the hook's imports."""
    tags: dict[str, dict[str, str]] = {}
    mock = MagicMock()
    client = mock.MlflowClient.return_value
    client.set_tag.side_effect = lambda run_id, key, value: tags.setdefault(run_id, {}).__setitem__(key, value)
    client.get_run.side_effect = lambda run_id: SimpleNamespace(data=SimpleNamespace(tags=dict(tags.get(run_id, {}))))
    monkeypatch.setitem(__import__("sys").modules, "mlflow", mock)
    mock.tags = tags
    return mock


def _run(name, status, tags=None):
    """A minimal MLflow run object as ``search_runs`` returns it."""
    return SimpleNamespace(
        data=SimpleNamespace(tags={"kedro.node_name": name, **(tags or {})}), info=SimpleNamespace(status=status)
    )


def fake_mlflow(search_results):
    """An ``mlflow`` module whose client returns *search_results* page by page, repeating the last."""
    mock = MagicMock()
    client = mock.MlflowClient.return_value
    client.get_experiment_by_name.return_value = SimpleNamespace(experiment_id="exp-1")
    calls = iter(search_results)
    last = search_results[-1]
    client.search_runs.side_effect = lambda *a, **k: next(calls, last)
    return mock


class TestInert:
    """Without a stamped definition the hook does nothing."""

    def test_no_definition_posts_nothing(self, hook, urlopen):
        os.environ[KEDRO_AZUREML_NOTIFY_START] = "1"
        os.environ[KEDRO_AZUREML_NOTIFY_OUTCOME] = "1"
        hook.before_pipeline_run()
        hook.on_pipeline_error(RuntimeError("boom"))
        hook.after_pipeline_run()
        assert urlopen == []


class TestStart:
    """``start`` is posted once, by the announcer only."""

    def test_announcer_posts_start(self, hook, urlopen):
        stamp(start=True)
        hook.before_pipeline_run()
        assert len(urlopen) == 1
        url, body, timeout = urlopen[0]
        assert url == WEBHOOK_URL
        assert timeout == 10
        assert body["text"].startswith("[start] Job A (pipeline training, env prod) started.")
        assert (
            "https://ml.azure.com/runs/root-1?wsid=/subscriptions/sub/resourcegroups/rg/workspaces/ws" in body["text"]
        )

    def test_non_announcer_posts_nothing(self, hook, urlopen):
        stamp(start=False)
        hook.before_pipeline_run()
        assert urlopen == []

    def test_start_disabled_posts_nothing(self, hook, urlopen):
        stamp(events=["failure"], start=True)
        hook.before_pipeline_run()
        assert urlopen == []


class TestFailure:
    """``failure`` is posted by the step that raised."""

    def test_failure_names_node_and_error_head(self, hook, urlopen):
        stamp()
        os.environ[KEDRO_AZUREML_MLFLOW_NODE_NAME] = "tune_model"
        hook.before_pipeline_run()
        hook.on_pipeline_error(RuntimeError("x" * 300))
        assert len(urlopen) == 1
        text = urlopen[0][1]["text"]
        assert text.startswith("[failure] Job A (pipeline training, env prod) failed in node tune_model after ")
        assert "x" * 250 in text
        assert "x" * 251 not in text

    def test_failure_disabled_posts_nothing(self, hook, urlopen):
        stamp(events=["start", "success"])
        hook.on_pipeline_error(RuntimeError("boom"))
        assert urlopen == []

    def test_failure_without_node_name(self, hook, urlopen):
        stamp(events=["failure"])
        hook.on_pipeline_error(RuntimeError("boom"))
        assert "failed in node None" in urlopen[0][1]["text"]


class TestOutcome:
    """The poster posts the outcome once, after its siblings."""

    def test_non_poster_posts_nothing(self, hook, urlopen):
        stamp(poster=False)
        hook.before_pipeline_run()
        hook.after_pipeline_run()
        assert urlopen == []

    def test_outcome_events_disabled_posts_nothing(self, hook, urlopen):
        stamp(events=["start"], poster=True)
        hook.after_pipeline_run()
        assert urlopen == []

    def test_no_siblings_posts_success_immediately(self, hook, urlopen):
        stamp(poster=True)
        hook.before_pipeline_run()
        with patch.dict("sys.modules", {"mlflow": MagicMock()}) as modules:
            hook.after_pipeline_run()
            modules["mlflow"].MlflowClient.assert_not_called()
        assert len(urlopen) == 1
        assert urlopen[0][1]["text"].startswith("[success] Job A (pipeline training, env prod) finished after 10s.")

    def test_success_disabled_but_failure_enabled_posts_nothing_on_success(self, hook, urlopen):
        stamp(events=["failure"], poster=True)
        hook.after_pipeline_run()
        assert urlopen == []

    def test_waits_for_two_siblings_then_posts_once(self, hook, urlopen):
        stamp(poster=True, siblings="leaf_a,leaf_b")
        os.environ["MLFLOW_EXPERIMENT_NAME"] = "exp"
        mlflow = fake_mlflow([
            [],
            [_run("leaf_a", "RUNNING")],
            [_run("leaf_a", "FINISHED"), _run("leaf_b", "RUNNING")],
            [_run("leaf_a", "FINISHED"), _run("leaf_b", "FINISHED")],
        ])
        with patch.dict("sys.modules", {"mlflow": mlflow}):
            hook.after_pipeline_run()
        assert hook._sleep.call_count == 3
        client = mlflow.MlflowClient.return_value
        client.get_experiment_by_name.assert_called_once_with("exp")
        client.search_runs.assert_called_with(["exp-1"], filter_string="tags.mlflow.rootRunId = 'root-1'")
        assert len(urlopen) == 1
        assert urlopen[0][1]["text"].startswith("[success]")

    def test_experiment_name_falls_back_to_generator_variable(self, hook, urlopen):
        stamp(poster=True, siblings="leaf_a")
        os.environ[KEDRO_AZUREML_MLFLOW_EXPERIMENT_NAME] = "exp-from-generator"
        mlflow = fake_mlflow([[_run("leaf_a", "FINISHED")]])
        with patch.dict("sys.modules", {"mlflow": mlflow}):
            hook.after_pipeline_run()
        mlflow.MlflowClient.return_value.get_experiment_by_name.assert_called_once_with("exp-from-generator")
        assert len(urlopen) == 1

    def test_failed_sibling_with_error_tag_posts_nothing(self, hook, urlopen):
        stamp(poster=True, siblings="leaf_a")
        os.environ["MLFLOW_EXPERIMENT_NAME"] = "exp"
        mlflow = fake_mlflow([[_run("leaf_a", "FAILED", {"kedro.error": "boom"})]])
        with patch.dict("sys.modules", {"mlflow": mlflow}):
            hook.after_pipeline_run()
        assert urlopen == []

    @pytest.mark.parametrize("status", ["FAILED", "KILLED"])
    def test_sibling_ended_without_error_tag_posts_failure_summary(self, hook, urlopen, status):
        stamp(poster=True, siblings="leaf_a,leaf_b")
        os.environ["MLFLOW_EXPERIMENT_NAME"] = "exp"
        mlflow = fake_mlflow([[_run("leaf_a", status), _run("leaf_b", "FINISHED")]])
        with patch.dict("sys.modules", {"mlflow": mlflow}):
            hook.after_pipeline_run()
        assert len(urlopen) == 1
        text = urlopen[0][1]["text"]
        assert text.startswith("[failure] Job A (pipeline training, env prod) failed in node leaf_a")
        assert "step ended without reporting (killed or cancelled)" in text

    def test_unreported_sibling_with_failure_disabled_posts_nothing(self, hook, urlopen):
        stamp(events=["success"], poster=True, siblings="leaf_a")
        os.environ["MLFLOW_EXPERIMENT_NAME"] = "exp"
        mlflow = fake_mlflow([[_run("leaf_a", "KILLED")]])
        with patch.dict("sys.modules", {"mlflow": mlflow}):
            hook.after_pipeline_run()
        assert urlopen == []

    def test_wait_cap_posts_outcome_unknown_naming_pending(self, hook, urlopen, caplog):
        stamp(poster=True, siblings="leaf_a,leaf_b", wait_timeout=25)
        os.environ["MLFLOW_EXPERIMENT_NAME"] = "exp"
        mlflow = fake_mlflow([[_run("leaf_b", "FINISHED")]])
        with patch.dict("sys.modules", {"mlflow": mlflow}), caplog.at_level(logging.WARNING):
            hook.after_pipeline_run()
        assert len(urlopen) == 1
        text = urlopen[0][1]["text"]
        assert text.startswith("[unknown] Job A (pipeline training, env prod): outcome unknown")
        assert "Still pending: leaf_a" in text
        assert "gave up waiting for sibling steps ['leaf_a']" in caplog.text

    def test_missing_experiment_counts_as_pending(self, hook, urlopen):
        stamp(poster=True, siblings="leaf_a", wait_timeout=15)
        mlflow = fake_mlflow([[_run("leaf_a", "FINISHED")]])
        with patch.dict("sys.modules", {"mlflow": mlflow}):
            hook.after_pipeline_run()
        mlflow.MlflowClient.return_value.search_runs.assert_not_called()
        assert urlopen[0][1]["text"].startswith("[unknown]")

    def test_mlflow_not_installed_posts_success_with_warning(self, hook, urlopen, caplog):
        stamp(poster=True, siblings="leaf_a")
        with patch.dict("sys.modules", {"mlflow": None}), caplog.at_level(logging.WARNING):
            hook.after_pipeline_run()
        assert "mlflow is not installed" in caplog.text
        assert urlopen[0][1]["text"].startswith("[success]")


class TestPosting:
    """The post never raises and never logs the URL."""

    def test_http_error_is_logged_without_url(self, hook, caplog):
        stamp(start=True)
        with (
            patch("kedro_azureml_pipeline.hooks.notify.urllib.request.urlopen", side_effect=TimeoutError("slow")),
            caplog.at_level(logging.WARNING),
        ):
            hook.before_pipeline_run()
        assert "posting the 'start' notification failed: TimeoutError" in caplog.text
        assert "secret-token" not in caplog.text

    def test_unset_webhook_variable_logs_and_skips(self, hook, urlopen, caplog):
        stamp(start=True)
        del os.environ[WEBHOOK_VAR]
        with caplog.at_level(logging.WARNING):
            hook.before_pipeline_run()
        assert urlopen == []
        assert f"{WEBHOOK_VAR} is unset" in caplog.text

    def test_non_http_url_is_refused(self, hook, urlopen, caplog):
        stamp(start=True)
        os.environ[WEBHOOK_VAR] = "file:///etc/passwd"
        with caplog.at_level(logging.WARNING):
            hook.before_pipeline_run()
        assert urlopen == []
        assert "is not an http(s) URL" in caplog.text

    def test_success_is_logged_at_info(self, hook, urlopen, caplog):
        stamp(start=True)
        with caplog.at_level(logging.INFO):
            hook.before_pipeline_run()
        assert "posted the 'start' notification for job job-a" in caplog.text
        assert "secret-token" not in caplog.text


class TestSlackApi:
    """The Slack API transport: bearer posts, a thread per run, webhook fallback."""

    def test_start_posts_to_chat_post_message_with_bearer_token(self, hook, urlopen, slack):
        stamp(start=True, api=True)
        urlopen.reply = b'{"ok": true, "ts": "1700000000.000100"}'
        hook.before_pipeline_run()
        (url, body, timeout) = urlopen[0]
        headers = urlopen.headers[0]
        assert url == SLACK_POST_MESSAGE_URL
        assert body["channel"] == CHANNEL
        assert body["text"].startswith("[start] Job A")
        assert "thread_ts" not in body
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        assert timeout == 10

    def test_start_records_the_thread_on_the_root_run(self, hook, urlopen, slack):
        stamp(start=True, api=True)
        urlopen.reply = b'{"ok": true, "ts": "1700000000.000100"}'
        hook.before_pipeline_run()
        assert slack.tags == {"root-1": {THREAD_TAG: "1700000000.000100"}}

    def test_outcome_replies_in_the_thread_and_broadcasts(self, hook, urlopen, slack):
        stamp(poster=True, api=True)
        slack.tags["root-1"] = {THREAD_TAG: "1700000000.000100"}
        urlopen.reply = b'{"ok": true, "ts": "1700000000.000200"}'
        hook.before_pipeline_run()
        hook.after_pipeline_run()
        (_, body, _) = urlopen[-1]
        assert body["thread_ts"] == "1700000000.000100"
        assert body["reply_broadcast"] is True
        assert body["text"].startswith("[success] Job A")
        assert slack.tags["root-1"] == {THREAD_TAG: "1700000000.000100"}, "outcomes never rewrite the thread"

    def test_failure_replies_in_the_thread(self, hook, urlopen, slack):
        stamp(api=True)
        slack.tags["root-1"] = {THREAD_TAG: "1700000000.000100"}
        urlopen.reply = b'{"ok": true}'
        hook.before_pipeline_run()
        hook.on_pipeline_error(RuntimeError("boom"))
        assert urlopen[-1][1]["thread_ts"] == "1700000000.000100"

    def test_outcome_without_a_thread_posts_plainly(self, hook, urlopen, slack):
        stamp(poster=True, api=True)
        urlopen.reply = b'{"ok": true}'
        hook.before_pipeline_run()
        hook.after_pipeline_run()
        assert "thread_ts" not in urlopen[-1][1]

    def test_unreadable_thread_posts_plainly_with_warning(self, hook, urlopen, slack, caplog):
        stamp(poster=True, api=True)
        slack.MlflowClient.return_value.get_run.side_effect = ConnectionError("down")
        urlopen.reply = b'{"ok": true}'
        with caplog.at_level(logging.WARNING):
            hook.before_pipeline_run()
            hook.after_pipeline_run()
        assert "thread_ts" not in urlopen[-1][1]
        assert "could not read the notification thread: ConnectionError" in caplog.text

    def test_thread_lookup_needs_a_root_run_id(self, hook, urlopen, slack):
        stamp(poster=True, api=True)
        del os.environ["AZUREML_ROOT_RUN_ID"]
        urlopen.reply = b'{"ok": true}'
        hook.before_pipeline_run()
        hook.after_pipeline_run()
        slack.MlflowClient.return_value.get_run.assert_not_called()

    def test_recording_the_thread_needs_a_root_run_id(self, hook, urlopen, slack):
        stamp(start=True, api=True)
        del os.environ["AZUREML_ROOT_RUN_ID"]
        urlopen.reply = b'{"ok": true, "ts": "1"}'
        hook.before_pipeline_run()
        assert slack.tags == {}

    def test_start_without_a_timestamp_records_nothing(self, hook, urlopen, slack):
        stamp(start=True, api=True)
        urlopen.reply = b'{"ok": true}'
        hook.before_pipeline_run()
        assert slack.tags == {}

    def test_unrecordable_thread_is_logged(self, hook, urlopen, slack, caplog):
        stamp(start=True, api=True)
        slack.MlflowClient.return_value.set_tag.side_effect = ConnectionError("down")
        urlopen.reply = b'{"ok": true, "ts": "1"}'
        with caplog.at_level(logging.WARNING):
            hook.before_pipeline_run()
        assert "could not record the notification thread: ConnectionError" in caplog.text

    def test_mlflow_not_installed_posts_without_a_thread(self, hook, urlopen, monkeypatch, caplog):
        stamp(start=True, api=True)
        monkeypatch.setitem(__import__("sys").modules, "mlflow", None)
        urlopen.reply = b'{"ok": true, "ts": "1"}'
        with caplog.at_level(logging.WARNING):
            hook.before_pipeline_run()
        assert len(urlopen) == 1
        assert "could not record the notification thread: ModuleNotFoundError" in caplog.text

    def test_refusal_is_logged_with_slack_error(self, hook, urlopen, slack, caplog):
        stamp(start=True, api=True)
        urlopen.reply = b'{"ok": false, "error": "not_in_channel"}'
        with caplog.at_level(logging.WARNING):
            hook.before_pipeline_run()
        assert "Slack refused the 'start' notification: not_in_channel" in caplog.text
        assert slack.tags == {}
        assert TOKEN not in caplog.text

    @pytest.mark.parametrize("reply", [b"not json", b"[1, 2]"])
    def test_non_object_reply_counts_as_refused(self, hook, urlopen, slack, caplog, reply):
        stamp(start=True, api=True)
        urlopen.reply = reply
        with caplog.at_level(logging.WARNING):
            hook.before_pipeline_run()
        assert "Slack refused the 'start' notification: None" in caplog.text

    def test_transport_error_is_logged_without_token(self, hook, slack, caplog):
        stamp(start=True, api=True)
        with (
            patch("kedro_azureml_pipeline.hooks.notify.urllib.request.urlopen", side_effect=TimeoutError("slow")),
            caplog.at_level(logging.WARNING),
        ):
            hook.before_pipeline_run()
        assert "posting the 'start' notification failed: TimeoutError" in caplog.text
        assert TOKEN not in caplog.text

    def test_missing_token_falls_back_to_the_webhook(self, hook, urlopen, slack):
        stamp(start=True, api=True)
        del os.environ[TOKEN_VAR]
        hook.before_pipeline_run()
        assert urlopen[0][0] == WEBHOOK_URL
        assert "channel" not in urlopen[0][1]

    def test_missing_token_and_no_webhook_names_the_token_variable(self, hook, urlopen, slack, caplog):
        stamp(start=True, api=True, webhook=False)
        del os.environ[TOKEN_VAR]
        with caplog.at_level(logging.WARNING):
            hook.before_pipeline_run()
        assert urlopen == []
        assert f"{TOKEN_VAR} is unset" in caplog.text

    def test_custom_builder_payload_is_sent_with_the_channel(self, hook, urlopen, slack):
        stamp(start=True, api=True, payload=f"{__name__}:build_blocks")
        urlopen.reply = b'{"ok": true}'
        hook.before_pipeline_run()
        body = urlopen[0][1]
        assert body["channel"] == CHANNEL
        assert body["blocks"][0]["text"]["text"] == "start Job A"


def build_blocks(event):
    """Payload builder returning Slack blocks."""
    return {"blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": f"{event.event} {event.display_name}"}}]}


def build_broken(event):
    """Payload builder that raises."""
    raise ValueError(f"cannot render {event.event}")


def build_not_mapping(event):
    """Payload builder returning the wrong type."""
    return [event.event]


class TestPayloadBuilder:
    """Custom payload builders and the default fallback."""

    def test_custom_builder_mapping_is_posted_unchanged(self, hook, urlopen):
        stamp(start=True, payload="tests.test_notify_hook:build_blocks")
        hook.before_pipeline_run()
        assert urlopen[0][1] == build_blocks(NotificationEvent("start", "job-a", "Job A", "training", "prod"))

    def test_builder_receives_full_event(self, hook, urlopen):
        seen = []
        stamp(events=["failure"], payload="tests.test_notify_hook:build_blocks")
        os.environ[KEDRO_AZUREML_MLFLOW_NODE_NAME] = "fit_model"
        hook.before_pipeline_run()
        with patch("tests.test_notify_hook.build_blocks", side_effect=lambda e: seen.append(e) or {"text": "x"}):
            hook.on_pipeline_error(RuntimeError("boom"))
        event = seen[0]
        assert event.event == "failure"
        assert event.node == "fit_model"
        assert event.error == "boom"
        assert event.root_run_id == "root-1"
        assert event.environment == IDENTIFIERS
        assert event.elapsed_seconds == 10

    def test_raising_builder_falls_back_to_default(self, hook, urlopen, caplog):
        stamp(start=True, payload="tests.test_notify_hook:build_broken")
        with caplog.at_level(logging.WARNING):
            hook.before_pipeline_run()
        assert "payload builder tests.test_notify_hook:build_broken failed (cannot render start)" in caplog.text
        assert urlopen[0][1]["text"].startswith("[start]")

    def test_non_mapping_builder_falls_back_to_default(self, hook, urlopen, caplog):
        stamp(start=True, payload="tests.test_notify_hook:build_not_mapping")
        with caplog.at_level(logging.WARNING):
            hook.before_pipeline_run()
        assert "returned list, not a mapping" in caplog.text
        assert urlopen[0][1]["text"].startswith("[start]")


class TestHelpers:
    """Studio URL and default payload rendering."""

    def test_studio_url_requires_every_identifier(self):
        assert studio_url(IDENTIFIERS) == (
            "https://ml.azure.com/runs/root-1?wsid=/subscriptions/sub/resourcegroups/rg/workspaces/ws"
        )
        partial = dict(IDENTIFIERS)
        del partial["AZUREML_ARM_WORKSPACE_NAME"]
        assert studio_url(partial) is None

    def test_default_payload_without_link_or_elapsed(self):
        event = NotificationEvent("success", "job-a", "Job A", "training", "prod")
        assert default_payload(event) == {"text": "[success] Job A (pipeline training, env prod) finished."}

    def test_default_payload_uses_root_run_id_when_no_studio_url(self):
        event = NotificationEvent("start", "job-a", "Job A", "training", "prod", root_run_id="root-9")
        assert default_payload(event)["text"].endswith("started. Run: root-9")

    def test_sibling_outcome_defaults(self):
        assert SiblingOutcome(finished=True) == SiblingOutcome(True, (), (), ())
