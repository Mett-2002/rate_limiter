import hashlib
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from .models import APIClient


class APIClientAuthentication(BaseAuthentication):
    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != b"bearer":
            return None
        if len(parts) != 2 or len(parts[1]) > 128:
            raise AuthenticationFailed("Use Authorization: Bearer YOUR_API_KEY")
        key_hash = hashlib.sha256(parts[1]).hexdigest()
        api_client = APIClient.objects.filter(api_key_hash=key_hash, is_active=True).first()
        if api_client is None:
            raise AuthenticationFailed("Invalid or inactive API key.")
        return api_client, None

    def authenticate_header(self, request):
        return "Bearer"
