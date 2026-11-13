import json
from hashlib import sha256
from pathlib import Path

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.cache import never_cache

from . import client


def _identity(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("Enter a user ID between 1 and 128 characters.")
    return value.strip()


@never_cache
@ensure_csrf_cookie
@require_GET
def home(request):
    # A changed asset gets a new URL, so browsers cannot mix new HTML with old JS/CSS.
    assets = Path(__file__).resolve().parent / "static" / "support"
    version = sha256(b"".join(
        (assets / name).read_bytes() for name in ("demo.css", "timeline.js", "demo.js")
    )).hexdigest()[:12]
    return render(request, "support/home.html", {"asset_version": version})


@never_cache
@require_POST
def submit_request(request):
    """Send one quota-consuming request to the limiter API."""
    try:
        if request.content_type != "application/json":
            raise ValueError("Send JSON with user_id.")
        data = json.loads(request.body)
        if not isinstance(data, dict) or set(data) - {"user_id"}:
            raise ValueError("Only user_id is accepted.")
        user_id = _identity(data.get("user_id"))
    except ValueError as error:
        return JsonResponse({"detail": str(error)}, status=400)
    try:
        status, decision = client.consume(user_id)
    except client.LimiterUnavailable as error:
        return JsonResponse({"detail": str(error)}, status=503)
    denied = status == 429 or not decision["allowed"]
    payload = {"user_id": user_id, "quota": decision}
    if denied:
        payload["detail"] = f"HTTP 429 · Rate limited for {user_id}. Try again as capacity opens."
    response = JsonResponse(payload, status=429 if denied else 200)
    if denied:
        response["Retry-After"] = str(decision["retry_after"])
    return response


@require_GET
def quota(request):
    try:
        user_id = _identity(request.GET.get("user_id", "alice"))
    except ValueError as error:
        return JsonResponse({"detail": str(error)}, status=400)
    try:
        _, status = client.get_status(user_id)
    except client.LimiterUnavailable as error:
        return JsonResponse({"detail": str(error)}, status=503)
    response = JsonResponse(status)
    response["Cache-Control"] = "no-store"
    return response
