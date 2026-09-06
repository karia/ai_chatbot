"""Bounded HTTPS reads. Returned text is untrusted data, never instructions."""

import ipaddress
import json
import logging
import os
import socket
import time
from email.message import Message
from urllib.parse import urlsplit

import pycurl
from bs4 import BeautifulSoup

MAX_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 10
MAX_REDIRECTS = 3
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/csv", "application/json"})
HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})


class FetchError(ValueError):
    """A stable, credential-free failure reason."""


def _validate_url(target):
    try:
        if not isinstance(target, str) or any(
            ord(c) <= 32 or ord(c) == 127 for c in target
        ):
            raise ValueError
        parts = urlsplit(target)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or "\\" in target
            or "%" in parts.hostname
            or parts.port == 0
        ):
            raise ValueError
        return parts
    except ValueError:
        raise FetchError("invalid_url") from None


def _public_address(address):
    ip = ipaddress.ip_address(address)
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        # Reject translation/tunneling ranges, including globally scoped NAT64.
        return (
            not ip.is_site_local
            and ip.ipv4_mapped is None
            and ip.sixtofour is None
            and ip.teredo is None
            and ip not in ipaddress.ip_network("64:ff9b::/96")
        )
    return True


def _log(level, **fields):
    levels = logging.getLevelNamesMapping()
    threshold = levels.get(os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    if levels[level] >= threshold:
        print(json.dumps({"level": level, "tool": "reader", **fields}))


def _fetch(target, *, slack_token=None, allowed_types=TEXT_TYPES | HTML_TYPES):
    started = time.monotonic()
    deadline = started + TIMEOUT_SECONDS
    size = 0
    destination = None
    reason = None
    try:
        # Synchronous DNS builds cannot enforce a transfer deadline with NOSIGNAL.
        if not pycurl.version_info()[4] & pycurl.VERSION_ASYNCHDNS:
            raise FetchError("async_dns_required")
        for hop in range(MAX_REDIRECTS + 1):
            parts = _validate_url(target)
            destination = f"https://{parts.hostname}:{parts.port or 443}"
            if slack_token is not None and hop == 0:
                if parts.hostname != "files.slack.com" or (parts.port or 443) != 443:
                    raise FetchError("credentials_destination")
                if not slack_token or any(
                    ord(c) <= 32 or ord(c) >= 127 for c in slack_token
                ):
                    raise FetchError("invalid_credentials")
            body = bytearray()
            callback_reason = None

            def open_socket(purpose, address):
                nonlocal callback_reason
                try:
                    if (
                        callback_reason is not None
                        or purpose != pycurl.SOCKTYPE_IPCXN
                        or address.family not in (socket.AF_INET, socket.AF_INET6)
                        or not _public_address(address.addr[0])
                    ):
                        callback_reason = "address_blocked"
                        return pycurl.SOCKET_BAD
                    # libcurl connects this socket to this exact numeric sockaddr.
                    # No hostname lookup or proxy connection follows this check.
                    return socket.socket(
                        address.family, address.socktype, address.protocol
                    )
                except Exception:  # noqa: BLE001 - callbacks must return libcurl failure codes
                    callback_reason = "connection_failed"
                    return pycurl.SOCKET_BAD

            def write(chunk, body=body):
                nonlocal size, callback_reason
                size += len(chunk)
                if size > MAX_BYTES:
                    callback_reason = "size_limit"
                    return 0
                if time.monotonic() >= deadline:
                    callback_reason = "timeout"
                    return 0
                body.extend(chunk)
                return len(chunk)

            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                raise FetchError("timeout")
            curl = pycurl.Curl()
            try:
                curl.setopt(pycurl.URL, target.encode("utf-8"))
                curl.setopt(pycurl.PROTOCOLS, pycurl.PROTO_HTTPS)
                curl.setopt(pycurl.PROXY, "")
                curl.setopt(pycurl.FOLLOWLOCATION, 0)
                curl.setopt(pycurl.SSL_VERIFYPEER, 1)
                curl.setopt(pycurl.SSL_VERIFYHOST, 2)
                curl.setopt(pycurl.NOSIGNAL, 1)
                curl.setopt(pycurl.TIMEOUT_MS, remaining_ms)
                curl.setopt(pycurl.OPENSOCKETFUNCTION, open_socket)
                curl.setopt(pycurl.WRITEFUNCTION, write)
                headers = ["Accept-Encoding: identity"]
                if slack_token is not None and hop == 0:
                    headers.append(f"Authorization: Bearer {slack_token}")
                curl.setopt(pycurl.HTTPHEADER, headers)
                curl.perform()
                if callback_reason:
                    raise FetchError(callback_reason)
                if time.monotonic() >= deadline:
                    raise FetchError("timeout")
                status = curl.getinfo(pycurl.RESPONSE_CODE)
                if status in (301, 302, 303, 307, 308):
                    if hop == MAX_REDIRECTS:
                        raise FetchError("redirect_limit")
                    target = curl.getinfo(pycurl.REDIRECT_URL)
                    if not target:
                        raise FetchError("invalid_redirect")
                    continue
                if not 200 <= status < 300:
                    raise FetchError("http_error")
                header = Message()
                header["Content-Type"] = curl.getinfo(pycurl.CONTENT_TYPE) or ""
                content_type = header["Content-Type"].split(";", 1)[0].strip().lower()
                if content_type not in allowed_types:
                    raise FetchError("unsupported_type")
                return bytes(body), content_type, target, header.get_content_charset()
            except pycurl.error as exc:
                raise FetchError(
                    callback_reason
                    or (
                        "timeout"
                        if exc.args[0] == pycurl.E_OPERATION_TIMEDOUT
                        else "transport_error"
                    )
                ) from None
            finally:
                curl.close()
    except FetchError as exc:
        reason = str(exc)
        raise
    finally:
        level = "WARNING" if reason else "INFO"
        if reason in {
            "transport_error",
            "http_error",
            "connection_failed",
            "async_dns_required",
        }:
            level = "ERROR"
        _log(
            level,
            destination=destination,
            bytes_read=size,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
            reason=reason,
        )


def _decode_text(body, charset):
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def fetch_url(target):
    """Return {url, title, text, trusted: False}; raise FetchError on refusal.

    One call permits three redirects, 1 MiB total body bytes and ten seconds
    including DNS, TLS and redirects. Consumers must keep text in tool results.
    """
    body, content_type, final_url, charset = _fetch(target)
    title = ""
    if content_type in HTML_TYPES:
        soup = BeautifulSoup(body, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        for node in soup(["script", "style"]):
            node.decompose()
        text = (soup.body or soup).get_text(" ", strip=True)
    else:
        text = _decode_text(body, charset)
    return {"url": final_url, "title": title, "text": text, "trusted": False}
