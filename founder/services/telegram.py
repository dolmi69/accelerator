"""Telegram serves as a launcher; all business data stays in the Django app."""

import ipaddress
import logging
import re
from urllib.parse import urlsplit

import httpx
from django.conf import settings


class TelegramError(Exception):
    def __init__(self, message, *, code=0, retry_after=5):
        super().__init__(message)
        self.code = code
        self.retry_after = max(1, min(retry_after, 60))


def validate_app_url(value):
    try:
        url = urlsplit(value.strip())
        host = url.hostname or ""
        port = url.port
        if (url.scheme != "https" or not host or url.username or url.password
                or url.query or url.fragment or port not in (None, 443)
                or host == "localhost" or host.endswith((".localhost", ".local"))):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host:
                raise ValueError
        else:
            if not address.is_global:
                raise ValueError
    except (ValueError, AttributeError):
        raise TelegramError("Нужен публичный HTTPS-адрес сайта без параметров и пароля.") from None
    return value.strip()


class TelegramClient:
    def __init__(self, token=None, *, transport=None):
        token = token if token is not None else settings.TELEGRAM_BOT_TOKEN
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
            raise TelegramError("Укажите токен BotFather в TELEGRAM_BOT_TOKEN.")
        # HTTP debug logging would otherwise include the token in the API URL.
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        self._client = httpx.Client(
            base_url=f"https://api.telegram.org/bot{token}/",
            timeout=httpx.Timeout(40, connect=10), transport=transport,
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._client.close()

    def call(self, method, **payload):
        try:
            response = self._client.post(method, json=payload)
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise TelegramError("Telegram временно недоступен. Повторяем подключение.") from None
        if not isinstance(data, dict):
            raise TelegramError("Telegram вернул некорректный ответ.")
        if not response.is_success or not data.get("ok"):
            code = data.get("error_code", response.status_code)
            messages = {
                401: "Telegram отклонил токен. Проверьте токен в BotFather.",
                403: "Бот не может отправить сообщение этому пользователю.",
                409: "Бот уже запущен в другом процессе или использует webhook.",
                429: "Достигнут лимит Telegram. Повторим запрос позже.",
            }
            raise TelegramError(messages.get(code, "Ошибка Telegram API; код " + str(code)),
                                code=code, retry_after=data.get("parameters", {}).get("retry_after", 5))
        return data.get("result")


def configure_bot(client, app_url):
    app_url = validate_app_url(app_url)
    bot = client.call("getMe")
    client.call("setMyCommands", commands=[
        {"command": "start", "description": "Открыть Co-Founder.AI"},
        {"command": "app", "description": "Мои проекты и радары"},
        {"command": "help", "description": "Как пользоваться Бруно"},
    ])
    client.call("setChatMenuButton", menu_button={
        "type": "web_app", "text": "Открыть приложение", "web_app": {"url": app_url},
    })
    return bot


def handle_update(client, update, app_url):
    """Reply only to incoming launcher commands in private conversations."""
    message = update.get("message", {})
    if message.get("chat", {}).get("type") != "private" or message.get("from", {}).get("is_bot"):
        return
    text = message.get("text", "")
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
    if command not in {"/start", "/app", "/help"}:
        return
    welcome = (
        "🐻 Здравствуйте! Я Бруно, ваш AI-напарник.\n\n"
        "Откройте Co-Founder.AI, расскажите о своей идее и соберите радар по пяти направлениям. "
        "Можно вести несколько проектов и отслеживать изменения.\n\n"
        "Войдите в существующий аккаунт сайта, чтобы увидеть свои проекты, или создайте новый."
    )
    if command == "/help":
        welcome = (
            "Откройте приложение → войдите в аккаунт → выберите или создайте проект.\n\n"
            "Поговорите с Бруно и нажмите «Составить таблицу». Радары, история и уточнения "
            "доступны внутри приложения. В этом чате бот открывает сайт."
        )
    client.call("sendMessage", chat_id=message["chat"]["id"], text=welcome,
                reply_markup={"inline_keyboard": [[{
                    "text": "🐻 Открыть Co-Founder.AI", "web_app": {"url": app_url},
                }]]})
