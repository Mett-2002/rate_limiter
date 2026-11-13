"""HTTP contract; the atomic algorithm lives in services.py and Lua."""
from django.conf import settings
from django.urls import reverse
from redis.exceptions import RedisError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .authentication import APIClientAuthentication
from .serializers import UserRequestSerializer
from .services import check_limit, redis_backend


class IndexView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({
            "name": "Internal rate limiter",
            "algorithm": "Exact sliding-window log (Redis FIFO + atomic Lua)",
            "semantics": "(now - window, now]; exact-boundary timestamps expire",
            "authentication": "Authorization: Bearer YOUR_API_CLIENT_KEY",
            "body_example": {"user_id": "alice"},
            "consume": reverse("consume"),
            "status": reverse("limit-status") + "?user_id=alice",
            "health": reverse("health"),
            "admin": reverse("admin:index"),
        })


class HealthView(IndexView):
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
    def post(self, request):
        return self.limit_response(request, request.data, consume_request=True)


class StatusView(AuthenticatedClientView):
    """GET prunes expired history without consuming a slot."""
    def get(self, request):
        return self.limit_response(request, request.query_params, consume_request=False)
