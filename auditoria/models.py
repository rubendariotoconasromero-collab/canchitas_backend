from django.conf import settings
from django.db import models


class EventoAuditoria(models.Model):
    entidad = models.CharField(max_length=80)
    objeto_id = models.CharField(max_length=80)
    accion = models.CharField(max_length=40)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    actor_nombre = models.CharField(max_length=255, blank=True)
    sucursal = models.ForeignKey('users.Sucursal', null=True, on_delete=models.PROTECT)
    anterior = models.JSONField(default=dict)
    nuevo = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['sucursal', 'created_at']),
                   models.Index(fields=['entidad', 'objeto_id'])]
