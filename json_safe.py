"""Centralized JSON-safe coercion for values persisted as JSONB.

psycopg's JSONB adapter serializes with the stdlib ``json`` encoder, which
rejects ``date`` / ``datetime`` / ``UUID`` / ``Decimal``. Audit and result
payloads in the daily pipeline are assembled directly from database rows and
routinely contain those types.

Coercion is deliberately performed *by type* rather than with a blanket
``str()`` fallback: an unexpected object type still reaches ``json.dumps`` and
raises ``TypeError``, so genuine serialization mistakes stay visible instead of
being silently stringified.

Contract preserved:
    - ``date``      -> ISO-8601 calendar date   ("2026-09-06")
    - ``datetime``  -> ISO-8601 timestamp       ("2026-09-06T08:41:52.933+00:00")
    - ``UUID``      -> canonical string
    - ``Decimal``   -> JSON number (int when integral, else float);
                       non-finite Decimals -> None
    - everything else is returned unchanged.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID


def json_safe(value):
    """Return a structurally identical value with JSONB-unsafe leaves coerced."""

    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]

    # datetime is a subclass of date - check it first.
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        if value == value.to_integral_value():
            return int(value)
        return float(value)

    return value
