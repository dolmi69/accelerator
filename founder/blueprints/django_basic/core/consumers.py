"""Persistent private chats. Browser-supplied sender IDs are never trusted."""
import json
from uuid import UUID
from channels.auth import get_user
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from importlib import import_module
from .models import Conversation, Message
from .security import allowed
from .capabilities import enabled


class ChatConsumer(AsyncJsonWebsocketConsumer):
    async def authorized(self):
        # Reload the session from storage so an already-open socket stops after logout.
        session = import_module(settings.SESSION_ENGINE).SessionStore(session_key=self.scope["session"].session_key)
        user = await get_user({"session": session})
        return bool(enabled('chat') and user.is_authenticated and user.is_active and user.pk == self.user_id
                    and await self.member())

    @database_sync_to_async
    def member(self):
        return Conversation.objects.filter(pk=self.conversation_id).filter(Q(first_id=self.user_id) | Q(second_id=self.user_id)).exists()

    async def connect(self):
        self.joined = False
        self.user_id = self.scope["user"].pk
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        self.group = "chat_" + self.conversation_id.hex
        if not self.scope["user"].is_authenticated or not await self.authorized():
            await self.close(code=4403)
            return
        await self.channel_layer.group_add(self.group, self.channel_name)
        self.joined = True
        await self.accept()

    async def disconnect(self, code):
        if self.joined:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        if not text_data or len(text_data) > 12000 or bytes_data is not None:
            await self.close(code=4400)
            return
        try:
            data = json.loads(text_data)
        except (ValueError, RecursionError):
            await self.close(code=4400)
            return
        if not isinstance(data, dict):
            await self.close(code=4400)
            return
        content = data.get("content")
        try:
            nonce = UUID(str(data.get("nonce", "")))
        except ValueError:
            await self.send_json({"type": "error", "error": "Не удалось отправить сообщение. Обновите страницу."})
            return
        if not isinstance(content, str) or not content.strip() or len(content) > 2000:
            await self.send_json({"type": "error", "error": "Напишите от 1 до 2000 символов.", "nonce": str(nonce)})
            return
        if not await self.authorized():
            await self.close(code=4403)
            return
        try:
            result, created = await self.save_message(content.strip(), nonce)
        except (IntegrityError, OperationalError):
            await self.send_json({"type": "error", "error": "Сервис занят. Повторите отправку.", "nonce": str(nonce)})
            return
        if result is None:
            await self.send_json({"type": "error", "error": "Слишком много сообщений или повтор с другим текстом.", "nonce": str(nonce)})
        elif created:
            await self.channel_layer.group_send(self.group, {"type": "chat.message", "message": result})
        else:
            await self.send_json({"type": "message", **result})

    @database_sync_to_async
    @transaction.atomic
    def save_message(self, content, nonce):
        old = Message.objects.filter(sender_id=self.user_id, nonce=nonce).select_related("sender").first()
        if old:
            if old.conversation_id != self.conversation_id or old.content != content:
                return None, False
            row, created = old, False
        else:
            if not allowed("message:" + str(self.user_id), 30, 60):
                return None, False
            row, created = Message.objects.get_or_create(sender_id=self.user_id, nonce=nonce,
                defaults={"conversation_id": self.conversation_id, "content": content})
            if row.conversation_id != self.conversation_id or row.content != content:
                return None, False
        if created:
            from .services import notify
            from django.urls import reverse
            conversation = Conversation.objects.get(pk=self.conversation_id)
            recipient = conversation.second_id if conversation.first_id == self.user_id else conversation.first_id
            notify(recipient,'Новое личное сообщение',reverse('chat',args=[self.conversation_id]),key=f'message:{row.pk}')
        return {"id": row.pk, "sender_id": row.sender_id, "sender": row.sender.username,
                "content": row.content, "created_at": row.created_at.isoformat(), "nonce": str(row.nonce)}, created

    async def chat_message(self, event):
        if not await self.authorized():
            await self.close(code=4403)
            return
        await self.send_json({"type": "message", **event["message"]})
