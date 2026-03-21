from __future__ import annotations

import logging

from app.core.logging import RedactionFilter


def test_redaction_filter_masks_sensitive_fields() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Authorization: Bearer secret-token-value",
        args=(),
        exc_info=None,
    )
    record.api_key = "abc123"
    record.secret = "super-secret"
    record.safe_value = "ok"

    assert RedactionFilter().filter(record) is True
    assert record.api_key == "[REDACTED]"
    assert record.secret == "[REDACTED]"
    assert record.safe_value == "ok"
    assert "[REDACTED]" in str(record.msg)


def test_redaction_filter_keeps_small_safe_payloads() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg={"event": "ok", "value": "hello"},
        args=(),
        exc_info=None,
    )
    assert RedactionFilter().filter(record) is True
    assert record.msg["event"] == "ok"
    assert record.msg["value"] == "hello"
