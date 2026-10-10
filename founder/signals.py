"""Remove private files from disk together with their database rows."""
from django.db.models.signals import post_delete
from django.dispatch import receiver

from founder.models import DirectAttachment


@receiver(post_delete, sender=DirectAttachment)
def delete_direct_attachment_files(sender, instance, **kwargs):
    from founder.services.direct_attachments import delete_files
    delete_files(instance)
