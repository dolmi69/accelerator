"""Durable, participant-scoped personal messages. Socket events only announce saved rows."""
from datetime import timedelta
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from founder.models import DirectConversation, DirectMessage, User, UserBlock


def participant_filter(user_id):
    return Q(user_low_id=user_id) | Q(user_high_id=user_id)


def owned_conversation(user_id, conversation_id):
    return DirectConversation.objects.select_related('user_low', 'user_high', 'source_card').get(
        participant_filter(user_id), pk=conversation_id,
    )


def blocked_pair(a, b):
    return UserBlock.objects.filter(Q(user_id=a, blocked_id=b) | Q(user_id=b, blocked_id=a)).exists()


def open_conversation(user, card):
    other_id = card.startup.owner_id
    if other_id == user.pk or blocked_pair(user.pk, other_id):
        raise PermissionDenied('Нельзя начать этот диалог.')
    low, high = sorted((user.pk, other_id))
    thread, _ = DirectConversation.objects.get_or_create(user_low_id=low, user_high_id=high,
                                                         defaults={'source_card': card})
    return thread


def serialize_message(message):
    return {'id': message.pk, 'conversation': str(message.conversation_id),
            'sender_id': message.sender_id, 'client_id': str(message.client_id),
            'content': message.content, 'created_at': message.created_at.isoformat()}


def unread_count(user_id):
    return DirectMessage.objects.filter(
        Q(conversation__user_low_id=user_id, id__gt=F('conversation__low_read_id'))
        | Q(conversation__user_high_id=user_id, id__gt=F('conversation__high_read_id')),
    ).exclude(sender_id=user_id).count()


def history(user_id, conversation_id, *, after=None, before=None):
    thread = owned_conversation(user_id, conversation_id)
    query = thread.direct_messages.all()
    if after is not None:
        rows = list(query.filter(id__gt=after)[:101])
        has_more = len(rows) > 100
        rows = rows[:100]
    else:
        if before is not None:
            query = query.filter(id__lt=before)
        rows = list(query.order_by('-id')[:51])
        has_more = len(rows) > 50
        rows = list(reversed(rows[:50]))
    return {'type': 'history', 'conversation': str(thread.pk),
            'messages': [serialize_message(row) for row in rows],
            'has_more': has_more, 'direction': 'after' if after is not None else 'before',
            'peer_read_id': thread.read_id_for(thread.other_user(user_id).pk),
            'blocked': blocked_pair(thread.user_low_id, thread.user_high_id)}


@transaction.atomic
def send_message(user_id, conversation_id, client_id, content):
    if not isinstance(content, str) or not content.strip() or len(content) > 4000:
        raise ValidationError('Сообщение должно содержать от 1 до 4000 символов.')
    try:
        client_id = UUID(str(client_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError('Не удалось распознать сообщение. Обновите страницу.') from exc
    # Serialize concurrent sends from the same user, including multiple tabs/workers.
    User.objects.select_for_update().get(pk=user_id, is_active=True)
    thread = owned_conversation(user_id, conversation_id)
    if blocked_pair(thread.user_low_id, thread.user_high_id) or not thread.other_user(user_id).is_active:
        raise PermissionDenied('Отправка сообщений в этом диалоге недоступна.')
    previous = DirectMessage.objects.filter(sender_id=user_id, client_id=client_id).first()
    if previous:
        if previous.conversation_id != thread.pk or previous.content != content.strip():
            raise ValidationError('Идентификатор сообщения уже использован.')
        return previous, False
    if DirectMessage.objects.filter(sender_id=user_id, created_at__gte=timezone.now()-timedelta(minutes=1)).count() >= 30:
        raise ValidationError('Слишком много сообщений. Подождите минуту.')
    message = DirectMessage.objects.create(conversation=thread, sender_id=user_id,
                                          client_id=client_id, content=content.strip())
    DirectConversation.objects.filter(pk=thread.pk).update(updated_at=message.created_at)
    return message, True


def mark_read(user_id, conversation_id, message_id):
    thread = owned_conversation(user_id, conversation_id)
    # Accept only a real message from this thread, never an arbitrary future ID.
    if not thread.direct_messages.filter(pk=message_id).exists():
        raise ValidationError('Сообщение не найдено.')
    field = 'low_read_id' if thread.user_low_id == user_id else 'high_read_id'
    DirectConversation.objects.filter(pk=thread.pk, **{f'{field}__lt': message_id}).update(**{field: message_id})
    return thread
