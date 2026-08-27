import getpass
import json
from datetime import datetime

import requests


AUTH0_DOMAIN = "tonal.auth0.com"
CLIENT_ID = "ERCyexW-xoVG_Yy3RDe-eV4xsOnRHP6L"
API_BASE = "https://api.tonal.com"


def authenticate(
    email: str,
    password: str,
) -> dict:

    response = requests.post(
        f"https://{AUTH0_DOMAIN}/oauth/token",
        json={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "username": email,
            "password": password,
            "scope":
                "openid profile email offline_access",
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Tonal authentication failed with "
            f"HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    return response.json()


def get_user_info(
    id_token: str,
) -> dict:

    response = requests.get(
        f"{API_BASE}/v6/users/userinfo",
        headers={
            "Authorization":
                f"Bearer {id_token}"
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Could not retrieve Tonal user information. "
            f"HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    return response.json()


def get_strength_history(
    id_token: str,
    user_id: str,
) -> list:

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    response = requests.get(
        (
            f"{API_BASE}/v6/users/"
            f"{user_id}/strength-scores/history"
        ),
        headers={
            "Authorization":
                f"Bearer {id_token}"
        },
        params={
            "limit": 5000,
            "endDate": today,
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Could not retrieve Strength Score history. "
            f"HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(
            "Unexpected Strength Score history response."
        )

    return data


def main():

    print(
        "\nTonal Strength Score History Inspector"
    )

    print(
        "Nothing will be written to disk or Supabase.\n"
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

    tokens = authenticate(
        email,
        password,
    )

    id_token = tokens.get(
        "id_token"
    )

    if not id_token:
        raise RuntimeError(
            "Authentication succeeded but "
            "no id_token was returned."
        )

    print(
        "Authentication successful."
    )

    user_info = get_user_info(
        id_token
    )

    user_id = (
        user_info.get(
            "id"
        )
        or user_info.get(
            "userId"
        )
    )

    if not user_id:
        raise RuntimeError(
            "Could not determine Tonal user ID."
        )

    print(
        "Retrieving Strength Score history..."
    )

    records = get_strength_history(
        id_token,
        user_id,
    )

    print(
        f"Record count: {len(records)}"
    )

    if not records:
        print(
            "No Strength Score history found."
        )
        return

    print(
        "\n========================================"
    )
    print(
        "FIRST RAW STRENGTH SCORE RECORD"
    )
    print(
        "========================================"
    )

    print(
        json.dumps(
            records[0],
            indent=2,
            default=str,
        )
    )

    print(
        "\n========================================"
    )
    print(
        "FIRST RECORD KEYS"
    )
    print(
        "========================================"
    )

    if isinstance(
        records[0],
        dict,
    ):
        print(
            json.dumps(
                list(
                    records[0].keys()
                ),
                indent=2,
            )
        )

    if len(records) > 1:

        print(
            "\n========================================"
        )
        print(
            "SECOND RAW STRENGTH SCORE RECORD"
        )
        print(
            "========================================"
        )

        print(
            json.dumps(
                records[1],
                indent=2,
                default=str,
            )
        )

    print(
        "\nInspection complete. "
        "No Tonal password or token "
        "was written to disk."
    )


if __name__ == "__main__":
    main()
