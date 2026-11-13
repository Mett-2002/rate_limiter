from rest_framework import serializers


class UserRequestSerializer(serializers.Serializer):
    user_id = serializers.CharField(max_length=128, allow_blank=False, trim_whitespace=True)

    def to_internal_value(self, data):
        # Reject misspelled fields and client-supplied limits or clocks.
        if not hasattr(data, "keys"):
            raise serializers.ValidationError({"non_field_errors": ["Expected a JSON object."]})
        if set(data.keys()) - {"user_id"}:
            raise serializers.ValidationError({"non_field_errors": ["Only user_id is accepted."]})
        if "user_id" in data and not isinstance(data["user_id"], str):
            raise serializers.ValidationError({"user_id": "Use a non-empty string."})
        return super().to_internal_value(data)
