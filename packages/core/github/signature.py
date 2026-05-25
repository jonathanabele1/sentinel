"""GitHub webhook signature validation.

GitHub signs every webhook delivery with HMAC-SHA256 over the raw request body
using the secret configured on the App. Without validation, anyone can POST
fake events to the receiver. This module is the single source of truth for the
check.
"""

from __future__ import annotations

import hmac
from hashlib import sha256

SIGNATURE_HEADER = "x-hub-signature-256"


def compute_signature(body: bytes, secret: str) -> str:
    """Return the `sha256=<hex>` string GitHub would send for this body."""
    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=sha256)
    return f"sha256={mac.hexdigest()}"


def verify_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    """Constant-time comparison against the signature sent by GitHub."""
    if not signature_header or not secret:
        return False
    expected = compute_signature(body, secret)
    return hmac.compare_digest(expected, signature_header)
