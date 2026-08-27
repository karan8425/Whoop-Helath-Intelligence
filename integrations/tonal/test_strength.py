import getpass
import json

import requests


AUTH0_DOMAIN = "tonal.auth0.com"
CLIENT_ID = "ERCyexW-xoVG_Yy3RDe-eV4xsOnRHP6L"
API_BASE = "https://api.tonal.com"


def authenticate(email: str, password: str) -> dict:
    response = requests.post(
        f"https://{AUTH0_DOMAIN}/oauth/token",
        json={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "username": email,
            "password": password,
            "scope": "openid profile email offline_access",
        },
        timeout=30,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "Tonal rejected the email or password."
        )

    if response.status_code == 403:
        raise RuntimeError(
            "Tonal denied access. The account may require verification."
        )

    if response.status_code != 200:
        raise RuntimeError(
            "Tonal authentication failed with "
            f"HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    return response.json()


def get_user_info(id_token: str) -> dict:
    response = requests.get(
        f"{API_BASE}/v6/users/userinfo",
        headers={
            "Authorization": f"Bearer {id_token}"
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


def get_current_strength_scores(
    id_token: str,
    user_id: str,
) -> dict:
    response = requests.get(
        (
            f"{API_BASE}/v6/users/"
            f"{user_id}/strength-scores/current"
        ),
        headers={
            "Authorization": f"Bearer {id_token}"
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Could not retrieve current Tonal Strength Scores. "
            f"HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    data = response.json()

    parsed = {
        "regions": {},
        "muscles": {},
    }

    if not isinstance(data, list):
        raise RuntimeError(
            "Tonal returned an unexpected Strength Score response type."
        )

    for region in data:
        region_name = region.get(
            "strengthBodyRegion",
            "Unknown",
        )

        parsed["regions"][region_name] = (
            region.get("score")
        )

        for muscle in region.get(
            "familyActivity",
            [],
        ):
            muscle_name = muscle.get(
                "strengthFamily",
                "Unknown",
            )

            parsed["muscles"][muscle_name] = {
                "score": muscle.get("score"),
                "region": region_name,
                "updated_at": muscle.get(
                    "updatedAt"
                ),
            }

    return parsed


def main():
    print("\nTonal Strength Score Test")
    print(
        "Your Tonal password is entered locally "
        "and is not saved by this script.\n"
    )

    email = input(
        "Tonal email: "
    ).strip()

    password = getpass.getpass(
        "Tonal password: "
    )

    print("\nAuthenticating with Tonal...")

    tokens = authenticate(
        email,
        password,
    )

    id_token = tokens.get(
        "id_token"
    )

    if not id_token:
        raise RuntimeError(
            "Authentication succeeded, but Tonal "
            "did not return an id_token."
        )

    print("Authentication successful.")
    print("Retrieving Tonal account information...")

    user_info = get_user_info(
        id_token
    )

    user_id = (
        user_info.get("id")
        or user_info.get("userId")
    )

    if not user_id:
        print(
            "\nTonal returned unexpected user-info fields:"
        )

        print(
            json.dumps(
                list(user_info.keys()),
                indent=2,
            )
        )

        raise RuntimeError(
            "Could not determine Tonal user ID."
        )

    print("Retrieving current Strength Scores...")

    strength = get_current_strength_scores(
        id_token,
        user_id,
    )

    print("\nSUCCESS")
    print(
        json.dumps(
            strength,
            indent=2,
            default=str,
        )
    )

    print(
        "\nNo Tonal password or token "
        "was written to disk."
    )


if __name__ == "__main__":
    main()
