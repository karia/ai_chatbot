"""Level handling, truncation and redaction of the single worker log helper."""

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from worker.observability import FIELD_LIMIT, emit


def record(capsys):
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(("configured", "level", "logged"), [
    (None, logging.INFO, True),
    (None, logging.DEBUG, False),
    ("WARNING", logging.INFO, False),
    ("warning", logging.WARNING, True),
    ("DEBUG", logging.DEBUG, True),
    ("nonsense", logging.DEBUG, False),
])
def test_level_threshold(capsys, monkeypatch, configured, level, logged):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    if configured is not None:
        monkeypatch.setenv("LOG_LEVEL", configured)
    emit("worker", level, operation="probe")
    output = capsys.readouterr().out
    assert bool(output) is logged
    if logged:
        assert json.loads(output) == {
            "level": logging.getLevelName(level),
            "component": "worker",
            "operation": "probe",
        }


def test_long_values_are_truncated_in_place(capsys):
    emit("worker", logging.INFO, text="a" * (FIELD_LIMIT * 2), nested={"text": "b" * (FIELD_LIMIT * 2)})
    logged = record(capsys)
    for value in (logged["text"], logged["nested"]["text"]):
        assert len(value) == FIELD_LIMIT
        assert value.endswith("[truncated]")
    assert len(json.dumps(logged["text"])) < FIELD_LIMIT * 2


def test_values_below_the_limit_are_kept(capsys):
    emit("worker", logging.INFO, text="a" * FIELD_LIMIT)
    assert record(capsys)["text"] == "a" * FIELD_LIMIT


@pytest.mark.parametrize("key", [
    "authorization", "Authorization", "auth_header", "headers", "secret",
    "signature", "signing_secret", "token", "slack_token", "x-slack-signature",
    "client_secret",
])
def test_sensitive_field_names_never_reach_the_log(capsys, key):
    emit("worker", logging.INFO, operation="probe", **{key: "leaked", "nested": {key: "leaked"}})
    logged = record(capsys)
    assert "leaked" not in json.dumps(logged)
    assert logged["operation"] == "probe"


@pytest.mark.parametrize("value", [
    "xoxb-1111-2222-abcdefg",
    f"v0={'a' * 64}",
    "Authorization: Bearer abcdefghijklmnop",
    "authorization=Basic dXNlcjpwYXNz",
])
def test_credential_shaped_values_are_redacted(capsys, value):
    emit("worker", logging.INFO, text=f"before {value} after", items=[value], nested={"text": value})
    output = capsys.readouterr().out
    assert "[REDACTED]" in output
    for fragment in value.split()[-1:]:
        assert fragment not in output
    logged = json.loads(output)
    assert logged["text"].startswith("before ") and logged["text"].endswith(" after")
    assert "[REDACTED]" in logged["items"][0]
    assert "[REDACTED]" in logged["nested"]["text"]


def test_caller_supplied_secrets_are_redacted(capsys):
    emit("worker", logging.INFO, secret_values=("hunter2", "", None), text="body hunter2 tail")
    assert record(capsys)["text"] == "body [REDACTED] tail"


def test_non_string_fields_survive_unchanged(capsys):
    emit("worker", logging.INFO, response_ms=1200, ok=True, missing=None)
    assert record(capsys) == {
        "level": "INFO",
        "component": "worker",
        "response_ms": 1200,
        "ok": True,
        "missing": None,
    }


def test_flat_lambda_layout_reaches_the_shared_helper(tmp_path):
    """The deployed worker has tools/ as a top-level package, not worker.tools."""
    source = Path(__file__).resolve().parents[3] / "src" / "worker"
    for module in source.glob("*.py"):
        shutil.copy(module, tmp_path / module.name)
    shutil.copytree(source / "tools", tmp_path / "tools")
    result = subprocess.run(
        [sys.executable, "-c", "import tools.attachments as a; print(a.emit.__module__)"],
        cwd=tmp_path, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "observability"
