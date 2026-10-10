"""Authenticated WebSocket endpoint for inbox notifications and personal conversations."""
import time
from importlib import import_module
from types import SimpleNamespace

from asgiref.sync import async_to_sync
from channels.generic.websocket import JsonWebsocketConsumer
from django.conf import settings
from django.contrib.auth import get_user
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import OperationalError

from founder.services.messaging import (history, mark_read, owned_conversation, send_message,
                                        serialize_message, unread_count)
from founder.services.json_utils import bounded_json_loads
from founder.services.request_limits import consume_limit, RequestLimitExceeded


class MessagesConsumer(JsonWebsocketConsumer):
    def connect(self):
        user = self.scope['user']
        if not user.is_authenticated or not user.is_active:
            self.close(code=4401)
            return
        self.user_id = user.pk
        self.group = f'inbox.{self.user_id}'
        self.rate_window = time.monotonic()
        self.frame_count = 0
        async_to_sync(self.channel_layer.group_add)(self.group, self.channel_name)
        self.accept()
        self.send_json({'type': 'ready', 'unread': unread_count(self.user_id)})

    def disconnect(self, close_code):
        if hasattr(self, 'group'):
            async_to_sync(self.channel_layer.group_discard)(self.group, self.channel_name)

    def authenticated(self):
        # Re-read session to stop an already open socket after logout/password change.
        store = import_module(settings.SESSION_ENGINE).SessionStore
        user = get_user(SimpleNamespace(session=store(session_key=self.scope['session'].session_key)))
        if not user.is_authenticated or user.pk != self.user_id or not user.is_active:
            self.close(code=4401)
            return False
        return True

    def receive(self, text_data=None, bytes_data=None, **kwargs):
        if not self.authenticated():
            return
        if time.monotonic() - self.rate_window > 60:
            self.rate_window, self.frame_count = time.monotonic(), 0
        self.frame_count += 1
        if self.frame_count > 180:
            self.close(code=4429)
            return
        if text_data is None or len(text_data) > 24000:
            self.close(code=4400)
            return
        try:
            consume_limit(f"socket:{self.user_id}", 360, 60)
            payload = bounded_json_loads(text_data, max_chars=24000)
        except RequestLimitExceeded:
            self.close(code=4429)
            return
        except OperationalError:
            self.close(code=1013)
            return
        except ValueError:
            self.send_json({'type': 'error', 'message': 'Неверный формат сообщения.'})
            return
        if not isinstance(payload, dict):
            self.send_json({'type': 'error', 'message': 'Неверный формат сообщения.'})
            return
        self.receive_json(payload)

    def receive_json(self, data, **kwargs):
        action = data.get('type')
        conversation_id = data.get('conversation')
        client_id = data.get('client_id')
        try:
            if action in {"sync", "send", "read"} and not isinstance(conversation_id, str):
                raise ValidationError('Неверный идентификатор диалога.')
            if action == 'sync':
                after, before, since = data.get('after'), data.get('before'), data.get('since')
                if any(value is not None and (type(value) is not int or not 0 <= value <= 2**63-1)
                       for value in (after, before, since)):
                    raise ValidationError('Неверный номер сообщения.')
                if since is not None and after is not None:
                    raise ValidationError('Неверный запрос истории.')
                self.send_json(history(self.user_id, conversation_id, after=after, before=before, since=since))
            elif action == 'send':
                message, created = send_message(self.user_id, conversation_id, client_id, data.get('content'),
                                                data.get('attachments'))
                event = {'type': 'inbox.event', 'kind': 'message', 'message': serialize_message(message)}
                # Repeated delivery is safe: the client also deduplicates by DB ID.
                thread = message.conversation
                self.broadcast(thread, event)
                self.send_json({'type': 'ack', 'client_id': str(message.client_id),
                                'id': message.pk, 'message': serialize_message(message)})
            elif action == 'read':
                message_id = data.get('id')
                if type(message_id) is not int or not 0 < message_id <= 2**63-1:
                    raise ValidationError('Неверный номер сообщения.')
                thread = mark_read(self.user_id, conversation_id, message_id)
                self.broadcast(thread, {'type': 'inbox.event', 'kind': 'read',
                                        'conversation': str(thread.pk), 'user_id': self.user_id, 'id': message_id})
            elif action == 'ping':
                async_to_sync(self.channel_layer.group_add)(self.group, self.channel_name)
                self.send_json({'type': 'pong', 'unread': unread_count(self.user_id)})
            else:
                raise ValidationError('Неизвестное действие.')
        except (ObjectDoesNotExist, PermissionDenied):
            self.send_json({'type': 'error', 'message': 'Этот диалог недоступен.', 'client_id': client_id})
        except (ValidationError, ValueError, TypeError) as exc:
            message = exc.messages[0] if isinstance(exc, ValidationError) else 'Неверный запрос.'
            self.send_json({'type': 'error', 'message': message, 'client_id': client_id})

    def broadcast(self, thread, event):
        for user_id in (thread.user_low_id, thread.user_high_id):
            async_to_sync(self.channel_layer.group_send)(f'inbox.{user_id}', event)

    def inbox_event(self, event):
        if not self.authenticated():
            return
        payload = {key: value for key, value in event.items() if key != 'type'}
        conversation_id = payload.get('conversation') or payload.get('message', {}).get('conversation')
        if conversation_id:
            try:
                thread = owned_conversation(self.user_id, conversation_id)
            except ObjectDoesNotExist:
                return
            payload['thread_unread'] = thread.direct_messages.filter(
                id__gt=thread.read_id_for(self.user_id),
            ).exclude(sender_id=self.user_id).count()
        self.send_json({'type': payload.pop('kind'), **payload, 'unread': unread_count(self.user_id)})
