"""Core HTTP tests. Time boundaries are covered in test_engine.py."""
import os
import uuid
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from redis.exceptions import ConnectionError, RedisError
from rest_framework.test import APIClient as DRFTestClient

from .models import APIClient
from .services import redis_backend


@override_settings(REDIS_URL=os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15"))
class RateLimitApiTests(TestCase):
    def setUp(self):
        self.redis = redis_backend(settings.REDIS_URL)[0]
        try:
            self.redis.ping()
        except RedisError as error:
            raise AssertionError("Start Redis or set TEST_REDIS_URL before running tests.") from error

        self.prefix = f"rl-api-test-{uuid.uuid4().hex}"
        options = override_settings(REDIS_KEY_PREFIX=self.prefix)
        options.enable()
        self.addCleanup(options.disable)
        self.addCleanup(self._delete_own_keys)
        self.api_client = APIClient.objects.create(
            name="support", max_requests=5, window_seconds=60,
        )
        self.client = DRFTestClient()
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self.api_client.generate_api_key()}",
        )
        self.consume_url = reverse("consume")
        self.status_url = reverse("limit-status")

    def _delete_own_keys(self):
        keys = list(self.redis.scan_iter(match=f"{self.prefix}:*"))
        if keys:
            self.redis.delete(*keys)

    def post(self, payload=None):
        data = {"user_id": "alice"} if payload is None else payload
        return self.client.post(self.consume_url, data, format="json")

    def test_limit_returns200_then429(self):
        max_requests = self.api_client.max_requests
        for used in range(1, max_requests + 1):
            response = self.post()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.data["allowed"])
            self.assertEqual(response.data["remaining"], max_requests - used)

        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertFalse(response.data["allowed"])
        self.assertEqual(response.data["used"], max_requests)
        self.assertEqual(response["X-RateLimit-Remaining"], "0")
        self.assertGreater(int(response["Retry-After"]), 0)

    def test_users_have_independent_quotas(self):
        for _ in range(self.api_client.max_requests):
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post().status_code, 429)
        response = self.post({"user_id": "bob"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["used"], 1)

    def test_status_does_not_consume(self):
        self.post()
        for _ in range(2):
            response = self.client.get(self.status_url, {"user_id": "alice"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["used"], 1)
            self.assertEqual(response.data["remaining"], self.api_client.max_requests - 1)
        self.assertEqual(self.post().data["used"], 2)

    def test_missing_or_invalid_key_returns401(self):
        self.client.credentials()
        self.assertEqual(self.post().status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid-key")
        self.assertEqual(self.post().status_code, 401)

    def test_invalid_input_returns400_without_consuming(self):
        for payload in ({}, {"user_id": ""}, {"user_id": 42}, {"user_id": "alice", "limit": 99}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code, 400)
        response = self.client.get(self.status_url, {"user_id": "alice"})
        self.assertEqual(response.data["used"], 0)

    def test_redis_failure_returns503(self):
        with patch("limiter.views.check_limit", side_effect=ConnectionError("offline")):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("allowed", response.data)
