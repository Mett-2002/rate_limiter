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
    api_client = serializers.CharField(help_text="Name of the authenticated API client.")
    user_id = serializers.CharField(help_text="User identifier after trimming leading and trailing whitespace.")
    allowed = serializers.BooleanField(
        help_text="For consume: this request was accepted. For status: capacity is available.",
    )
    limit = serializers.IntegerField(min_value=1, help_text="Maximum accepted requests per rolling window.")
    window_seconds = serializers.IntegerField(min_value=1, help_text="Length of the rolling window in seconds.")
    used = serializers.IntegerField(min_value=0, help_text="Accepted requests still inside the window after this operation.")
    remaining = serializers.IntegerField(min_value=0, help_text="Available slots after this operation: max(0, limit - used).")
    retry_after = serializers.IntegerField(
        min_value=0, help_text="Seconds to wait after a denied decision, rounded up. Zero when allowed is true.",
    )
    next_available_at = serializers.FloatField(
        allow_null=True, help_text="Earliest time a full quota can regain a slot, in Unix seconds. Null when slots remain; also set after an accepted last-slot request.",
    )


class ErrorResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()


class HealthResponseSerializer(serializers.Serializer):
    redis = serializers.ChoiceField(choices=["ok", "unavailable"])


USER_ID = {
    "type": "string", "minLength": 1, "maxLength": 128,
    "pattern": r"\S", "example": "alice",
    "description": "User identifier supplied by the trusted calling service. Case-sensitive; leading and trailing whitespace is trimmed. Use 1 to 128 characters after trimming.",
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

EMPTY_QUOTA = {**AVAILABLE_QUOTA, "used": 0, "remaining": 5}
LAST_ACCEPTED_REQUEST = {**FULL_QUOTA, "allowed": True, "retry_after": 0}

CONSUME_RESPONSES = {
    200: OpenApiResponse(
        LimitResponseSerializer, description="Request accepted; one slot consumed. This includes the request that fills the window.",
        examples=[
            OpenApiExample("First accepted request", value=AVAILABLE_QUOTA, response_only=True),
            OpenApiExample(
                "Last slot accepted", value=LAST_ACCEPTED_REQUEST, response_only=True,
                description="The fifth request succeeds and fills the default window. A subsequent consume is rejected until capacity opens.",
            ),
        ],
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
            OpenApiExample(
                "Empty window", value=EMPTY_QUOTA, response_only=True,
                description="No accepted requests remain in the window. This also applies after all previous requests have expired.",
            ),
            OpenApiExample("Partially used window", value=AVAILABLE_QUOTA, response_only=True),
            OpenApiExample("Full window", value=FULL_QUOTA, response_only=True),
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
