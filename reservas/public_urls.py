from django.urls import path
from .public_api import PublicBusiness, PublicCourts, PublicAvailability, PublicQuote, PublicRequests, PublicTracking
from .public_history import HistoryContext, PublicHistory, HistoryDetail, HistoryImport

urlpatterns = [
    path('historial/contexto/', HistoryContext.as_view()),
    path('historial/importar/', HistoryImport.as_view()),
    path('historial/<str:code>/', HistoryDetail.as_view()),
    path('historial/', PublicHistory.as_view()),
    path('negocio/', PublicBusiness.as_view()),
    path('canchas/', PublicCourts.as_view()),
    path('canchas/<int:pk>/', PublicCourts.as_view()),
    path('canchas/<int:pk>/disponibilidad/', PublicAvailability.as_view()),
    path('cotizar/', PublicQuote.as_view()),
    path('solicitudes/', PublicRequests.as_view()),
    path('seguimiento/', PublicTracking.as_view()),
]
