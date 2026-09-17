"""Bounded structured logging without credentials."""

import json
import logging
import os
import re


FIELD_LIMIT = 1_000
SENSITIVE_FIELDS = {
    "authorization",
    "auth_header",
    "headers",
    "secret",
    "signature",
    "signing_secret",
    "token",
}
SENSITIVE_VALUES = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,}\"']+"),
    re.compile(r"v0=[0-9a-fA-F]{64}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]+"),
)


def _sensitive_field(key):
    normalized = key.lower().replace("-", "_")
    return normalized in SENSITIVE_FIELDS or normalized.endswith(
        ("_secret", "_signature", "_token")
    )


def _sanitize(value, secret_values):
    if isinstance(value, str):
        for secret in secret_values:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = SENSITIVE_VALUES[0].sub(r"\1[REDACTED]", value)
        for pattern in SENSITIVE_VALUES[1:]:
            value = pattern.sub("[REDACTED]", value)
        if len(value) > FIELD_LIMIT:
            value = value[: FIELD_LIMIT - len("[truncated]")] + "[truncated]"
        return value
    if isinstance(value, dict):
        return {
            key: _sanitize(item, secret_values)
            for key, item in value.items()
            if not _sensitive_field(key)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, secret_values) for item in value]
    return value


def emit(component, level, *, secret_values=(), **fields):
    """Write one sanitized JSON event when its level is enabled."""
    threshold = logging.getLevelNamesMapping().get(
        os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    if level < threshold:
        return
    record = _sanitize(
        {
            "level": logging.getLevelName(level),
            "component": component,
            **fields,
        },
        secret_values,
    )
    print(json.dumps(record), flush=True)
