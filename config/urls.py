from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("founder.profile_urls")),
    path("", include("founder.community_urls")),
    path("", include("founder.urls")),
]
