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


# ============================================================
# CONFIGURATION
# ============================================================

# WHOOP signs webhook requests using the application's
# existing WHOOP Client Secret.
#
# This value must remain in the Render environment.
# Never hard-code it here.
WHOOP_CLIENT_SECRET = os.getenv(
    "WHOOP_CLIENT_SECRET",
    ""
)

# Reject webhook requests whose signed timestamp is more than
# five minutes away from the current server time.
MAX_TIMESTAMP_AGE_SECONDS = 300


# ============================================================
# TIMESTAMP VALIDATION
# ============================================================

def _validate_timestamp(
    timestamp: str,
) -> None:

    try:
        timestamp_milliseconds = int(
            timestamp
        )

    except (
        TypeError,
        ValueError,
    ) as exc:

        raise HTTPException(
            status_code=401,
            detail=(
                "Invalid WHOOP webhook "
                "timestamp."
            ),
        ) from exc

    # WHOOP sends milliseconds since Unix epoch.
    timestamp_seconds = (
        timestamp_milliseconds
        / 1000.0
    )

    current_time_seconds = (
        time.time()
    )

    age_seconds = abs(
        current_time_seconds
        - timestamp_seconds
    )

    if (
        age_seconds
        > MAX_TIMESTAMP_AGE_SECONDS
    ):

        raise HTTPException(
            status_code=401,
            detail=(
                "Expired WHOOP webhook "
                "timestamp."
            ),
        )


# ============================================================
# SIGNATURE VALIDATION
# ============================================================

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

    # WHOOP signature input:
    #
    # timestamp header string + exact raw HTTP request body
    #
    # It is important that we use the raw bytes and do not
    # re-serialize the JSON before calculating the HMAC.
    signed_payload = (
        timestamp.encode(
            "utf-8"
        )
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
            detail=(
                "Invalid WHOOP webhook "
                "signature."
            ),
        )


# ============================================================
# WEBHOOK ENDPOINT
# ============================================================

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

    if (
        not timestamp
        or not signature
    ):

        raise HTTPException(
            status_code=401,
            detail=(
                "Missing WHOOP webhook "
                "signature headers."
            ),
        )

    # Read the exact raw request body before parsing JSON.
    # WHOOP uses these bytes when generating its signature.
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
            detail=(
                "Invalid webhook JSON."
            ),
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

    # Log only event metadata.
    #
    # Do not log authorization tokens, secrets,
    # signatures, or the full webhook payload.
    print(
        "[whoop-webhook] "
        f"type={event_type} "
        f"trace_id={trace_id} "
        f"resource_id={resource_id} "
        f"user_id={user_id}",
        flush=True,
    )

    # Phase 1 behavior:
    #
    # We intentionally acknowledge the authenticated event
    # without running the health-intelligence pipeline yet.
    #
    # Once real webhook delivery is verified, recovery.updated
    # will become the trigger for the existing daily pipeline.
    return {
        "status": "accepted",
        "event_type": event_type,
        "trace_id": trace_id,
    }