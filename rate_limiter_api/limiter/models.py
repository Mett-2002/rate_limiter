"""SQL stores configuration; Redis stores expiring request history."""
import hashlib
import secrets
import uuid
from django.core.validators import MinValueValidator
from django.db import models


class APIClient(models.Model):
    name = models.CharField(max_length=100, unique=True)
    max_requests = models.PositiveIntegerField(default=5, validators=[MinValueValidator(1)])
    window_seconds = models.PositiveIntegerField(default=60, validators=[MinValueValidator(1)])
    is_active = models.BooleanField(default=True)
    api_key_hash = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    policy_version = models.UUIDField(default=uuid.uuid4, editable=False)

    class Meta:
        verbose_name = "API client"
        verbose_name_plural = "API clients"
        constraints = [
            models.CheckConstraint(condition=models.Q(max_requests__gte=1), name="api_client_limit_positive"),
            models.CheckConstraint(condition=models.Q(window_seconds__gte=1), name="api_client_window_positive"),
        ]

    def __str__(self):
        return self.name

    @property
    def is_authenticated(self):
        # DRF checks this after API key authentication.
        return True

    def save(self, *args, **kwargs):
        """Changing a policy starts a fresh quota; rotating a key does not."""
        fields = kwargs.get("update_fields")
        config_fields = {"max_requests", "window_seconds"}
        if self.pk and (fields is None or config_fields.intersection(fields)):
            previous = type(self).objects.filter(pk=self.pk).values(*config_fields).first()
            if previous and any(previous[name] != getattr(self, name) for name in config_fields):
                self.policy_version = uuid.uuid4()
                if fields is not None:
                    kwargs["update_fields"] = set(fields) | {"policy_version"}
        super().save(*args, **kwargs)

    def generate_api_key(self):
        """Return the secret once and store only its fingerprint."""
        api_key = secrets.token_urlsafe(32)
        self.api_key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        self.save(update_fields=["api_key_hash"])
        return api_key
