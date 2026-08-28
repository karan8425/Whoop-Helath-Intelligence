import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)


router = APIRouter()


# WHOOP signs webhook requests using the application's
# existing WHOOP Client Secret.
#
# We intentionally read it from the environment here.
# Never hard-code the secret in this file.
WHOOP_CLIENT_SECRET = os.getenv(
    "WHOOP_CLIENT_SECRET",
    ""
)

# Reject signed requests whose timestamp is too far from
# the current server time. This reduces replay risk.
MAX_TIMESTAMP_AGE_SECONDS = 300


def _validate_timestamp(
    timestamp: str,
) -> None:

    try:
        timestamp_value = int(
            timestamp
        )

    except (
        TypeError,
        ValueError,
    ) as exc:

        raise HTTPException(
            status_code=401,
            detail="Invalid WHOOP webhook timestamp.",
        ) from exc

    now = int(
        time.time()
    )

    if abs(
        now - timestamp_value
    ) > MAX_TIMESTAMP_AGE_SECONDS:

        raise HTTPException(
            status_code=401,
            detail="Expired WHOOP webhook timestamp.",
        )


def _validate_signature(
    timestamp: str,
    body: bytes,
    received_signature: str,
) -> None:

    if not WHOOP_CLIENT_SECRET:

        raise HTTPException(
            status_code=503,
            detail=(
                "WHOOP webhook validation "
                "is not configured."
            ),
        )

    signed_payload = (
        timestamp.encode("utf-8")
        + body
    )

    digest = hmac.new(
        WHOOP_CLIENT_SECRET.encode(
            "utf-8"
        ),
        signed_payload,
        hashlib.sha256,
    ).digest()

    expected_signature = (
        base64.b64encode(
            digest
        ).decode(
            "utf-8"
        )
    )

    if not hmac.compare_digest(
        expected_signature,
        received_signature,
    ):

        raise HTTPException(
            status_code=401,
            detail="Invalid WHOOP webhook signature.",
        )


@router.post(
    "/webhooks/whoop"
)
async def receive_whoop_webhook(
    request: Request,
):

    timestamp = request.headers.get(
        "X-WHOOP-Signature-Timestamp"
    )

    signature = request.headers.get(
        "X-WHOOP-Signature"
    )

    if not timestamp or not signature:

        raise HTTPException(
            status_code=401,
            detail=(
                "Missing WHOOP webhook "
                "signature headers."
            ),
        )

    body = await request.body()

    _validate_timestamp(
        timestamp
    )

    _validate_signature(
        timestamp,
        body,
        signature,
    )

    try:
        payload = json.loads(
            body
        )

    except json.JSONDecodeError as exc:

        raise HTTPException(
            status_code=400,
            detail="Invalid webhook JSON.",
        ) from exc

    event_type = payload.get(
        "type"
    )

    trace_id = payload.get(
        "trace_id"
    )

    resource_id = payload.get(
        "id"
    )

    user_id = payload.get(
        "user_id"
    )

    # Deliberately log only safe event metadata.
    # Do not log the raw webhook payload.
    print(
        "[whoop-webhook] "
        f"type={event_type} "
        f"trace_id={trace_id} "
        f"resource_id={resource_id} "
        f"user_id={user_id}",
        flush=True,
    )

    return {
        "status": "accepted",
        "event_type": event_type,
        "trace_id": trace_id,
    }