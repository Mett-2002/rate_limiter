"""A small HTTP client; this file never imports the limiter's Python code."""
import json
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


class LimiterUnavailable(Exception):
    pass


def get_api_key():
    if settings.DEMO_API_KEY:
        return settings.DEMO_API_KEY
    try:
        data = json.loads(settings.DEMO_CREDENTIALS_FILE.read_text(encoding="utf-8"))
        token = data["api_key"]
        if not isinstance(token, str) or not token:
            raise ValueError("Missing key")
        return token
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise LimiterUnavailable("Demo credentials are not configured.") from error


def _request(path, payload=None):
    headers = {
        "Authorization": f"Bearer {get_api_key()}",
        "Accept": "application/json",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(settings.LIMITER_BASE_URL + path, data=data, headers=headers)
    try:
        with urlopen(request, timeout=2) as response:
            result = json.loads(response.read().decode("utf-8"))
            status = response.status
    except HTTPError as error:
        if error.code != 429:
            raise LimiterUnavailable("The limiter is unavailable or the demo key was rejected.") from error
        try:
            result = json.loads(error.read().decode("utf-8"))
        except ValueError as decode_error:
            raise LimiterUnavailable("The limiter returned an invalid response.") from decode_error
        status = 429
    except (OSError, ValueError) as error:
        raise LimiterUnavailable("Cannot reach the rate-limiter API. Start the API and Redis first.") from error
    required = {"allowed", "limit", "window_seconds", "used", "remaining", "retry_after", "next_available_at"}
    if not isinstance(result, dict) or not required.issubset(result) or not isinstance(result["allowed"], bool):
        raise LimiterUnavailable("The limiter returned an invalid response.")
    if status not in (200, 429):
        raise LimiterUnavailable("The limiter returned an unexpected response.")
    return status, result


def consume(user_id):
    return _request("/api/limits/consume/", {"user_id": user_id})


def get_status(user_id):
    return _request("/api/limits/status/?" + urlencode({"user_id": user_id}))
