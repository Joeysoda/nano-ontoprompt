"""Opaque, signed keyset cursor tokens."""
import base64
import hashlib
import hmac
import json
import time
from app.config import settings
from .errors import ObjectQueryError


def _secret():
    return (settings.secret_key or "object-query-dev-secret").encode("utf-8")


def encode(payload, ttl=3600):
    body = dict(payload)
    body["expires_at"] = int(time.time()) + ttl
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    signature = hmac.new(_secret(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def decode(token):
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(_secret(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw)
        if int(payload.get("expires_at", 0)) < int(time.time()):
            raise ObjectQueryError("cursor_expired", "read.page_token", "Cursor has expired")
        return payload
    except ObjectQueryError:
        raise
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise ObjectQueryError("cursor_invalid", "read.page_token", "Cursor is invalid") from exc
