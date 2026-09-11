import os
import time

import requests


AUTH0_DOMAIN = "tonal.auth0.com"
CLIENT_ID = "ERCyexW-xoVG_Yy3RDe-eV4xsOnRHP6L"
API_BASE = "https://api.tonal.com"
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


class TonalConfigurationError(RuntimeError):
    pass


class TonalAuthenticationError(RuntimeError):
    pass


class TonalRequestError(RuntimeError):
    pass


def automation_credentials(environ=None):
    source = os.environ if environ is None else environ
    email = source.get("TONAL_EMAIL")
    password = source.get("TONAL_PASSWORD")

    if not email or not password:
        raise TonalConfigurationError(
            "Required Development Tonal automation credentials are not configured."
        )

    return email, password


def _request_with_retry(
    method,
    url,
    *,
    max_attempts=3,
    backoff_seconds=1.0,
    sleep=time.sleep,
    **kwargs,
):
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.request(method, url, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == max_attempts:
                raise TonalRequestError(
                    "Tonal request failed after bounded transient retries."
                ) from exc
        else:
            if response.status_code not in TRANSIENT_STATUS_CODES:
                return response

            if attempt == max_attempts:
                raise TonalRequestError(
                    f"Tonal request failed with transient HTTP {response.status_code} "
                    "after bounded retries."
                )

        sleep(min(backoff_seconds * (2 ** (attempt - 1)), 8.0))

    raise TonalRequestError("Tonal request failed.")


def authenticate(email, password, **retry_options):
    response = _request_with_retry(
        "POST",
        f"https://{AUTH0_DOMAIN}/oauth/token",
        json={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "username": email,
            "password": password,
            "scope": "openid profile email offline_access",
        },
        timeout=30,
        **retry_options,
    )

    if response.status_code in (401, 403):
        raise TonalAuthenticationError(
            f"Tonal authentication was rejected with HTTP {response.status_code}."
        )

    if response.status_code != 200:
        raise TonalRequestError(
            f"Tonal authentication failed with HTTP {response.status_code}."
        )

    tokens = response.json()
    if not tokens.get("id_token"):
        raise TonalAuthenticationError(
            "Tonal authentication succeeded without a usable identity token."
        )

    return tokens


def tonal_get(id_token, path, *, headers=None, params=None, **retry_options):
    request_headers = {"Authorization": f"Bearer {id_token}"}
    if headers:
        request_headers.update(headers)

    return _request_with_retry(
        "GET",
        API_BASE + path,
        headers=request_headers,
        params=params,
        timeout=60,
        **retry_options,
    )
