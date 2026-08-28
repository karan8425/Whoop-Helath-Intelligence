import base64
import hashlib
import hmac
import json
import os
import time
import traceback

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Request,
)

from daily_job import (
    run_daily_pipeline,
)

from whoop_webhook_store import (
    init_whoop_webhook_tables,
    store_webhook_event,
    mark_pipeline_started,
    mark_pipeline_completed,
    mark_pipeline_failed,
)


router = APIRouter()


WHOOP_CLIENT_SECRET = os.getenv(
    "WHOOP_CLIENT_SECRET",
    ""
)

MAX_TIMESTAMP_AGE_SECONDS = 300


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
            detail="Invalid WHOOP webhook timestamp.",
        ) from exc

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

    if age_seconds > MAX_TIMESTAMP_AGE_SECONDS:

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
        WHOOP_CLIENT_SECRET.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).digest()

    expected_signature = (
        base64.b64encode(
            digest
        ).decode("utf-8")
    )

    if not hmac.compare_digest(
        expected_signature,
        received_signature,
    ):

        raise HTTPException(
            status_code=401,
            detail="Invalid WHOOP webhook signature.",
        )


def _run_recovery_pipeline(
    trace_id: str,
) -> None:

    print(
        "[whoop-webhook] "
        f"starting daily pipeline "
        f"trace_id={trace_id}",
        flush=True,
    )

    try:
        mark_pipeline_started(
            trace_id
        )

        result = run_daily_pipeline()

        mark_pipeline_completed(
            trace_id
        )

        print(
            "[whoop-webhook] "
            f"daily pipeline completed "
            f"trace_id={trace_id} "
            f"status={result.get('status')}",
            flush=True,
        )

    except Exception as exc:

        error_text = (
            f"{type(exc).__name__}: {exc}\n"
            f"{traceback.format_exc()}"
        )

        mark_pipeline_failed(
            trace_id,
            error_text,
        )

        print(
            "[whoop-webhook] "
            f"daily pipeline failed "
            f"trace_id={trace_id} "
            f"error={type(exc).__name__}: {exc}",
            flush=True,
        )


@router.post(
    "/webhooks/whoop"
)
async def receive_whoop_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
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

    print(
        "[whoop-webhook] "
        f"type={event_type} "
        f"trace_id={trace_id} "
        f"resource_id={resource_id} "
        f"user_id={user_id}",
        flush=True,
    )

    init_whoop_webhook_tables()

    is_new_event = store_webhook_event(
        trace_id=trace_id,
        event_type=event_type,
        resource_id=resource_id,
        user_id=user_id,
        payload=payload,
    )

    if not is_new_event:

        print(
            "[whoop-webhook] "
            f"duplicate ignored "
            f"trace_id={trace_id}",
            flush=True,
        )

        return {
            "status": "duplicate_ignored",
            "event_type": event_type,
            "trace_id": trace_id,
        }

    if event_type != "recovery.updated":

        return {
            "status": "accepted",
            "event_type": event_type,
            "trace_id": trace_id,
            "pipeline_triggered": False,
        }

    background_tasks.add_task(
        _run_recovery_pipeline,
        trace_id,
    )

    return {
        "status": "accepted",
        "event_type": event_type,
        "trace_id": trace_id,
        "pipeline_triggered": True,
    }