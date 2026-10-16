from uuid import uuid4
from rest_framework import serializers


def validate_image(value):
    if value.size > 5 * 1024 * 1024:
        raise serializers.ValidationError('La imagen no puede superar 5 MB.')
    if value.image.format not in ('JPEG', 'PNG', 'WEBP'):
        raise serializers.ValidationError('Usa JPEG, PNG o WebP.')
    if value.image.width * value.image.height > 25_000_000:
        raise serializers.ValidationError('La imagen supera 25 megapixeles.')
    suffix = {'JPEG':'.jpg','PNG':'.png','WEBP':'.webp'}[value.image.format]
    value.name = uuid4().hex + suffix
    return value
