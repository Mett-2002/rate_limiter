"""API, model, and admin tests; all Redis keys use isolated random prefixes."""
import hashlib
import os
import uuid
from html.parser import HTMLParser
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from redis.exceptions import ConnectionError
from rest_framework.test import APIClient as DRFTestClient

from .models import APIClient
from .services import redis_backend


class _ControlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"input", "button"} and "id" in attributes:
            self.controls[attributes["id"]] = (tag, attributes)


class PublicApiPagesTests(SimpleTestCase):
    def test_index_defaults_to_json_and_preserves_existing_fields(self):
        response = self.client.get(reverse("api-index"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        data = response.json()
        self.assertEqual(data["name"], "Internal rate limiter")
        self.assertEqual(data["algorithm"], "Exact sliding-window log (Redis FIFO + atomic Lua)")
        self.assertEqual(data["semantics"], "(now - window, now]; exact-boundary timestamps expire")
        self.assertEqual(data["authentication"], "Authorization: Bearer YOUR_API_CLIENT_KEY")
        self.assertEqual(data["body_example"], {"user_id": "alice"})
        self.assertEqual(data["consume"], reverse("consume"))
        self.assertEqual(data["status"], reverse("limit-status") + "?user_id=alice")
        self.assertEqual(data["health"], reverse("health"))
        self.assertEqual(data["admin"], reverse("admin:index"))
        self.assertEqual(data["test_api"], reverse("playground"))
        self.assertIn("POST", data["help"])
        self.assertIn("Bearer", data["help"])
        endpoints = {(endpoint["method"], endpoint["path"]): endpoint
                     for endpoint in data["endpoints"]}
        self.assertIn(("POST", reverse("consume")), endpoints)
        self.assertIn(("GET", reverse("limit-status")), endpoints)
        self.assertIn(("GET", reverse("health")), endpoints)
        for endpoint in data["endpoints"]:
            self.assertEqual(set(endpoint), {"method", "path", "authentication", "description"})
            self.assertTrue(endpoint["description"])
            self.assertTrue(endpoint["authentication"])
        self.assertIn("Bearer", endpoints[("POST", reverse("consume"))]["authentication"])
        self.assertIn("Bearer", endpoints[("GET", reverse("limit-status"))]["authentication"])

    def test_browser_index_renders_guidance_and_test_link(self):
        response = self.client.get(reverse("api-index"), HTTP_ACCEPT="text/html")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])
        self.assertTemplateUsed(response, "limiter/index.html")
        for text in ("Test the API", "POST", "Bearer", reverse("playground")):
            self.assertContains(response, text)

    def test_playground_is_public_and_protects_api_key_entry(self):
        response = self.client.get(reverse("playground"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "limiter/playground.html")
        parser = _ControlParser()
        parser.feed(response.content.decode())
        key_tag, key_attributes = parser.controls["api-key"]
        self.assertEqual(key_tag, "input")
        self.assertEqual(key_attributes["type"], "password")
        for control_id in ("consume-button", "status-button"):
            self.assertEqual(parser.controls[control_id][0], "button")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "same-origin")

    def test_playground_does_not_accept_posts(self):
        response = self.client.post(reverse("playground"), {"api_key": "not-a-real-key"})
        self.assertEqual(response.status_code, 405)
        self.assertIn("no-store", response["Cache-Control"])

    @patch("limiter.views.redis_backend")
    def test_health_stays_public_for_json_clients_and_browsers(self, backend):
        redis_client = backend.return_value
        backend.return_value = (redis_client, None)
        for accept in ("application/json", "text/html"):
            with self.subTest(accept=accept):
                response = self.client.get(reverse("health"), HTTP_ACCEPT=accept)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateNotUsed(response, "limiter/index.html")
                if accept == "application/json":
                    self.assertEqual(response.json(), {"redis": "ok"})
        self.assertEqual(redis_client.ping.call_count, 2)


class APIClientModelTests(TestCase):
    def test_positive_fields_reject_zero_during_validation(self):
        for field in ("max_requests", "window_seconds"):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError) as caught:
                    APIClient(name="validation", **{field: 0}).full_clean()
                self.assertIn(field, caught.exception.message_dict)

    def test_database_constraints_reject_zero_without_validation(self):
        for field in ("max_requests", "window_seconds"):
            with self.subTest(field=field):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    APIClient.objects.create(name=field, **{field: 0})

    def test_key_generation_stores_only_hash_and_preserves_policy(self):
        api_client = APIClient.objects.create(name="support")
        version = api_client.policy_version
        first_key = api_client.generate_api_key()
        self.assertEqual(api_client.api_key_hash, hashlib.sha256(first_key.encode()).hexdigest())
        self.assertNotEqual(first_key, api_client.api_key_hash)
        self.assertNotEqual(first_key, api_client.generate_api_key())
        api_client.refresh_from_db()
        self.assertEqual(api_client.policy_version, version)

    def test_editing_each_rule_field_changes_policy_version(self):
        api_client = APIClient.objects.create(name="support")
        for field, value in (("max_requests", 7), ("window_seconds", 30)):
            previous = api_client.policy_version
            setattr(api_client, field, value)
            api_client.save(update_fields=[field])
            api_client.refresh_from_db()
            self.assertNotEqual(api_client.policy_version, previous)

    def test_renaming_and_disabling_preserve_policy(self):
        api_client = APIClient.objects.create(name="support")
        version = api_client.policy_version
        api_client.name = "renamed"
        api_client.is_active = False
        api_client.save()
        api_client.refresh_from_db()
        self.assertEqual(api_client.policy_version, version)


class RateLimitApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.redis_settings = override_settings(REDIS_URL=os.environ.get(
            "TEST_REDIS_URL", "redis://127.0.0.1:6379/15"))
        cls.redis_settings.enable()
        try:
            cls.redis = redis_backend(settings.REDIS_URL)[0]
            cls.redis.ping()
        except Exception as exc:
            cls.redis_settings.disable()
            raise AssertionError("Start real Redis before running API tests.") from exc

    @classmethod
    def tearDownClass(cls):
        cls.redis_settings.disable()
        super().tearDownClass()

    def setUp(self):
        self.prefix = f"rl-api-test-{uuid.uuid4().hex}"
        options = override_settings(REDIS_KEY_PREFIX=self.prefix)
        options.enable()
        self.addCleanup(options.disable)
        self.addCleanup(self._delete_own_keys)
        self.api_client = APIClient.objects.create(name="support")
        self.key = self.api_client.generate_api_key()
        self.client = DRFTestClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.key}")
        self.consume_url = reverse("consume")
        self.status_url = reverse("limit-status")

    def _delete_own_keys(self):
        keys = list(self.redis.scan_iter(match=f"{self.prefix}:*"))
        if keys:
            self.redis.delete(*keys)

    def post(self, payload=None):
        return self.client.post(self.consume_url,
            {"user_id": "alice"} if payload is None else payload, format="json")

    def test_first_five_http200_sixth429_with_headers(self):
        for remaining in (4, 3, 2, 1, 0):
            response = self.post()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.data["allowed"])
            self.assertEqual(response.data["api_client"], "support")
            self.assertEqual(response.data["remaining"], remaining)
            self.assertEqual(response["X-RateLimit-Limit"], "5")
            self.assertEqual(response["X-RateLimit-Remaining"], str(remaining))
            self.assertEqual(response["Cache-Control"], "no-store")
        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertFalse(response.data["allowed"])
        self.assertGreater(int(response["Retry-After"]), 0)
        self.assertEqual(response.data["used"], 5)

    def test_bad_authentication_returns401(self):
        headers = (None, "Bearer wrong-key", "Bearer", "Bearer a b", "Basic abc",
                   "Bearer " + "a" * 129)
        for index, header in enumerate(headers):
            with self.subTest(case=index):
                self.client.credentials(**({} if header is None else {
                    "HTTP_AUTHORIZATION": header}))
                response = self.post()
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response["WWW-Authenticate"], "Bearer")
                self.assertEqual(response["Cache-Control"], "no-store")

    def test_inactive_api_client_cannot_consume(self):
        self.api_client.is_active = False
        self.api_client.save(update_fields=["is_active"])
        self.assertEqual(self.post().status_code, 401)

    def test_invalid_payloads_return400_without_consuming(self):
        payloads = [{}, {"user_id": ""}, {"user_id": "  "}, {"user_id": None},
            {"user_id": 42}, {"user_id": True}, {"user_id": []}, {"user_id": {}},
            {"user_id": "a" * 129}, [], "alice", 42,
            {"user_id": "alice", "limit": 99},
            {"user_id": "alice", "now_us": 1}]
        for index, payload in enumerate(payloads):
            with self.subTest(case=index):
                self.assertEqual(self.post(payload).status_code, 400)
        self.assertEqual(self.client.get(self.status_url, {"user_id": "alice"}).data["used"], 0)

    def test_persian_and_trimmed_identifier_share_quota(self):
        first = self.post({"user_id": "  کاربر-۱  "})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["user_id"], "کاربر-۱")
        self.assertEqual(self.post({"user_id": "کاربر-۱"}).data["used"], 2)

    def test_status_does_not_consume_even_when_full(self):
        for _ in range(5):
            self.post()
        for _ in range(2):
            response = self.client.get(self.status_url, {"user_id": "alice"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["used"], 5)
            self.assertFalse(response.data["allowed"])
        self.assertEqual(self.post().status_code, 429)

    def test_get_consume_returns405(self):
        self.assertEqual(self.client.get(self.consume_url).status_code, 405)

    def test_redis_failure_fails_closed_with503(self):
        with patch("limiter.views.check_limit", side_effect=ConnectionError("offline")):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("allowed", response.data)
        self.assertEqual(response["Cache-Control"], "no-store")
        with patch("limiter.views.check_limit", side_effect=ConnectionError("offline")):
            response = self.client.get(self.status_url, {"user_id": "alice"})
        self.assertEqual(response.status_code, 503)

    def test_rotation_revokes_old_key_but_preserves_quota(self):
        for _ in range(5):
            self.post()
        version = self.api_client.policy_version
        replacement = self.api_client.generate_api_key()
        self.assertEqual(self.post().status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {replacement}")
        self.assertEqual(self.post().status_code, 429)
        self.api_client.refresh_from_db()
        self.assertEqual(self.api_client.policy_version, version)

    def test_policy_edit_starts_new_quota(self):
        for _ in range(5):
            self.post()
        self.api_client.max_requests = 2
        self.api_client.save(update_fields=["max_requests"])
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["used"], 1)
        self.assertEqual(response.data["limit"], 2)

    def test_users_and_api_clients_have_independent_quota(self):
        for _ in range(5):
            self.post()
        self.assertEqual(self.post({"user_id": "bob"}).status_code, 200)
        other = APIClient.objects.create(name="orders")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {other.generate_api_key()}")
        self.assertEqual(self.post().data["used"], 1)


class APIClientAdminTests(TestCase):
    def setUp(self):
        admin = get_user_model().objects.create_superuser(
            username="test-admin", password="test-password-for-tests-only")
        self.api_client = APIClient.objects.create(name="support")
        self.api_client.generate_api_key()
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(admin)
        self.key_url = reverse("admin:limiter_apiclient_key", args=[self.api_client.pk])

    def test_get_key_page_does_not_rotate(self):
        original_hash = self.api_client.api_key_hash
        response = self.client.get(self.key_url)
        self.assertEqual(response.status_code, 200)
        self.api_client.refresh_from_db()
        self.assertEqual(self.api_client.api_key_hash, original_hash)
        self.assertIsNone(response.context_data["token"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_rotation_requires_post_with_valid_csrf(self):
        original_hash = self.api_client.api_key_hash
        self.assertEqual(self.client.post(self.key_url).status_code, 403)
        self.api_client.refresh_from_db()
        self.assertEqual(self.api_client.api_key_hash, original_hash)
        self.client.get(self.key_url)
        csrf = self.client.cookies["csrftoken"].value
        response = self.client.post(self.key_url, {"csrfmiddlewaretoken": csrf})
        self.assertEqual(response.status_code, 200)
        token = response.context_data["token"]
        self.api_client.refresh_from_db()
        self.assertNotEqual(self.api_client.api_key_hash, original_hash)
        self.assertEqual(self.api_client.api_key_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertIn("no-store", response["Cache-Control"])
        for cookie in response.cookies.values():
            self.assertNotIn(token, cookie.OutputString())

    def test_nonstaff_cannot_issue_keys(self):
        user = get_user_model().objects.create_user(username="normal-user")
        client = Client()
        client.force_login(user)
        before = self.api_client.api_key_hash
        self.assertEqual(client.post(self.key_url).status_code, 302)
        self.api_client.refresh_from_db()
        self.assertEqual(self.api_client.api_key_hash, before)

    def test_staff_without_permission_cannot_issue_keys(self):
        user = get_user_model().objects.create_user(username="limited-staff", is_staff=True)
        client = Client()
        client.force_login(user)
        self.assertEqual(client.post(self.key_url).status_code, 403)

    def test_admin_creation_displays_secret_without_storing_plaintext(self):
        add_url = reverse("admin:limiter_apiclient_add")
        self.client.get(add_url)
        csrf = self.client.cookies["csrftoken"].value
        response = self.client.post(add_url, {
            "csrfmiddlewaretoken": csrf, "name": "orders", "max_requests": 5,
            "window_seconds": 60, "is_active": "on", "_save": "Save"})
        self.assertEqual(response.status_code, 200)
        token = response.context_data["token"]
        row = APIClient.objects.values().get(name="orders")
        self.assertEqual(row["api_key_hash"], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, [str(value) for value in row.values()])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        for cookie in response.cookies.values():
            self.assertNotIn(token, cookie.OutputString())


    def test_key_form_keeps_same_origin_posts_and_rejects_null_origin(self):
        response = self.client.get(self.key_url)
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        csrf = self.client.cookies["csrftoken"].value
        before = self.api_client.api_key_hash
        rejected = self.client.post(self.key_url, {"csrfmiddlewaretoken": csrf}, HTTP_ORIGIN="null")
        self.assertEqual(rejected.status_code, 403)
        self.api_client.refresh_from_db()
        self.assertEqual(self.api_client.api_key_hash, before)
        accepted = self.client.post(self.key_url, {"csrfmiddlewaretoken": csrf}, HTTP_ORIGIN="http://testserver")
        self.assertEqual(accepted.status_code, 200)
        self.assertIsNotNone(accepted.context_data["token"])
