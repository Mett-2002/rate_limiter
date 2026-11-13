"""Rate-limit API views."""
from django.conf import settings
from redis.exceptions import RedisError
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .authentication import APIClientAuthentication
from .serializers import UserRequestSerializer
from .services import check_limit, redis_backend
from .schema import (
    AUTH_HEADER, CONSUME_RESPONSES, HEALTH_RESPONSES, QUOTA_HEADERS,
    RETRY_HEADER, STATUS_RESPONSES, USER_ID,
)


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Check Redis health", tags=["Health"], auth=[],
        responses=HEALTH_RESPONSES,
    )
    def get(self, request):
        try:
            client, _ = redis_backend(settings.REDIS_URL)
            client.ping()
        except RedisError:
            return Response({"redis": "unavailable"}, status=503)
        return Response({"redis": "ok"})


class AuthenticatedClientView(APIView):
    authentication_classes = [APIClientAuthentication]
    permission_classes = [IsAuthenticated]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def limit_response(self, request, data, consume_request):
        serializer = UserRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        user_id = serializer.validated_data["user_id"]
        try:
            decision = check_limit(request.user, user_id, consume_request=consume_request)
        except RedisError:
            return Response({"detail": "Limiter unavailable; action must not proceed."}, status=503)
        response = Response({"api_client": request.user.name, "user_id": user_id, **decision},
                            status=429 if consume_request and not decision["allowed"] else 200)
        response["X-RateLimit-Limit"] = str(decision["limit"])
        response["X-RateLimit-Remaining"] = str(decision["remaining"])
        if response.status_code == 429:
            response["Retry-After"] = str(decision["retry_after"])
        return response


class ConsumeView(AuthenticatedClientView):
    @extend_schema(
        summary="Consume one request", tags=["Limits"],
        description=(
            "Atomically checks quota and records one accepted request. Send only `user_id` in the JSON body.\n\n"
            "- **200:** this attempt was accepted, including the last available slot (`remaining: 0`).\n"
            "- **429:** the window was already full; this attempt consumes no slot and does not extend expiry.\n\n"
            "Every POST is a new attempt; accepted requests are not deduplicated. "
            "The caller should perform its action only after an allowed response."
        ),
        request={"application/json": UserRequestSerializer},
        parameters=[*QUOTA_HEADERS, RETRY_HEADER, AUTH_HEADER],
        responses=CONSUME_RESPONSES,
    )
    def post(self, request):
        return self.limit_response(request, request.data, consume_request=True)


class StatusView(AuthenticatedClientView):
    @extend_schema(
        summary="Check status", tags=["Limits"],
        description=(
            "Reads the current quota and removes expired history without consuming or reserving a slot.\n\n"
            "**200** means the status lookup succeeded. `allowed: false` means a consume would currently be rejected. "
            "The examples cover empty, partially used and full windows.\n\n"
            "Use **Consume** before performing an action; a status check does not guarantee that capacity will still be available."
        ),
        request=None,
        parameters=[
            OpenApiParameter("user_id", USER_ID, required=True, description=USER_ID["description"]),
            *QUOTA_HEADERS, AUTH_HEADER,
        ],
        responses=STATUS_RESPONSES,
    )
    def get(self, request):
        return self.limit_response(request, request.query_params, consume_request=False)
