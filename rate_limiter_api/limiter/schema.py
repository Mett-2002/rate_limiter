"""OpenAPI authentication, response schemas and examples."""
from drf_spectacular.extensions import OpenApiAuthenticationExtension, OpenApiSerializerExtension
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse
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
    allowed = serializers.BooleanField(
        help_text="For consume: this request was accepted. For status: capacity is available.",
    )
    limit = serializers.IntegerField(min_value=1)
    window_seconds = serializers.IntegerField(min_value=1)
    used = serializers.IntegerField(min_value=0)
    remaining = serializers.IntegerField(min_value=0)
    retry_after = serializers.IntegerField(
        min_value=0, help_text="Zero when allowed; otherwise seconds to wait, rounded up.",
    )
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


VALIDATION_ERROR_SCHEMA = {
    "type": "object",
    "properties": {
        "user_id": {"oneOf": [
            {"type": "string"},
            {"type": "array", "items": {"type": "string"}},
        ]},
        "non_field_errors": {"type": "array", "items": {"type": "string"}},
        "detail": {"type": "string"},
    },
    "additionalProperties": False,
}

ERROR_RESPONSES = {
    400: OpenApiResponse(
        VALIDATION_ERROR_SCHEMA,
        description="Invalid input. Field errors use user_id or non_field_errors; malformed JSON uses detail.",
        examples=[
            OpenApiExample("Missing user ID", value={"user_id": ["This field is required."]}, response_only=True),
            OpenApiExample("Blank user ID", value={"user_id": ["This field may not be blank."]}, response_only=True),
            OpenApiExample("Unknown field", value={"non_field_errors": ["Only user_id is accepted."]}, response_only=True),
        ],
    ),
    401: OpenApiResponse(
        ErrorResponseSerializer, description="Missing, invalid or inactive API client key.",
        examples=[
            OpenApiExample("Missing key", value={"detail": "Authentication credentials were not provided."}, response_only=True),
            OpenApiExample("Invalid key", value={"detail": "Invalid or inactive API key."}, response_only=True),
        ],
    ),
    503: OpenApiResponse(
        ErrorResponseSerializer, description="Redis is unavailable. The calling service must not perform the action.",
        examples=[OpenApiExample("Limiter unavailable", value={
            "detail": "Limiter unavailable; action must not proceed.",
        }, response_only=True)],
    ),
}

# Example values use the default policy. The timestamp is illustrative.
AVAILABLE_QUOTA = {
    "api_client": "support", "user_id": "alice", "allowed": True,
    "limit": 5, "window_seconds": 60, "used": 1, "remaining": 4,
    "retry_after": 0, "next_available_at": None,
}
FULL_QUOTA = {
    "api_client": "support", "user_id": "alice", "allowed": False,
    "limit": 5, "window_seconds": 60, "used": 5, "remaining": 0,
    "retry_after": 30, "next_available_at": 1800000060.0,
}

CONSUME_RESPONSES = {
    200: OpenApiResponse(
        LimitResponseSerializer, description="Request accepted; one slot consumed.",
        examples=[OpenApiExample("First accepted request", value=AVAILABLE_QUOTA, response_only=True)],
    ),
    429: OpenApiResponse(
        LimitResponseSerializer, description="Quota full; no slot consumed.",
        examples=[OpenApiExample("Request rejected", value=FULL_QUOTA, response_only=True)],
    ),
    **ERROR_RESPONSES,
}
STATUS_RESPONSES = {
    200: OpenApiResponse(
        LimitResponseSerializer, description="Current quota; no slot consumed. A full quota also returns HTTP 200.",
        examples=[
            OpenApiExample("Capacity available", value=AVAILABLE_QUOTA, response_only=True),
            OpenApiExample("Quota full", value=FULL_QUOTA, response_only=True),
        ],
    ),
    **ERROR_RESPONSES,
}
HEALTH_RESPONSES = {
    200: OpenApiResponse(
        HealthResponseSerializer, description="Redis is reachable.",
        examples=[OpenApiExample("Redis available", value={"redis": "ok"}, response_only=True)],
    ),
    503: OpenApiResponse(
        HealthResponseSerializer, description="Redis is unavailable.",
        examples=[OpenApiExample("Redis unavailable", value={"redis": "unavailable"}, response_only=True)],
    ),
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
AUTH_HEADER = OpenApiParameter(
    "WWW-Authenticate", str, OpenApiParameter.HEADER,
    description="Bearer authentication is required.", response=[401],
)
