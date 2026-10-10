"""Durable, participant-scoped personal messages. Socket events only announce saved rows."""
from datetime import timedelta
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from founder.models import DirectAttachment, DirectConversation, DirectMessage, User, UserBlock
from founder.services.direct_attachments import parse_ids, serialize_attachment


def participant_filter(user_id):
    return Q(user_low_id=user_id) | Q(user_high_id=user_id)


def owned_conversation(user_id, conversation_id):
    return DirectConversation.objects.select_related('user_low', 'user_high', 'source_card').get(
        participant_filter(user_id), pk=conversation_id,
    )


def blocked_pair(a, b):
    return UserBlock.objects.filter(Q(user_id=a, blocked_id=b) | Q(user_id=b, blocked_id=a)).exists()


def open_conversation(user, card):
    return start_direct_conversation(user, card.startup.owner, source_card=card)


def start_direct_conversation(user, recipient, *, source_card=None):
    other_id = recipient.pk
    if not recipient.is_active or other_id == user.pk or blocked_pair(user.pk, other_id):
        raise PermissionDenied('Нельзя начать этот диалог.')
    low, high = sorted((user.pk, other_id))
    thread, _ = DirectConversation.objects.get_or_create(user_low_id=low, user_high_id=high,
                                                         defaults={'source_card': source_card})
    return thread


def serialize_message(message):
    return {'id': message.pk, 'conversation': str(message.conversation_id),
            'sender_id': message.sender_id, 'client_id': str(message.client_id),
            'content': message.content, 'created_at': message.created_at.isoformat(),
            'attachments': [serialize_attachment(item) for item in message.attachments.all()]}


def unread_count(user_id):
    return DirectMessage.objects.filter(
        Q(conversation__user_low_id=user_id, id__gt=F('conversation__low_read_id'))
        | Q(conversation__user_high_id=user_id, id__gt=F('conversation__high_read_id')),
    ).exclude(sender_id=user_id).count()


JUMP_PAGE_SIZE = 500


def history(user_id, conversation_id, *, after=None, before=None, since=None):
    """after: newer rows; before: an older page; before+since: the whole gap down to a
    search result (capped, adjacent to `before`, so the client can repeat without holes)."""
    thread = owned_conversation(user_id, conversation_id)
    query = thread.direct_messages.prefetch_related('attachments')
    if after is not None:
        rows = list(query.filter(id__gt=after)[:101])
        has_more = len(rows) > 100
        rows = rows[:100]
    else:
        if before is not None:
            query = query.filter(id__lt=before)
        limit = 50 if since is None else JUMP_PAGE_SIZE
        window = query if since is None else query.filter(id__gte=since)
        rows = list(window.order_by('-id')[:limit + 1])
        has_more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
        if since is not None and not has_more:
            has_more = query.filter(id__lt=rows[0].pk if rows else since).exists()
    return {'type': 'history', 'conversation': str(thread.pk),
            'messages': [serialize_message(row) for row in rows],
            'has_more': has_more, 'direction': 'after' if after is not None else 'before',
            'peer_read_id': thread.read_id_for(thread.other_user(user_id).pk),
            'blocked': blocked_pair(thread.user_low_id, thread.user_high_id)}


@transaction.atomic
def send_message(user_id, conversation_id, client_id, content, attachment_ids=None):
    content = '' if content is None else content
    if not isinstance(content, str) or len(content) > 4000:
        raise ValidationError('Сообщение должно содержать до 4000 символов.')
    attachment_ids = parse_ids(attachment_ids)
    if not content.strip() and not attachment_ids:
        raise ValidationError('Напишите сообщение или прикрепите файл.')
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
        sent_files = set(previous.attachments.values_list('pk', flat=True))
        if (previous.conversation_id != thread.pk or previous.content != content.strip()
                or sent_files != set(attachment_ids)):
            raise ValidationError('Идентификатор сообщения уже использован.')
        return previous, False
    if DirectMessage.objects.filter(sender_id=user_id, created_at__gte=timezone.now()-timedelta(minutes=1)).count() >= 30:
        raise ValidationError('Слишком много сообщений. Подождите минуту.')
    files = list(DirectAttachment.objects.select_for_update().filter(
        pk__in=attachment_ids, uploader_id=user_id, conversation=thread, message__isnull=True))
    if len(files) != len(attachment_ids):
        raise ValidationError('Файл не найден или уже отправлен. Прикрепите его заново.')
    message = DirectMessage.objects.create(conversation=thread, sender_id=user_id,
                                          client_id=client_id, content=content.strip())
    if files:
        DirectAttachment.objects.filter(pk__in=attachment_ids).update(message=message)
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
