"""Provision API clients and issue keys without a Python shell."""
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from .models import APIClient


@admin.register(APIClient)
class APIClientAdmin(admin.ModelAdmin):
    list_display = ("name", "max_requests", "window_seconds", "is_active")
    readonly_fields = ("key_management",)
    fields = ("name", "max_requests", "window_seconds", "is_active", "key_management")

    @admin.display(description="API key")
    def key_management(self, obj):
        if not obj or not obj.pk:
            return "Save the new API client to receive its API key."
        url = reverse("admin:limiter_apiclient_key", args=[obj.pk])
        return format_html('<a href="{}">Generate / rotate API key</a>', url)

    def get_urls(self):
        return [path("<int:api_client_id>/api-key/", self.admin_site.admin_view(self.key_view),
                     name="limiter_apiclient_key")] + super().get_urls()

    def key_response(self, request, obj, token=None):
        response = TemplateResponse(request, "admin/limiter/api_client_key.html", {
            **self.admin_site.each_context(request),
            "title": "API client API key", "api_client": obj, "token": token,
            "change_url": reverse("admin:limiter_apiclient_change", args=[obj.pk]),
        })
        response["Cache-Control"] = "no-store"
        # Preserve Origin on Chrome form POSTs; cross-origin referrers stay hidden.
        response["Referrer-Policy"] = "same-origin"
        return response

    def key_view(self, request, api_client_id):
        obj = get_object_or_404(APIClient, pk=api_client_id)
        if not self.has_change_permission(request, obj):
            raise PermissionDenied
        token = obj.generate_api_key() if request.method == "POST" else None
        return self.key_response(request, obj, token)

    def response_add(self, request, obj, post_url_continue=None):
        # Show the raw key once, never in a cookie-backed Django message.
        return self.key_response(request, obj, obj.generate_api_key())
