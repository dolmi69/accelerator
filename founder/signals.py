"""Стартовые монеты для каждого нового аккаунта, включая вход через Telegram."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from founder.models import User


@receiver(post_save, sender=User, dispatch_uid="founder_welcome_coins")
def welcome_coins(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        from founder.services.coins import grant_welcome
        grant_welcome(instance)
