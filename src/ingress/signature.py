import hashlib
import hmac


def verify(body, headers, secret, now):
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")
    if not isinstance(timestamp, str) or not timestamp.isascii() or not timestamp.isdecimal():
        return False
    if len(timestamp) > 12 or abs(now - int(timestamp)) > 300:
        return False
    if not isinstance(signature, str) or not signature.isascii():
        return False
    expected = "v0=" + hmac.new(
        secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
