"""Read the text files in one Slack event as untrusted tool results."""

import time

from worker.tools.url import TEXT_TYPES, FetchError, _fetch, _log

MAX_ATTACHMENTS = 3


def fetch_attachments(event, *, slack_token):
    """Return up to three {id, name, url, text, trusted: False} results.

    Call once with the complete event, not once per file. Oversized events and
    unsupported metadata are rejected before fetching. Each file has its own
    1 MiB / ten-second budget. Plain text, Markdown, CSV and JSON are allowed.
    """
    started = time.monotonic()
    try:
        files = event.get("files", []) if isinstance(event, dict) else None
        if not isinstance(files, list):
            raise FetchError("invalid_attachments")
        if len(files) > MAX_ATTACHMENTS:
            raise FetchError("attachment_limit")
        if (
            not isinstance(slack_token, str)
            or not slack_token
            or any(ord(c) <= 32 or ord(c) >= 127 for c in slack_token)
        ):
            raise FetchError("invalid_credentials")
        for file in files:
            if (
                not isinstance(file, dict)
                or not isinstance(file.get("url_private"), str)
                or not isinstance(file.get("mimetype"), str)
            ):
                raise FetchError("invalid_attachments")
            if file["mimetype"] not in TEXT_TYPES:
                raise FetchError("unsupported_type")
    except FetchError as exc:
        _log(
            "WARNING",
            destination=None,
            bytes_read=0,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
            reason=str(exc),
        )
        raise

    results = []
    for file in files:
        body, _, target = _fetch(
            file["url_private"], slack_token=slack_token, allowed_types=TEXT_TYPES
        )
        results.append(
            {
                "id": file.get("id", ""),
                "name": file.get("name", ""),
                "url": target,
                "text": body.decode("utf-8", errors="replace"),
                "trusted": False,
            }
        )
    return results
