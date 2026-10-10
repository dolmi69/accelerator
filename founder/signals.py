"""Remove private files from disk together with their database rows."""
from django.db.models.signals import post_delete
from django.dispatch import receiver

from founder.models import DirectAttachment


@receiver(post_delete, sender=DirectAttachment)
def delete_direct_attachment_files(sender, instance, **kwargs):
    from founder.services.direct_attachments import delete_files
    delete_files(instance)
from django.db.models.signals import post_save
from django.dispatch import receiver

from founder.models import User


@receiver(post_save, sender=User, dispatch_uid="founder_welcome_coins")
def welcome_coins(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        from founder.services.coins import grant_welcome
        grant_welcome(instance)
