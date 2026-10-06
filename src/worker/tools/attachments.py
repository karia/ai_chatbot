"""Read the text files in one Slack event as untrusted tool results."""

import logging
import time

from .url import MAX_BYTES, TEXT_TYPES, FetchError, _decode_text, _fetch, emit

MAX_ATTACHMENTS = 3
MAX_IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}


def fetch_attachments(event, *, slack_token):
    """Return up to three untrusted text or image results.

    Call once with the complete event, not once per file. Oversized events and
    unsupported metadata are rejected before fetching. Text files have a 1 MiB
    budget and images have a 2 MiB budget. Each fetch has a ten-second budget.
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
            if file["mimetype"] not in TEXT_TYPES | IMAGE_TYPES.keys():
                raise FetchError("unsupported_type")
    except FetchError as exc:
        emit(
            "reader",
            logging.WARNING,
            destination=None,
            bytes_read=0,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
            reason=str(exc),
        )
        raise

    results = []
    for file in files:
        image = file["mimetype"] in IMAGE_TYPES
        allowed_types = IMAGE_TYPES.keys() if image else TEXT_TYPES
        body, content_type, target, charset = _fetch(
            file["url_private"],
            slack_token=slack_token,
            allowed_types=allowed_types,
            max_bytes=MAX_IMAGE_BYTES if image else MAX_BYTES,
        )
        result = {
            "id": file.get("id", ""),
            "name": file.get("name", ""),
            "url": target,
            "trusted": False,
        }
        if image:
            result["image"] = {
                "format": IMAGE_TYPES[content_type],
                "source": {"bytes": body},
            }
        else:
            result["text"] = _decode_text(body, charset)
        results.append(result)
    return results
