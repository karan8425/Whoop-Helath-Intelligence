import asyncio
import json
import logging
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse


os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault(
    "TOKEN_ENCRYPTION_KEY",
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
)
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://test:test@localhost/test",
)
os.environ.setdefault("WHOOP_CLIENT_ID", "test-client-id")
os.environ.setdefault("WHOOP_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault(
    "WHOOP_REDIRECT_URI",
    "https://development.example/whoop/callback",
)

from fastapi.testclient import TestClient

import main
import whoop


class _Response:

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "access_token": "mock-access-token",
            "refresh_token": "mock-refresh-token",
            "expires_in": 3600,
        }


class _AsyncClient:

    def __init__(self, *args, **kwargs):
        self.post = AsyncMock(return_value=_Response())

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class WhoopOAuthRouteTests(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(
            main.app,
            base_url="https://testserver",
        )

    def _authenticate(self):
        response = self.client.post(
            "/admin/login",
            data={"password": "test-admin-password"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

    def _start_oauth(self):
        self._authenticate()
        response = self.client.get(
            "/whoop/login",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 307)
        query = parse_qs(urlparse(response.headers["location"]).query)
        return query["state"][0]

    def test_callback_route_exists(self):
        paths = {
            route.path
            for route in main.app.routes
            if hasattr(route, "path")
        }
        self.assertIn("/whoop/callback", paths)

    def test_successful_callback_uses_existing_exchange(self):
        state = self._start_oauth()

        with patch.object(
            main,
            "exchange_code",
            new=AsyncMock(return_value={"status": "ok"}),
        ) as exchange:
            response = self.client.get(
                "/whoop/callback",
                params={
                    "code": "mock-authorization-code",
                    "state": state,
                },
            )

        self.assertEqual(response.status_code, 200)
        exchange.assert_awaited_once_with(
            "mock-authorization-code"
        )

    def test_exchange_code_invokes_encrypted_token_persistence_path(self):
        with (
            patch.object(whoop.httpx, "AsyncClient", _AsyncClient),
            patch.object(whoop, "save_token_json") as save_token,
        ):
            result = asyncio.run(
                whoop.exchange_code(
                    "mock-authorization-code"
                )
            )

        self.assertEqual(
            result["access_token"],
            "mock-access-token",
        )
        save_token.assert_called_once()
        persisted = json.loads(
            save_token.call_args.args[0]
        )
        self.assertEqual(
            persisted["refresh_token"],
            "mock-refresh-token",
        )

    def test_oauth_error_is_handled_without_reflection(self):
        state = self._start_oauth()
        provider_error = "provider-error-that-must-not-be-reflected"

        response = self.client.get(
            "/whoop/callback",
            params={
                "error": provider_error,
                "state": state,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn(provider_error, response.text)
        self.assertIn(
            "WHOOP authorization was not completed.",
            response.text,
        )

    def test_missing_code_fails_cleanly(self):
        state = self._start_oauth()

        response = self.client.get(
            "/whoop/callback",
            params={"state": state},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing authorization code.", response.text)

    def test_callback_does_not_log_or_expose_code_or_tokens(self):
        state = self._start_oauth()
        authorization_code = "sensitive-authorization-code"
        token_text = "sensitive-token-value"

        with (
            patch.object(
                main,
                "exchange_code",
                new=AsyncMock(
                    side_effect=RuntimeError(token_text)
                ),
            ),
            patch("builtins.print") as print_mock,
            patch.object(logging.Logger, "_log") as log_mock,
        ):
            response = self.client.get(
                "/whoop/callback",
                params={
                    "code": authorization_code,
                    "state": state,
                },
            )

        self.assertEqual(response.status_code, 502)
        captured = (
            response.text
            + repr(print_mock.call_args_list)
            + repr(log_mock.call_args_list)
        )
        self.assertNotIn(authorization_code, captured)
        self.assertNotIn(token_text, captured)

    def test_historical_login_route_remains_admin_protected(self):
        unauthenticated = self.client.get(
            "/whoop/login",
            follow_redirects=False,
        )
        self.assertEqual(unauthenticated.status_code, 401)

        state = self._start_oauth()
        self.assertEqual(len(state), 8)

    def test_invalid_state_is_rejected_before_exchange(self):
        self._start_oauth()

        with patch.object(
            main,
            "exchange_code",
            new=AsyncMock(),
        ) as exchange:
            response = self.client.get(
                "/whoop/callback",
                params={
                    "code": "mock-authorization-code",
                    "state": "incorrect",
                },
            )

        self.assertEqual(response.status_code, 400)
        exchange.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
