"""Save only profile fields and keep file cleanup outside the database transaction."""
import logging

from django.db import transaction
from founder.models import User

logger = logging.getLogger(__name__)


def _delete_avatar(storage, name):
    try:
        storage.delete(name)
    except Exception:
        logger.warning('Could not remove an obsolete avatar file')


def save_user_profile(user_id, cleaned_data):
    from founder.profile_forms import PROFILE_FIELDS
    new_avatar = None
    storage = User._meta.get_field('avatar').storage
    try:
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=user_id)
            old_avatar = user.avatar.name
            for field in PROFILE_FIELDS:
                setattr(user, field, cleaned_data[field])
            upload = cleaned_data.get('avatar_upload')
            if upload:
                user.avatar.save('avatar.jpg', upload, save=False)
                new_avatar = user.avatar.name
            elif cleaned_data.get('remove_avatar'):
                user.avatar = ''
            # Exclude passwords, privileges, email and login from every profile update.
            user.save(update_fields=[*PROFILE_FIELDS, 'avatar'])
            if old_avatar and old_avatar != user.avatar.name:
                transaction.on_commit(lambda: _delete_avatar(storage, old_avatar))
        return user
    except Exception:
        if new_avatar:
            _delete_avatar(storage, new_avatar)
        raise
