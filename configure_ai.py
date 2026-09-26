"""Локальная настройка GigaChat без передачи ключа через чат."""

import getpass
import os
import secrets
from pathlib import Path

from dotenv import set_key


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def configure():
    print("\nПодключение GigaChat к Co-Founder.AI")
    print("1 — прямой GigaChat API (ключ авторизации Сбера)")
    print("2 — GigaChat в Cloud.ru (Key Secret сервисного аккаунта)")
    choice = input("Выберите 1 или 2: ").strip()
    if choice not in {"1", "2"}:
        print("Настройка отменена: выберите 1 или 2.")
        return 1

    key = getpass.getpass("Вставьте API-ключ (он не будет показан): ").strip()
    if not key:
        print("Пустой ключ не сохранён.")
        return 1

    if not ENV_FILE.exists():
        ENV_FILE.touch(mode=0o600)
        set_key(ENV_FILE, "DJANGO_SECRET_KEY", secrets.token_urlsafe(48))
        set_key(ENV_FILE, "DJANGO_DEBUG", "1")
        set_key(ENV_FILE, "DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
    os.chmod(ENV_FILE, 0o600)

    if choice == "1":
        print("Тип доступа: 1 — физлицо, 2 — бизнес-пакет, 3 — корпоративный")
        scope_choice = input("Выберите тип доступа [1]: ").strip() or "1"
        scopes = {
            "1": "GIGACHAT_API_PERS",
            "2": "GIGACHAT_API_B2B",
            "3": "GIGACHAT_API_CORP",
        }
        if scope_choice not in scopes:
            print("Неверный тип доступа. Ключ не сохранён.")
            return 1
        set_key(ENV_FILE, "GIGACHAT_CREDENTIALS", key)
        set_key(ENV_FILE, "GIGACHAT_SCOPE", scopes[scope_choice])
        set_key(ENV_FILE, "GIGACHAT_MODEL", "GigaChat-3-Pro")
        set_key(ENV_FILE, "AI_PROVIDER", "gigachat")
    else:
        set_key(ENV_FILE, "CLOUDRU_API_KEY", key)
        set_key(ENV_FILE, "AI_PROVIDER", "cloudru")

    print("\nГотово. Ключ сохранён локально в .env. Перезапустите сервер Co-Founder.AI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(configure())
