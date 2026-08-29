import asyncio
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
    mark_pipeline_skipped,
    mark_pipeline_failed,
    pipeline_lock,
)


router = APIRouter()


# ============================================================
# CONFIGURATION
# ============================================================

WHOOP_CLIENT_SECRET = os.getenv(
    "WHOOP_CLIENT_SECRET",
    ""
)

MAX_TIMESTAMP_AGE_SECONDS = 300

SLEEP_EVENT_DELAY_SECONDS = 45

SLEEP_EVENT_MAX_ATTEMPTS = 4

SLEEP_EVENT_RETRY_SECONDS = 45


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
                "Invalid WHOOP webhook timestamp."
            ),
        ) from exc

    timestamp_seconds = (
        timestamp_milliseconds
        / 1000.0
    )

    age_seconds = abs(
        time.time()
        - timestamp_seconds
    )

    if (
        age_seconds
        > MAX_TIMESTAMP_AGE_SECONDS
    ):

        raise HTTPException(
            status_code=401,
            detail=(
                "Expired WHOOP webhook timestamp."
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
                "Invalid WHOOP webhook signature."
            ),
        )


# ============================================================
# PIPELINE EXECUTION
# ============================================================

def _execute_pipeline_once(
    event_id: int,
    trace_id: str,
    event_type: str,
):

    with pipeline_lock() as acquired:

        if not acquired:

            print(
                "[whoop-webhook] "
                "pipeline already running; "
                "skipping overlapping event "
                f"event_id={event_id} "
                f"type={event_type} "
                f"trace_id={trace_id}",
                flush=True,
            )

            mark_pipeline_skipped(
                event_id,
                "skipped_pipeline_busy",
            )

            return {
                "status":
                    "skipped_pipeline_busy"
            }

        mark_pipeline_started(
            event_id
        )

        print(
            "[whoop-webhook] "
            "starting daily pipeline "
            f"event_id={event_id} "
            f"type={event_type} "
            f"trace_id={trace_id}",
            flush=True,
        )

        result = (
            run_daily_pipeline()
        )

        mark_pipeline_completed(
            event_id
        )

        print(
            "[whoop-webhook] "
            "daily pipeline finished "
            f"event_id={event_id} "
            f"type={event_type} "
            f"trace_id={trace_id} "
            f"status={result.get('status')}",
            flush=True,
        )

        return result


def _run_immediate_pipeline(
    event_id: int,
    trace_id: str,
    event_type: str,
) -> None:

    try:

        _execute_pipeline_once(
            event_id,
            trace_id,
            event_type,
        )

    except Exception as exc:

        error_text = (
            f"{type(exc).__name__}: {exc}\n"
            f"{traceback.format_exc()}"
        )

        mark_pipeline_failed(
            event_id,
            error_text,
        )

        print(
            "[whoop-webhook] "
            "pipeline failed "
            f"event_id={event_id} "
            f"type={event_type} "
            f"trace_id={trace_id} "
            f"error={type(exc).__name__}: {exc}",
            flush=True,
        )


async def _run_sleep_pipeline(
    event_id: int,
    trace_id: str,
    event_type: str,
) -> None:

    print(
        "[whoop-webhook] "
        "sleep event received; "
        f"waiting {SLEEP_EVENT_DELAY_SECONDS}s "
        "for WHOOP Recovery processing "
        f"event_id={event_id} "
        f"trace_id={trace_id}",
        flush=True,
    )

    await asyncio.sleep(
        SLEEP_EVENT_DELAY_SECONDS
    )

    try:

        for attempt in range(
            1,
            SLEEP_EVENT_MAX_ATTEMPTS + 1,
        ):

            result = await asyncio.to_thread(
                _execute_pipeline_once,
                event_id,
                trace_id,
                event_type,
            )

            status = (
                result.get(
                    "status"
                )
                if result
                else None
            )

            if status == "completed":

                return

            if status == "skipped_pipeline_busy":

                return

            if status not in {
                "pending_freshness",
                "stale_data",
            }:

                return

            if (
                attempt
                >= SLEEP_EVENT_MAX_ATTEMPTS
            ):

                print(
                    "[whoop-webhook] "
                    "Recovery still not ready after "
                    f"{attempt} attempts "
                    f"event_id={event_id} "
                    f"trace_id={trace_id}",
                    flush=True,
                )

                return

            print(
                "[whoop-webhook] "
                "Recovery not ready; "
                f"retrying in "
                f"{SLEEP_EVENT_RETRY_SECONDS}s "
                f"attempt={attempt} "
                f"event_id={event_id} "
                f"trace_id={trace_id}",
                flush=True,
            )

            await asyncio.sleep(
                SLEEP_EVENT_RETRY_SECONDS
            )

    except Exception as exc:

        error_text = (
            f"{type(exc).__name__}: {exc}\n"
            f"{traceback.format_exc()}"
        )

        mark_pipeline_failed(
            event_id,
            error_text,
        )

        print(
            "[whoop-webhook] "
            "sleep-triggered pipeline failed "
            f"event_id={event_id} "
            f"trace_id={trace_id} "
            f"error={type(exc).__name__}: {exc}",
            flush=True,
        )


# ============================================================
# WEBHOOK ENDPOINT
# ============================================================

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

    if not trace_id:

        raise HTTPException(
            status_code=400,
            detail=(
                "WHOOP webhook payload "
                "is missing trace_id."
            ),
        )

    if not event_type:

        raise HTTPException(
            status_code=400,
            detail=(
                "WHOOP webhook payload "
                "is missing type."
            ),
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

    event_id = (
        store_webhook_event(
            trace_id=trace_id,
            event_type=event_type,
            resource_id=resource_id,
            user_id=user_id,
            payload=payload,
        )
    )

    # --------------------------------------------------------
    # Exact duplicate:
    #
    # same trace_id + same event_type
    #
    # A related event with the same trace_id but a DIFFERENT
    # event_type is NOT a duplicate.
    # --------------------------------------------------------

    if event_id is None:

        print(
            "[whoop-webhook] "
            "exact duplicate ignored "
            f"type={event_type} "
            f"trace_id={trace_id}",
            flush=True,
        )

        return {
            "status":
                "duplicate_ignored",

            "event_type":
                event_type,

            "trace_id":
                trace_id,
        }

    # --------------------------------------------------------
    # RECOVERY
    # --------------------------------------------------------

    if event_type == "recovery.updated":

        background_tasks.add_task(
            _run_immediate_pipeline,
            event_id,
            trace_id,
            event_type,
        )

        return {
            "status":
                "accepted",

            "event_id":
                event_id,

            "event_type":
                event_type,

            "trace_id":
                trace_id,

            "pipeline_triggered":
                True,

            "trigger_mode":
                "immediate",
        }

    # --------------------------------------------------------
    # WORKOUT
    # --------------------------------------------------------

    if event_type == "workout.updated":

        background_tasks.add_task(
            _run_immediate_pipeline,
            event_id,
            trace_id,
            event_type,
        )

        return {
            "status":
                "accepted",

            "event_id":
                event_id,

            "event_type":
                event_type,

            "trace_id":
                trace_id,

            "pipeline_triggered":
                True,

            "trigger_mode":
                "immediate",
        }

    # --------------------------------------------------------
    # SLEEP FALLBACK
    # --------------------------------------------------------

    if event_type == "sleep.updated":

        background_tasks.add_task(
            _run_sleep_pipeline,
            event_id,
            trace_id,
            event_type,
        )

        return {
            "status":
                "accepted",

            "event_id":
                event_id,

            "event_type":
                event_type,

            "trace_id":
                trace_id,

            "pipeline_triggered":
                True,

            "trigger_mode":
                "wait_for_recovery",
        }

    # --------------------------------------------------------
    # OTHER EVENTS
    # --------------------------------------------------------

    return {
        "status":
            "accepted",

        "event_id":
            event_id,

        "event_type":
            event_type,

        "trace_id":
            trace_id,

        "pipeline_triggered":
            False,
    }