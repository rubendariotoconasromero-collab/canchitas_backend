from django.urls import path
from .notices import notices

urlpatterns = [path('', notices)]
