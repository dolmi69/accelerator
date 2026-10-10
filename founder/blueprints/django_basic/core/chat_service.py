"""One persistent, idempotent write path for sockets and prototype actions."""
from django.db import transaction
from django.urls import reverse
from .models import Conversation, Message
from .security import allowed


@transaction.atomic
def save_message(sender_id, conversation_id, content, nonce):
    conversation = Conversation.objects.get(pk=conversation_id)
    if sender_id not in (conversation.first_id, conversation.second_id):
        return None, False
    old = Message.objects.filter(sender_id=sender_id, nonce=nonce).select_related('sender').first()
    if old:
        row, created = old, False
    else:
        if not allowed('message:' + str(sender_id), 30, 60):
            return None, False
        row, created = Message.objects.get_or_create(sender_id=sender_id, nonce=nonce,
            defaults={'conversation_id': conversation_id, 'content': content})
    if row.conversation_id != conversation_id or row.content != content:
        return None, False
    if created:
        from .services import notify
        recipient = conversation.second_id if conversation.first_id == sender_id else conversation.first_id
        notify(recipient, 'Новое личное сообщение', reverse('chat', args=[conversation_id]), key=f'message:{row.pk}')
    return {'id': row.pk, 'sender_id': row.sender_id, 'sender': row.sender.username,
            'content': row.content, 'created_at': row.created_at.isoformat(), 'nonce': str(row.nonce)}, created


def broadcast_message(conversation_id, message):
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    async_to_sync(get_channel_layer().group_send)('chat_' + conversation_id.hex,
        {'type': 'chat.message', 'message': message})
