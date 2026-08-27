import getpass
import json

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


def authorized_get(
    id_token: str,
    path: str,
):

    url = (
        path
        if path.startswith("http")
        else API_BASE + path
    )

    response = requests.get(
        url,
        headers={
            "Authorization":
                f"Bearer {id_token}"
        },
        timeout=30,
    )

    return response


def describe_response(
    name: str,
    response,
):

    print(
        f"\n===== {name} ====="
    )

    print(
        f"HTTP {response.status_code}"
    )

    if response.status_code != 200:

        print(
            response.text[:500]
        )

        return None

    try:
        data = response.json()

    except ValueError:

        print(
            "Response was not JSON."
        )

        print(
            response.text[:500]
        )

        return None

    print(
        "Response type:",
        type(data).__name__,
    )

    if isinstance(data, list):

        print(
            "Record count:",
            len(data),
        )

        if data:

            print(
                "First record keys:"
            )

            print(
                json.dumps(
                    list(
                        data[0].keys()
                    ),
                    indent=2,
                )
            )

            print(
                "First record sample:"
            )

            print(
                json.dumps(
                    data[0],
                    indent=2,
                    default=str,
                )[:3000]
            )

    elif isinstance(data, dict):

        print(
            "Top-level keys:"
        )

        print(
            json.dumps(
                list(
                    data.keys()
                ),
                indent=2,
            )
        )

        print(
            "Sample:"
        )

        print(
            json.dumps(
                data,
                indent=2,
                default=str,
            )[:3000]
        )

    return data


def main():

    print(
        "\nTonal Movement Catalog Test"
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

    tokens = authenticate(
        email,
        password,
    )

    id_token = tokens.get(
        "id_token"
    )

    if not id_token:
        raise RuntimeError(
            "No id_token returned."
        )

    print(
        "Authentication successful."
    )

    candidate_paths = [
        (
            "Movement Catalog v6",
            "/v6/movements",
        ),

        (
            "Movement Catalog v5",
            "/v5/movements",
        ),

        (
            "Movement Families",
            "/v6/movement-families",
        ),

        (
            "Movement Catalog",
            "/v6/catalog/movements",
        ),

        (
            "Activities",
            "/v6/activities",
        ),
    ]

    successful = []

    for name, path in candidate_paths:

        response = authorized_get(
            id_token,
            path,
        )

        data = describe_response(
            name,
            response,
        )

        if (
            response.status_code == 200
            and data is not None
        ):
            successful.append(
                {
                    "name": name,
                    "path": path,
                }
            )

    print(
        "\n===== SUCCESSFUL ROUTES ====="
    )

    print(
        json.dumps(
            successful,
            indent=2,
        )
    )

    print(
        "\nNo Tonal password or token "
        "was written to disk."
    )


if __name__ == "__main__":
    main()
