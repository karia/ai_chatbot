"""Conditional state writes. Callers retain returned event snapshots for updates."""

import json
import logging
import os
import time
from decimal import Decimal
from uuid import uuid4

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError


LEASE_SECONDS = 180
EVENT_TTL_SECONDS = 30 * 86400


class Conflict(Exception):
    """State changed, a lease is unavailable, or the session is stopped; retry."""


class EventExpired(Exception):
    """The event is outside the retention window; do not execute it."""


def _now():
    return Decimal(str(time.time()))


def _log(level, operation, item=None):
    threshold = logging.getLevelNamesMapping().get(
        os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    if level < threshold:
        return
    record = {"level": logging.getLevelName(level), "operation": operation}
    if item and "status" in item:
        record.update(status=item["status"], attempt=int(item["attempt"]))
    print(json.dumps(record), flush=True)


class Store:
    def __init__(self):
        self.client = boto3.client("dynamodb")
        self.table_name = os.environ["DYNAMODB_TABLE_NAME"]

    def _get(self, pk):
        item = self.client.get_item(
            TableName=self.table_name, Key={"pk": {"S": pk}}, ConsistentRead=True
        ).get("Item")
        if item is None:
            return None
        return {key: TypeDeserializer().deserialize(value) for key, value in item.items()}

    def get_event(self, team, event):
        item = self._get(f"EVENT#{team}#{event}")
        return item if item and item["expires_at"] > _now() else None

    def get_session(self, thread_hash):
        return self._get(f"SESSION#{thread_hash}")

    def _put(self, item, previous, *, owned=False):
        """Check owner/attempt alongside revision matching as defense in depth."""
        item = {**item, "revision": uuid4().hex}
        put = {"TableName": self.table_name, "Item": item}
        if previous is None:
            put["ConditionExpression"] = "attribute_not_exists(pk)"
        else:
            put["ConditionExpression"] = "revision = :revision"
            put["ExpressionAttributeValues"] = {":revision": previous["revision"]}
            if "owner" in previous:
                put["ConditionExpression"] += " AND #owner = :owner AND attempt = :attempt"
                put["ExpressionAttributeNames"] = {"#owner": "owner"}
                put["ExpressionAttributeValues"].update(
                    {":owner": previous["owner"], ":attempt": previous["attempt"]}
                )
            if owned:
                put["ConditionExpression"] += (
                    " AND lease_until > :now AND expires_at > :now"
                )
                put["ExpressionAttributeValues"][":now"] = _now()
        serializer = TypeSerializer()
        for field in ("Item", "ExpressionAttributeValues"):
            if field in put:
                put[field] = {
                    key: serializer.serialize(value) for key, value in put[field].items()
                }
        return {"Put": put}, item

    def _write(self, *writes):
        try:
            self.client.transact_write_items(TransactItems=[write[0] for write in writes])
        except ClientError as error:
            if error.response["Error"]["Code"] == "TransactionCanceledException":
                reasons = error.response.get("CancellationReasons", [])
                if any(
                    reason.get("Code") in {"ConditionalCheckFailed", "TransactionConflict"}
                    for reason in reasons
                ):
                    _log(logging.WARNING, "state_conflict")
                    raise Conflict("Conditional state write failed") from error
            _log(logging.ERROR, "state_write_failed")
            raise
        item = writes[0][1]
        level = logging.ERROR if item.get("status") == "NEEDS_REVIEW" else logging.INFO
        _log(level, "state_written", item)
        return item

    def acquire(self, team, event, thread_hash, owner, *, received_at):
        """Use the original receipt time on every delivery; never renew event TTL.

        COMPLETED is returned unchanged. Other returned states have a new lease;
        the worker must inspect the saved phase before resuming external work.
        """
        now = _now()
        expires_at = int(received_at) + EVENT_TTL_SECONDS
        if expires_at <= now:
            raise EventExpired()
        pk = f"EVENT#{team}#{event}"
        previous = self._get(pk)
        if previous:
            if previous["expires_at"] <= now:
                raise EventExpired()
            if previous["session_pk"] != f"SESSION#{thread_hash}":
                raise Conflict("Event belongs to another session")
            if previous["status"] == "COMPLETED":
                return previous
            if previous["status"] == "NEEDS_REVIEW" or max(
                previous["lease_until"], previous.get("retry_at", 0)
            ) > now:
                raise Conflict("Event is unavailable")
        session = self.get_session(thread_hash)
        if session and (
            session.get("stop_reason")
            or session.get("active_event_id") not in (None, pk)
        ):
            raise Conflict("Session is unavailable")
        updated_session = {
            **(session or {"pk": f"SESSION#{thread_hash}", "answer_count": 0}),
            "active_event_id": pk,
            "updated_at": now,
        }
        item = {
            **(previous or {"pk": pk, "status": "RUNNING", "expires_at": expires_at}),
            "session_pk": updated_session["pk"],
            "owner": owner,
            "attempt": previous["attempt"] + 1 if previous else 1,
            "lease_until": now + LEASE_SECONDS,
        }
        return self._write(self._put(item, previous), self._put(updated_session, session))

    def _transition(self, event, status, allowed, **fields):
        if event["status"] not in allowed:
            raise Conflict("Invalid state transition")
        item = {**event, **fields, "status": status}
        event_write = self._put(item, event, owned=True)
        if status not in ("COMPLETED", "NEEDS_REVIEW"):
            return self._write(event_write)
        session = self._get(event["session_pk"])
        if (
            not session
            or session.get("active_event_id") != event["pk"]
            or session.get("stop_reason")
        ):
            raise Conflict("Session is unavailable")
        updated_session = {**session, "updated_at": _now()}
        if status == "COMPLETED":
            del updated_session["active_event_id"]
            updated_session["answer_count"] += 1
        else:
            updated_session["stop_reason"] = fields["failure"]
        return self._write(event_write, self._put(updated_session, session))

    def bind_memory(self, thread_hash, memory_session_id):
        """Set the Memory mapping once while preserving session controls."""
        session = self.get_session(thread_hash)
        if not session or session.get("memory_session_id", memory_session_id) != memory_session_id:
            raise Conflict("Memory mapping is unavailable")
        updated = {
            **session,
            "memory_session_id": memory_session_id,
            "updated_at": _now(),
        }
        self._write(self._put(updated, session))

    def started(self, event):
        """Persist before inference or Memory writes so an interrupted run is identifiable."""
        return self._transition(event, "RUNNING", {"RUNNING"}, phase="EXECUTING")

    def defer(self, event, retry_at):
        """Persist a known retryable failure; POSTING requires a definite rejection."""
        retry_at = Decimal(str(retry_at))
        status = "GENERATED" if event["status"] == "POSTING" else event["status"]
        return self._transition(
            event, status, {"RUNNING", "GENERATED", "POSTING"}, retry_at=retry_at
        )

    def generated(self, event, reply):
        """Persist only after Memory saving has completed."""
        reply = reply.encode("utf-8")[:65536].decode("utf-8", errors="ignore")
        return self._transition(event, "GENERATED", {"RUNNING"}, reply=reply)

    def posting(self, event):
        return self._transition(event, "POSTING", {"GENERATED"})

    def complete(self, event, slack_ts):
        return self._transition(event, "COMPLETED", {"POSTING"}, slack_ts=slack_ts)

    def needs_review(self, event, failure):
        if not failure:
            raise ValueError("A failure classification is required")
        return self._transition(
            event, "NEEDS_REVIEW", {"RUNNING", "GENERATED", "POSTING"}, failure=failure
        )

    def reserve_post(self, team, channel):
        """Reserve one second immediately; a conflict must be retried later."""
        now = _now()
        pk = f"RATE#{team}#{channel}"
        previous = self._get(pk)
        if previous and previous["next_post_at"] > now:
            raise Conflict("Posting slot is unavailable")
        item = {"pk": pk, "next_post_at": now + 1}
        self._write(self._put(item, previous))
        return item["next_post_at"]
