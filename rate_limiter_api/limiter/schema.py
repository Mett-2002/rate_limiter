"""OpenAPI authentication and response definitions."""
from drf_spectacular.extensions import OpenApiAuthenticationExtension, OpenApiSerializerExtension
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse
from rest_framework import serializers


class APIClientKeyScheme(OpenApiAuthenticationExtension):
    target_class = "limiter.authentication.APIClientAuthentication"
    name = "APIClientKey"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "description": "Paste the API client key from Django Admin, without the Bearer prefix.",
        }


class LimitResponseSerializer(serializers.Serializer):
    api_client = serializers.CharField()
    user_id = serializers.CharField()
    allowed = serializers.BooleanField()
    limit = serializers.IntegerField(min_value=1)
    window_seconds = serializers.IntegerField(min_value=1)
    used = serializers.IntegerField(min_value=0)
    remaining = serializers.IntegerField(min_value=0)
    retry_after = serializers.IntegerField(min_value=0)
    next_available_at = serializers.FloatField(
        allow_null=True, help_text="Unix time in seconds when capacity next becomes available; null when not full.",
    )


class ErrorResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()


class HealthResponseSerializer(serializers.Serializer):
    redis = serializers.ChoiceField(choices=["ok", "unavailable"])


USER_ID = {
    "type": "string", "minLength": 1, "maxLength": 128,
    "pattern": r"\S", "example": "alice",
    "description": "End-user identifier supplied by the calling service; whitespace is trimmed.",
}


class UserRequestSchema(OpenApiSerializerExtension):
    """Document the serializer's strict rejection of unknown fields."""
    target_class = "limiter.serializers.UserRequestSerializer"

    def map_serializer(self, auto_schema, direction):
        schema = auto_schema._map_serializer(self.target, direction, bypass_extensions=True)
        schema["additionalProperties"] = False
        schema["properties"]["user_id"].update(USER_ID)
        return schema


ERROR_RESPONSES = {
    400: OpenApiResponse(OpenApiTypes.OBJECT, description="Invalid JSON or user_id. Validation errors identify the invalid field."),
    401: OpenApiResponse(ErrorResponseSerializer, description="Missing, invalid or inactive API client key."),
    503: OpenApiResponse(ErrorResponseSerializer, description="Redis is unavailable. The calling service must not perform the action."),
}
QUOTA_HEADERS = [
    OpenApiParameter("X-RateLimit-Limit", int, OpenApiParameter.HEADER,
                     description="Configured request limit.", response=[200, 429]),
    OpenApiParameter("X-RateLimit-Remaining", int, OpenApiParameter.HEADER,
                     description="Available slots after this operation.", response=[200, 429]),
]
RETRY_HEADER = OpenApiParameter(
    "Retry-After", int, OpenApiParameter.HEADER,
    description="Seconds to wait, rounded up.", response=[429],
)
