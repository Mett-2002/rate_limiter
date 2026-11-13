"""Real-Redis tests: fail clearly if the dependency is unavailable.

Only keys under a fresh random test prefix are deleted. Never flush Redis.
The internal test clock lets boundary tests run immediately without sleeping.
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from .services import _history_key, check_limit, redis_backend


class SlidingWindowTests(SimpleTestCase):
    NOW = 1_800_000_000_000_000

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
        self.api_client = SimpleNamespace(
            pk=101,
            policy_version=uuid.uuid4(),
            max_requests=5,
            window_seconds=60,
        )

    def _delete_own_keys(self):
        keys = list(self.redis.scan_iter(match=f"{self.prefix}:*"))
        if keys:
            self.redis.delete(*keys)

    def check(self, seconds=0, *, user="alice", consume_request=True):
        return check_limit(
            self.api_client,
            user,
            consume_request=consume_request,
            now_us=self.NOW + round(seconds * 1_000_000),
        )

    def fill(self):
        for _ in range(5):
            self.assertTrue(self.check()["allowed"])

    def test_first_five_allowed_and_sixth_rejected(self):
        for expected_used in range(1, 6):
            result = self.check()
            self.assertTrue(result["allowed"])
            self.assertEqual(result["used"], expected_used)
            self.assertEqual(result["remaining"], 5 - expected_used)

        rejected = self.check()
        self.assertFalse(rejected["allowed"])
        self.assertEqual(rejected["used"], 5)
        self.assertEqual(rejected["retry_after"], 60)

    def test_one_microsecond_before_boundary_is_still_full(self):
        self.fill()
        result = self.check(59.999999)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["retry_after"], 1)

    def test_exact_sixty_seconds_releases_capacity(self):
        self.fill()
        result = self.check(60)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["used"], 1)

    def test_after_sixty_seconds_releases_capacity(self):
        self.fill()
        self.assertEqual(self.check(60.000001)["remaining"], 4)

    def test_staggered_requests_expire_individually(self):
        for seconds in (0, 10, 20, 30, 40):
            self.assertTrue(self.check(seconds)["allowed"])
        self.assertTrue(self.check(60)["allowed"])
        self.assertFalse(self.check(69.999999)["allowed"])
        self.assertTrue(self.check(70)["allowed"])

    def test_users_are_independent(self):
        self.fill()
        result = self.check(user="bob")
        self.assertTrue(result["allowed"])
        self.assertEqual(result["used"], 1)
        self.assertFalse(self.check()["allowed"])

    def test_api_clients_are_independent(self):
        self.fill()
        self.api_client.pk = 102
        self.assertEqual(self.check()["used"], 1)

    def test_policy_version_starts_a_new_bucket(self):
        self.fill()
        self.api_client.policy_version = uuid.uuid4()
        self.assertEqual(self.check()["used"], 1)


    def test_status_does_not_consume_and_prunes_on_boundary(self):
        self.assertEqual(self.check()["used"], 1)
        status = self.check(30, consume_request=False)
        self.assertEqual(status["used"], 1)
        self.assertEqual(status["remaining"], 4)
        status = self.check(60, consume_request=False)
        self.assertEqual(status["used"], 0)
        self.assertEqual(status["remaining"], 5)

    def test_every_request_prunes_even_when_bucket_is_not_full(self):
        self.check()
        self.assertEqual(self.check(60)["used"], 1)
        key = _history_key(self.api_client, "alice")
        self.assertEqual(self.redis.llen(key), 1)

    def test_rejection_does_not_append_or_extend_expiration(self):
        self.fill()
        key = _history_key(self.api_client, "alice")
        # A deliberate shorter TTL makes an accidental 60s refresh observable.
        self.redis.pexpire(key, 30_000)
        ttl_before = self.redis.pttl(key)
        self.assertFalse(self.check(30)["allowed"])
        ttl_after = self.redis.pttl(key)
        self.assertEqual(self.redis.llen(key), 5)
        self.assertLessEqual(ttl_after, ttl_before)
        self.assertGreater(ttl_after, 0)

    def test_status_does_not_extend_expiration(self):
        self.check()
        key = _history_key(self.api_client, "alice")
        self.redis.pexpire(key, 30_000)
        ttl_before = self.redis.pttl(key)
        self.check(30, consume_request=False)
        self.assertLessEqual(self.redis.pttl(key), ttl_before)

    def test_actual_redis_clock_for_consume_and_status(self):
        result = check_limit(self.api_client, "alice")
        self.assertTrue(result["allowed"])
        status = check_limit(self.api_client, "alice", consume_request=False)
        self.assertEqual(status["used"], 1)
        self.assertTrue(status["allowed"])

    def test_twenty_concurrent_calls_accept_exactly_five(self):
        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(
                executor.map(
                    lambda _: check_limit(self.api_client, "alice"), range(20)
                )
            )
        self.assertEqual(sum(result["allowed"] for result in results), 5)
        self.assertEqual(check_limit(self.api_client, "alice", consume_request=False)["used"], 5)

    def test_clock_moving_backward_keeps_fifo_order(self):
        self.check(10)
        self.check(9)
        key = _history_key(self.api_client, "alice")
        timestamps = [int(value) for value in self.redis.lrange(key, 0, -1)]
        self.assertEqual(timestamps, sorted(timestamps))
