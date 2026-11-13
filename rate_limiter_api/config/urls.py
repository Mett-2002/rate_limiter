from django.contrib import admin
from django.urls import include, path
from limiter.views import IndexView

urlpatterns = [
    path("", IndexView.as_view(), name="api-index"),
    path("admin/", admin.site.urls),
    path("api/", include("limiter.urls")),
]
