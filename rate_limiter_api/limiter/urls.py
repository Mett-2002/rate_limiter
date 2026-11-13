from django.urls import path
from .views import ConsumeView, HealthView, StatusView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("limits/consume/", ConsumeView.as_view(), name="consume"),
    path("limits/status/", StatusView.as_view(), name="limit-status"),
]
