"""Keep redaction rules and log-driven metric filters aligned across packages."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MONITORING = (ROOT / "terraform" / "monitoring.tf").read_text()


def _filter_patterns():
    found = re.findall(
        r'resource "aws_cloudwatch_log_metric_filter" "(\w+)" \{.*?pattern\s+= "((?:[^"\\]|\\.)*)"',
        MONITORING,
        re.DOTALL,
    )
    return {name: pattern.replace('\\"', '"') for name, pattern in found}


def test_redaction_rules_match_between_ingress_and_worker():
    from ingress import app
    from worker import observability

    assert app.SENSITIVE_FIELDS == observability.SENSITIVE_FIELDS
    assert app.LOG_FIELD_LIMIT == observability.FIELD_LIMIT
    secret = "xoxb-1-abc"
    signature = f"v0={'a' * 64}"
    for value in (secret, signature, f"Authorization: Bearer {secret}", "plain"):
        assert app._safe_log_value(value) == observability._sanitize(value, ())


@pytest.mark.parametrize(("name", "record"), [
    ("needs_review", {"status": "NEEDS_REVIEW"}),
    ("session_stopped", {"state": "session_stopped"}),
    ("lambda_timeout", {"state": "budget_exhausted"}),
    ("answer_completed", {"state": "answer_completed", "response_ms": 1200}),
    ("service_throttled", {"operation": "service_throttled", "service": "bedrock"}),
])
def test_metric_filters_match_the_emitted_fields(name, record):
    pattern = _filter_patterns()[name]
    for field, value in record.items():
        if f"$.{field} =" in pattern:
            assert f'"{value}"' in pattern or str(value) in pattern
            break
    else:
        pytest.fail(f"{name} matches no field of {json.dumps(record)}")


def test_response_time_filter_reads_the_logged_duration():
    assert 'value     = "$.response_ms"' in MONITORING
