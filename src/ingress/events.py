import hashlib
import json
import re


def _string(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Expected a nonempty string")
    return value


def _timestamp(value):
    if not re.fullmatch(r"[0-9]+\.[0-9]+", _string(value)):
        raise ValueError("Invalid Slack timestamp")
    return value


def normalize(payload, team_id, api_app_id, received_at):
    """Return schema v1 with file IDs and Unix receipt time, or ignore an event."""
    if not isinstance(payload, dict):
        raise ValueError("Expected an object")
    event_type = _string(payload.get("type"))
    if event_type != "event_callback":
        return None
    team = _string(payload.get("team_id"))
    app = _string(payload.get("api_app_id"))
    if team != team_id or app != api_app_id:
        return None
    event = payload.get("event")
    if not isinstance(event, dict):
        raise ValueError("Expected an event")
    if (
        _string(event.get("type")) != "app_mention"
        or event.get("subtype")
        or "bot_id" in event
        or "bot_profile" in event
    ):
        return None
    message_ts = _timestamp(event.get("ts"))
    text = event.get("text")
    files = event.get("files", [])
    if not isinstance(text, str) or not isinstance(files, list):
        raise ValueError("Invalid text or files")
    file_ids = []
    for file in files:
        if not isinstance(file, dict):
            raise ValueError("Invalid file")
        file_ids.append(_string(file.get("id")))
    return {
        "schema_version": 1,
        "event_id": _string(payload.get("event_id")),
        "team_id": team,
        "api_app_id": app,
        "channel_id": _string(event.get("channel")),
        "user_id": _string(event.get("user")),
        "thread_ts": _timestamp(event.get("thread_ts", message_ts)),
        "message_ts": message_ts,
        "text": text,
        "file_ids": file_ids,
        "received_at": received_at,
    }


def fifo_id(*parts):
    """Hash a compact UTF-8 JSON array to preserve component boundaries."""
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
