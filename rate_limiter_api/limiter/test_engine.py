"""Real-Redis tests using configurable limits and a deterministic test clock.

Only keys under each test's random prefix are deleted. Never flush Redis.
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from .services import _history_key, check_limit, redis_backend


class SlidingWindowTests(SimpleTestCase):
    START_TIME_US = 1_800_000_000_000_000
    MICROSECONDS_PER_SECOND = 1_000_000
    ONE_MICROSECOND = 1 / MICROSECONDS_PER_SECOND

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.redis_settings = override_settings(
            REDIS_URL=os.environ.get(
                "TEST_REDIS_URL", "redis://127.0.0.1:6379/15"
            )
        )
        cls.redis_settings.enable()
        try:
            cls.redis = redis_backend(settings.REDIS_URL)[0]
            cls.redis.ping()
        except Exception as exc:
            cls.redis_settings.disable()
            raise AssertionError(
                "Real Redis is required. Start Redis, then run the tests again. "
                "Set TEST_REDIS_URL if it uses a different address."
            ) from exc

    @classmethod
    def tearDownClass(cls):
        cls.redis_settings.disable()
        super().tearDownClass()

    def setUp(self):
        self.prefix = f"rl-test-{uuid.uuid4().hex}"
        self.prefix_settings = override_settings(REDIS_KEY_PREFIX=self.prefix)
        self.prefix_settings.enable()
        self.addCleanup(self.prefix_settings.disable)
        self.addCleanup(self._delete_own_keys)
        self.api_client = self.make_api_client(max_requests=5, window_seconds=60)

    def make_api_client(self, *, max_requests, window_seconds, client_id=101):
        """Build a test policy without needing a SQL database."""
        return SimpleNamespace(
            pk=client_id,
            policy_version=uuid.uuid4(),
            max_requests=max_requests,
            window_seconds=window_seconds,
        )

    def _delete_own_keys(self):
        keys = list(self.redis.scan_iter(match=f"{self.prefix}:*"))
        if keys:
            self.redis.delete(*keys)

    def check(self, *, elapsed_seconds=0, user_id="alice", consume_request=True):
        """Check the current policy at a known time relative to the test start."""
        elapsed_us = round(elapsed_seconds * self.MICROSECONDS_PER_SECOND)
        return check_limit(
            self.api_client,
            user_id,
            consume_request=consume_request,
            now_us=self.START_TIME_US + elapsed_us,
        )

    def fill(self, *, user_id="alice", elapsed_seconds=0):
        """Consume exactly the configured number of slots for this user."""
        for _ in range(self.api_client.max_requests):
            result = self.check(user_id=user_id, elapsed_seconds=elapsed_seconds)
            self.assertTrue(result["allowed"])

    def test_configured_limit_is_allowed_then_next_request_is_rejected(self):
        max_requests = self.api_client.max_requests
        window_seconds = self.api_client.window_seconds
        for expected_used in range(1, max_requests + 1):
            result = self.check()
            self.assertTrue(result["allowed"])
            self.assertEqual(result["used"], expected_used)
            self.assertEqual(result["remaining"], max_requests - expected_used)

        rejected = self.check()
        self.assertFalse(rejected["allowed"])
        self.assertEqual(rejected["used"], max_requests)
        self.assertEqual(rejected["retry_after"], window_seconds)
        status = self.check(consume_request=False)
        self.assertEqual(status["used"], max_requests)

    def test_different_request_limits_and_time_windows(self):
        for max_requests, window_seconds in ((1, 10), (3, 30), (5, 60), (8, 120)):
            with self.subTest(max_requests=max_requests, window_seconds=window_seconds):
                self.api_client = self.make_api_client(
                    max_requests=max_requests, window_seconds=window_seconds,
                )
                self.fill()
                rejected = self.check(
                    elapsed_seconds=window_seconds - self.ONE_MICROSECOND,
                )
                self.assertFalse(rejected["allowed"])
                self.assertEqual(rejected["used"], max_requests)
                self.assertEqual(rejected["retry_after"], 1)
                accepted = self.check(elapsed_seconds=window_seconds)
                self.assertTrue(accepted["allowed"])
                self.assertEqual(accepted["used"], 1)
                self.assertEqual(accepted["remaining"], max_requests - 1)
                self.assertEqual(accepted["limit"], max_requests)
                self.assertEqual(accepted["window_seconds"], window_seconds)

    def test_one_microsecond_before_window_end_is_still_full(self):
        self.fill()
        result = self.check(
            elapsed_seconds=self.api_client.window_seconds - self.ONE_MICROSECOND,
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["retry_after"], 1)

    def test_exact_window_end_releases_capacity(self):
        self.fill()
        result = self.check(elapsed_seconds=self.api_client.window_seconds)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["used"], 1)

    def test_after_window_end_releases_capacity(self):
        self.fill()
        result = self.check(
            elapsed_seconds=self.api_client.window_seconds + self.ONE_MICROSECOND,
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["remaining"], self.api_client.max_requests - 1)

    def test_staggered_requests_expire_individually(self):
        # At least two accepted timestamps are needed to show separate expiry.
        self.api_client = self.make_api_client(max_requests=3, window_seconds=30)
        window_seconds = self.api_client.window_seconds
        spacing_seconds = window_seconds / self.api_client.max_requests
        for request_index in range(self.api_client.max_requests):
            result = self.check(elapsed_seconds=request_index * spacing_seconds)
            self.assertTrue(result["allowed"])
        self.assertTrue(self.check(elapsed_seconds=window_seconds)["allowed"])
        second_expiry_seconds = window_seconds + spacing_seconds
        self.assertFalse(self.check(
            elapsed_seconds=second_expiry_seconds - self.ONE_MICROSECOND,
        )["allowed"])
        self.assertTrue(self.check(elapsed_seconds=second_expiry_seconds)["allowed"])

    def test_users_are_independent(self):
        self.fill()
        result = self.check(user_id="bob")
        self.assertTrue(result["allowed"])
        self.assertEqual(result["used"], 1)
        self.assertFalse(self.check()["allowed"])

    def test_api_clients_are_independent(self):
        self.fill()
        self.api_client.pk += 1
        self.assertEqual(self.check()["used"], 1)

    def test_policy_version_starts_a_new_bucket(self):
        self.fill()
        self.api_client.policy_version = uuid.uuid4()
        self.assertEqual(self.check()["used"], 1)

    def test_status_does_not_consume_and_prunes_on_window_end(self):
        max_requests = self.api_client.max_requests
        window_seconds = self.api_client.window_seconds
        self.assertEqual(self.check()["used"], 1)
        status = self.check(elapsed_seconds=window_seconds / 2, consume_request=False)
        self.assertEqual(status["used"], 1)
        self.assertEqual(status["remaining"], max_requests - 1)
        status = self.check(elapsed_seconds=window_seconds, consume_request=False)
        self.assertEqual(status["used"], 0)
        self.assertEqual(status["remaining"], max_requests)

    def test_every_request_prunes_even_when_bucket_is_not_full(self):
        self.check()
        result = self.check(elapsed_seconds=self.api_client.window_seconds)
        self.assertEqual(result["used"], 1)
        key = _history_key(self.api_client, "alice")
        self.assertEqual(self.redis.llen(key), 1)

    def test_actual_redis_clock_for_consume_and_status(self):
        result = check_limit(self.api_client, "alice")
        self.assertTrue(result["allowed"])
        status = check_limit(self.api_client, "alice", consume_request=False)
        self.assertEqual(status["used"], 1)
        self.assertEqual(status["allowed"], self.api_client.max_requests > 1)

    def test_concurrent_requests_do_not_exceed_configured_limit(self):
        max_requests = self.api_client.max_requests
        request_count = max_requests * 4
        with ThreadPoolExecutor(max_workers=min(request_count, 20)) as executor:
            results = list(executor.map(lambda _: self.check(), range(request_count)))
        self.assertEqual(sum(result["allowed"] for result in results), max_requests)
        status = self.check(consume_request=False)
        self.assertEqual(status["used"], max_requests)
