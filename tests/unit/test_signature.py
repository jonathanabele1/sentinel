"""Unit tests for GitHub webhook signature verification."""

from __future__ import annotations

from packages.core.github.signature import compute_signature, verify_signature


def test_compute_signature_known_value() -> None:
    # Reference value: HMAC-SHA256 of "hello" with key "secret".
    expected = "sha256=88aab3ede8d3adf94d26ab90d3bafd4a2083070c3bcce9c014ee04a443847c0b"
    assert compute_signature(b"hello", "secret") == expected


def test_verify_signature_accepts_valid() -> None:
    body = b'{"hello":"world"}'
    sig = compute_signature(body, "shh")
    assert verify_signature(body, sig, "shh") is True


def test_verify_signature_rejects_tampered_body() -> None:
    sig = compute_signature(b"original", "shh")
    assert verify_signature(b"tampered", sig, "shh") is False


def test_verify_signature_rejects_wrong_secret() -> None:
    body = b"payload"
    sig = compute_signature(body, "right")
    assert verify_signature(body, sig, "wrong") is False


def test_verify_signature_rejects_missing_header() -> None:
    assert verify_signature(b"payload", None, "shh") is False


def test_verify_signature_rejects_empty_secret() -> None:
    body = b"payload"
    sig = compute_signature(body, "shh")
    assert verify_signature(body, sig, "") is False
