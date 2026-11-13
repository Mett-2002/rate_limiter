"""HTTP contract; the atomic algorithm lives in services.py and Lua."""
from django.conf import settings
from redis.exceptions import RedisError
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .authentication import APIClientAuthentication
from .serializers import UserRequestSerializer
from .services import check_limit, redis_backend
from .schema import (
    ERROR_RESPONSES, QUOTA_HEADERS, RETRY_HEADER, USER_ID,
    HealthResponseSerializer, LimitResponseSerializer,
)


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Check Redis health", tags=["Health"], auth=[],
        responses={200: HealthResponseSerializer, 503: HealthResponseSerializer},
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
    """POST one new attempt. Each accepted call consumes one slot."""
    @extend_schema(
        summary="Consume one request", tags=["Limits"],
        description="Each accepted call consumes one slot. Rejected calls consume none. Only user_id is accepted.",
        request={"application/json": UserRequestSerializer},
        parameters=[*QUOTA_HEADERS, RETRY_HEADER],
        responses={
            200: OpenApiResponse(LimitResponseSerializer, description="Request accepted; one slot consumed."),
            429: OpenApiResponse(LimitResponseSerializer, description="Quota full; no slot consumed."),
            **ERROR_RESPONSES,
        },
    )
    def post(self, request):
        return self.limit_response(request, request.data, consume_request=True)


class StatusView(AuthenticatedClientView):
    """GET prunes expired history without consuming a slot."""
    @extend_schema(
        summary="Check status", tags=["Limits"],
        description="Removes expired history without consuming capacity. Returns 200 even when allowed is false.",
        request=None,
        parameters=[OpenApiParameter("user_id", USER_ID, required=True), *QUOTA_HEADERS],
        responses={200: LimitResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request):
        return self.limit_response(request, request.query_params, consume_request=False)
