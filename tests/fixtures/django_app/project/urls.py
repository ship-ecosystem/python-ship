from django.urls import path

from .views import dependencies, index

urlpatterns = [path("", index), path("dependencies/", dependencies)]
