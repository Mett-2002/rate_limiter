from django.contrib import admin
from django.urls import include, path
from limiter.views import IndexView, PlaygroundView

urlpatterns = [
    path("", IndexView.as_view(), name="api-index"),
    path("playground/", PlaygroundView.as_view(), name="playground"),
    path("admin/", admin.site.urls),
    path("api/", include("limiter.urls")),
]
