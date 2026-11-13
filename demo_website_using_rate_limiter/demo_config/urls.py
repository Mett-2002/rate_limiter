from django.urls import path
from support import views

urlpatterns = [
    path("", views.home, name="demo-home"),
    path("requests/", views.submit_request, name="demo-request"),
    path("quota/", views.quota, name="demo-quota"),
]
