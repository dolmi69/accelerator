"""Delete a project, refund unused testing rewards and clean up private files."""
import logging
from functools import partial

from django.db import transaction

from founder.models import ChatAttachment, StartupProfile
from founder.services.coins import close_bounty
from founder.services.django_builder import delete_runtime

logger = logging.getLogger(__name__)


def _cleanup_files(startup_id, owner_id, attachments):
    try:
        delete_runtime(startup_id, owner_id)
    except Exception:
        logger.exception("Deleted project runtime cleanup failed: %s", startup_id)
    try:
        from founder.services.backend_stages import clear_checkpoints
        clear_checkpoints(startup_id)
    except Exception:
        logger.exception('Deleted project stage cleanup failed: %s', startup_id)
    for storage, name in attachments:
        try:
            storage.delete(name)
        except Exception:
            logger.exception("Deleted project attachment cleanup failed: %s", startup_id)


def delete_project(startup):
    """Database deletion and refunds succeed together; disk cleanup follows commit.

    Account balances, coin history and paid AI usage remain in their ledgers.
    Files are never removed before the database transaction has committed.
    """
    with transaction.atomic():
        locked = StartupProfile.objects.select_for_update().get(pk=startup.pk, owner_id=startup.owner_id)
        for bounty in locked.test_bounties.filter(closed_at__isnull=True).select_related('funder').order_by('funder_id', 'pk'):
            close_bounty(bounty.funder, locked, bounty.pk)
        attachments = [(attachment.file.storage, attachment.file.name)
                       for attachment in ChatAttachment.objects.filter(message__session__startup=locked)
                       if attachment.file.name]
        startup_id, owner_id = locked.pk, locked.owner_id
        locked.delete()
        transaction.on_commit(partial(_cleanup_files, startup_id, owner_id, attachments))
