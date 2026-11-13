"""Demo tests mock only its HTTP boundary; root API tests use real Redis."""
import io
import json
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.test import Client, SimpleTestCase, override_settings

from . import client


def decision(allowed=True, used=1):
    return {
        "allowed": allowed, "limit": 5, "window_seconds": 60,
        "used": used, "remaining": 5 - used,
        "retry_after": 0 if allowed else 60,
        "next_available_at": None if used < 5 else 2000000000,
    }


@override_settings(DEMO_API_KEY="private-demo-test-key")
class DemoTests(SimpleTestCase):
    def test_home_does_not_render_api_key(self):
        response = self.client.get("/")
        self.assertContains(response, 'id="request-form"')
        self.assertNotContains(response, 'id="message-form"')
        self.assertNotContains(response, "private-demo-test-key")

    def test_home_versions_all_assets_and_prevents_stale_html(self):
        assets = Path(__file__).resolve().parent / "static" / "support"
        names = ("demo.css", "timeline.js", "demo.js")
        payloads = [(assets / name).read_bytes() for name in names]
        version = sha256(b"".join(payloads)).hexdigest()[:12]
        response = self.client.get("/")
        for name in names:
            self.assertContains(response, f"/static/support/{name}?v={version}")
        self.assertIn("no-store", response["Cache-Control"])
        # A new CSS or script revision must bypass the previously cached URLs.
        for changed in range(len(names)):
            updated = list(payloads)
            updated[changed] += b"\n/* new revision */"
            with patch("support.views.Path.read_bytes", side_effect=updated):
                refreshed = self.client.get("/")
            self.assertNotContains(refreshed, f"?v={version}")
            self.assertContains(refreshed, "Live request timeline")

    @patch("support.client.consume", return_value=(200, decision()))
    def test_api_admin_cookie_cannot_invalidate_demo_request(self, consume):
        browser = Client(enforce_csrf_checks=True)
        browser.get("/")
        demo_token = browser.cookies["demo_csrftoken"].value
        browser.cookies["csrftoken"] = "x" * 32
        response = browser.post(
            "/requests/", {"user_id": "alice"}, content_type="application/json",
            HTTP_X_CSRFTOKEN=demo_token, HTTP_ORIGIN="http://testserver",
        )
        self.assertEqual(response.status_code, 200)
        consume.assert_called_once_with("alice")

    @patch("support.client.consume")
    @patch("support.client.get_status", return_value=(200, decision(False, 5)))
    def test_polling_reads_status_without_consuming(self, get_status, consume):
        for _ in range(3):
            response = self.client.get("/quota/?user_id=bob")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["remaining"], 0)
            self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(get_status.call_count, 3)
        consume.assert_not_called()

    @patch("support.client.get_status", side_effect=client.LimiterUnavailable("API down"))
    def test_status_unavailable_returns_503(self, get_status):
        self.assertEqual(self.client.get("/quota/?user_id=alice").status_code, 503)


    @patch("support.client.consume", return_value=(200, decision()))
    def test_request_button_consumes_for_selected_user(self, consume):
        for user in ("alice", "bob"):
            response = self.client.post("/requests/", {"user_id": user}, content_type="application/json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["user_id"], user)
            self.assertTrue(response.json()["quota"]["allowed"])
            self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual([call.args[0] for call in consume.call_args_list], ["alice", "bob"])

    @patch("support.client.consume", return_value=(429, decision(False, 5)))
    def test_request_button_returns_rate_limit_and_retry_header(self, consume):
        response = self.client.post("/requests/", {"user_id": "alice"}, content_type="application/json")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")
        self.assertFalse(response.json()["quota"]["allowed"])

    @patch("support.client.consume", side_effect=client.LimiterUnavailable("API unavailable"))
    def test_request_button_fails_closed_when_api_is_unavailable(self, consume):
        response = self.client.post("/requests/", {"user_id": "alice"}, content_type="application/json")
        self.assertEqual(response.status_code, 503)

    @patch("support.client.consume")
    def test_invalid_request_button_input_never_consumes(self, consume):
        for data in ({}, {"user_id": ""}, {"user_id": 1}, {"user_id": "alice", "limit": 50}, []):
            self.assertEqual(self.client.post("/requests/", data, content_type="application/json").status_code, 400)
        consume.assert_not_called()

    @patch("support.client.consume", return_value=(200, decision()))
    def test_request_button_enforces_csrf(self, consume):
        browser = Client(enforce_csrf_checks=True)
        browser.get("/")
        payload = {"user_id": "alice"}
        self.assertEqual(browser.post("/requests/", payload, content_type="application/json").status_code, 403)
        consume.assert_not_called()
        token = browser.cookies["demo_csrftoken"].value
        response = browser.post("/requests/", payload, content_type="application/json", HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="http://testserver")
        self.assertEqual(response.status_code, 200)
        consume.assert_called_once_with("alice")


@override_settings(DEMO_API_KEY="private-demo-test-key", LIMITER_BASE_URL="http://127.0.0.1:8200")
class HttpClientTests(SimpleTestCase):
    @patch("support.client.urlopen")
    def test_http_request_has_server_side_key_and_expected_contract(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = json.dumps(decision()).encode()
        status, result = client.consume("alice")
        self.assertEqual(status, 200)
        self.assertTrue(result["allowed"])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8200/api/limits/consume/")
        self.assertEqual(request.get_header("Authorization"), "Bearer private-demo-test-key")
        self.assertEqual(json.loads(request.data), {"user_id": "alice"})
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 2)

    @patch("support.client.urlopen")
    def test_http_429_body_is_returned_without_retry(self, urlopen):
        urlopen.side_effect = HTTPError("url", 429, "Too many requests", {}, io.BytesIO(json.dumps(decision(False, 5)).encode()))
        status, result = client.consume("alice")
        self.assertEqual(status, 429)
        self.assertFalse(result["allowed"])
        urlopen.assert_called_once()

    @patch("support.client.urlopen")
    def test_network_error_is_unavailable_without_retry(self, urlopen):
        urlopen.side_effect = URLError("connection refused")
        with self.assertRaises(client.LimiterUnavailable):
            client.consume("alice")
        urlopen.assert_called_once()

    @patch("support.client.urlopen")
    def test_http_503_is_unavailable_without_retry(self, urlopen):
        urlopen.side_effect = HTTPError("url", 503, "Unavailable", {}, io.BytesIO(b"{}"))
        with self.assertRaises(client.LimiterUnavailable):
            client.consume("alice")
        urlopen.assert_called_once()

    @patch("support.client.urlopen")
    def test_malformed_json_is_unavailable(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b"not-json"
        with self.assertRaises(client.LimiterUnavailable):
            client.get_status("alice")

    @patch("support.client.urlopen")
    def test_missing_response_fields_are_unavailable(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b'{"allowed":true}'
        with self.assertRaises(client.LimiterUnavailable):
            client.consume("alice")
