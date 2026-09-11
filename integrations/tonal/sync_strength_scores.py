import getpass
import json

import requests

from db import get_conn
from integrations.tonal.client import (
    authenticate as shared_authenticate,
    tonal_get,
)


AUTH0_DOMAIN = "tonal.auth0.com"
CLIENT_ID = "ERCyexW-xoVG_Yy3RDe-eV4xsOnRHP6L"
API_BASE = "https://api.tonal.com"


def authenticate(
    email: str,
    password: str,
) -> str:

    token = shared_authenticate(email, password).get(
        "id_token"
    )

    if not token:
        raise RuntimeError(
            "Authentication succeeded but "
            "no id_token was returned."
        )

    return token


def get_user_id(
    token: str,
) -> str:

    response = tonal_get(token, "/v6/users/userinfo")

    if response.status_code != 200:
        raise RuntimeError(
            "Could not retrieve Tonal user information. "
            f"HTTP {response.status_code}."
        )

    data = response.json()

    user_id = (
        data.get("id")
        or data.get("userId")
    )

    if not user_id:
        raise RuntimeError(
            "Could not determine Tonal user ID."
        )

    return user_id


def get_strength_history(
    token: str,
    user_id: str,
) -> list:

    response = tonal_get(
        token,
        f"/v6/users/{user_id}/strength-scores/history",
        params={"limit": 5000},
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Could not retrieve Strength Score history. "
            f"HTTP {response.status_code}."
        )

    records = response.json()

    if not isinstance(
        records,
        list,
    ):
        raise RuntimeError(
            "Unexpected Strength Score response."
        )

    return records


def sync_records(
    records: list,
) -> int:

    processed = 0

    with get_conn() as conn:

        with conn.cursor() as cur:

            for record in records:

                source_record_key = (
                    record.get("id")
                )

                observed_at = (
                    record.get(
                        "activityTime"
                    )
                )

                if (
                    not source_record_key
                    or not observed_at
                ):
                    continue

                cur.execute(
                    """
                    INSERT INTO tonal_strength_scores (
                        source_record_key,
                        observed_at,
                        overall_score,
                        upper_score,
                        lower_score,
                        core_score,
                        raw_data,
                        synced_at
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s::jsonb,
                        NOW()
                    )
                    ON CONFLICT (
                        source_record_key
                    )
                    DO UPDATE SET
                        observed_at =
                            EXCLUDED.observed_at,

                        overall_score =
                            EXCLUDED.overall_score,

                        upper_score =
                            EXCLUDED.upper_score,

                        lower_score =
                            EXCLUDED.lower_score,

                        core_score =
                            EXCLUDED.core_score,

                        raw_data =
                            EXCLUDED.raw_data,

                        synced_at =
                            NOW()
                    """,
                    (
                        source_record_key,

                        observed_at,

                        record.get(
                            "overall"
                        ),

                        record.get(
                            "upper"
                        ),

                        record.get(
                            "lower"
                        ),

                        record.get(
                            "core"
                        ),

                        json.dumps(
                            record
                        ),
                    ),
                )

                processed += 1

    return processed


def validate() -> dict:

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    COUNT(*) AS count,
                    MIN(observed_at) AS oldest,
                    MAX(observed_at) AS newest
                FROM tonal_strength_scores
                """
            )

            summary = (
                cur.fetchone()
            )

            cur.execute(
                """
                SELECT
                    observed_at,
                    overall_score,
                    upper_score,
                    lower_score,
                    core_score
                FROM tonal_strength_scores
                ORDER BY observed_at DESC
                LIMIT 1
                """
            )

            latest = (
                cur.fetchone()
            )

    return {
        "records":
            summary[
                "count"
            ],

        "oldest":
            (
                summary[
                    "oldest"
                ].isoformat()
                if summary[
                    "oldest"
                ]
                else None
            ),

        "newest":
            (
                summary[
                    "newest"
                ].isoformat()
                if summary[
                    "newest"
                ]
                else None
            ),

        "latest":
            (
                {
                    "observed_at":
                        latest[
                            "observed_at"
                        ].isoformat(),

                    "overall":
                        latest[
                            "overall_score"
                        ],

                    "upper":
                        latest[
                            "upper_score"
                        ],

                    "lower":
                        latest[
                            "lower_score"
                        ],

                    "core":
                        latest[
                            "core_score"
                        ],
                }
                if latest
                else None
            ),
    }


def main():

    print(
        "\nTonal Strength Score Sync"
    )

    print(
        "Credentials are entered locally "
        "and are not saved.\n"
    )

    email = input(
        "Tonal email: "
    ).strip()

    password = getpass.getpass(
        "Tonal password: "
    )

    print(
        "\nAuthenticating..."
    )

    token = authenticate(
        email,
        password,
    )

    print(
        "Authentication successful."
    )

    user_id = get_user_id(
        token
    )

    print(
        "Retrieving Strength Score history..."
    )

    records = get_strength_history(
        token,
        user_id,
    )

    print(
        f"Retrieved {len(records)} records."
    )

    print(
        "Writing Strength Scores to Supabase..."
    )

    processed = sync_records(
        records
    )

    password = None
    token = None

    print(
        "\nSYNC COMPLETE"
    )

    print(
        json.dumps(
            {
                "strength_scores_processed":
                    processed
            },
            indent=2,
        )
    )

    print(
        "\nValidating database..."
    )

    print(
        json.dumps(
            validate(),
            indent=2,
            default=str,
        )
    )

    print(
        "\nNo Tonal password or authentication token "
        "was written to disk."
    )


if __name__ == "__main__":
    main()
