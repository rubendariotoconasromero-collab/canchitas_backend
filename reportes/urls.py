from django.urls import path
from . import views

urlpatterns = [path('opciones/', views.options),
               path('<str:kind>/exportar/xlsx/', views.export_xlsx),
               path('<str:kind>/exportar/pdf/', views.export_pdf),
               path('<str:kind>/exportar/', views.export), path('<str:kind>/', views.report)]
