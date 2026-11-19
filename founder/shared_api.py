"""Trusted API for one resolved release. There is no model call or guest code execution here."""

import json
import math
import re
import secrets
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import PurePath
from uuid import UUID

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.template.loader import render_to_string
from django.utils.http import content_disposition_header
from PIL import Image, UnidentifiedImageError

from founder.models import HostedSite, StartupProfile
from founder.services.qwen import QwenOutputError
from founder.services.request_limits import RequestLimitExceeded, consume_limit
from founder.services.shared_schema import bind_space, space_key
from founder.shared_models import (
    SharedBooking,
    SharedFile,
    SharedMember,
    SharedRecord,
    SharedSpace,
)


class Rejected(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def check(ok, message="Проверьте поля запроса.", status=400):
    if not ok:
        raise Rejected(message, status)


def body(request, limit):
    chunk = request.read(limit + 1)
    check(len(chunk) <= limit, "Файл или запрос слишком большой.", 413)
    return chunk


def json_body(request):
    check(request.content_type == "application/json", "Нужен JSON.", 415)
    value = json.loads(body(request, 65536))
    check(isinstance(value, dict))
    return value


def uid(value):
    check(isinstance(value, str) and len(value) == 36)
    return UUID(value)


def row_id(value):
    check(type(value) is int and 0 < value < 2**63)
    return value


def visible(space, key, member, access):
    rows = SharedRecord.objects.filter(space=space, collection=key)
    if access == "all":
        return rows
    return rows.filter(owner=member) if access == "own" else rows.none()


def rights(space, key, member):
    table = space.schema["tables"].get(key)
    check(table is not None, "Таблица не найдена.", 404)
    return table, table["access"][member.role]


def writable(field, role):
    return "write_roles" not in field or role in field["write_roles"]


def file_visible(item, space, member):
    if item.owner_id == member.pk:
        return True
    table, grants = rights(space, item.collection, member)
    rows = visible(space, item.collection, member, grants["read"])
    links = Q()
    for name, field in table["fields"].items():
        if field["type"] == "file":
            links |= Q(values__contains={name: str(item.pk)})
    return bool(links) and rows.filter(links).exists()


def members_visible(space, member):
    members = SharedMember.objects.filter(space=space)
    directory = any(
        table["access"][member.role]["update"] == "all"
        and any(f["type"] == "member" and writable(f, member.role) for f in table["fields"].values())
        for table in space.schema["tables"].values()
    )
    return members if directory else members.filter(pk=member.pk)


def typed_value(space, key, field, value, member):
    if value is None:
        check(not field.get("required"), "Заполните обязательное поле.")
        return None
    kind = field["type"]
    if kind == "text":
        check(
            isinstance(value, str)
            and len(value) <= 4000
            and "\x00" not in value
            and (value.strip() or not field.get("required"))
        )
    elif kind == "number":
        check(type(value) in (int, float) and abs(value) <= 10**15 and math.isfinite(value))
    elif kind == "boolean":
        check(type(value) is bool)
    elif kind == "choice":
        check(value in field["options"])
    elif kind in ("datetime", "date"):
        check(isinstance(value, str) and len(value) <= 40)
        instant = datetime.fromisoformat(value) if kind == "datetime" else date.fromisoformat(value)
        if kind == "datetime":
            check(instant.utcoffset() is not None, "Укажите часовой пояс.")
            instant = instant.astimezone(UTC)
        return instant.isoformat()
    elif kind == "member":
        check(members_visible(space, member).filter(pk=uid(value)).exists(), "Участник недоступен.")
    elif kind == "ref":
        target = field["target"]
        access = rights(space, target, member)[1]["read"]
        check(
            visible(space, target, member, access).filter(pk=row_id(value)).exists(),
            "Связанная запись недоступна.",
        )
    elif kind == "file":
        item = SharedFile.objects.defer("content").filter(space=space, collection=key, pk=uid(value)).first()
        check(item and file_visible(item, space, member), "Файл недоступен.")
    return value


def auth(request, space, action, member, cookie, root):
    check(request.method == "POST", "Требуется POST.", 405)
    if action == "logout":
        if member:
            member.session_epoch += 1
            member.save(update_fields=["session_epoch"])
        response = JsonResponse({"ok": True})
        response.delete_cookie(cookie, path=root, samesite="Lax")
        return response
    data = json_body(request)
    check(
        set(data) == {"username", "password"}
        and isinstance(data["username"], str)
        and isinstance(data["password"], str)
    )
    username, password = data["username"].strip().casefold(), data["password"]
    check(
        re.fullmatch(r"[\w.@+-]{1,60}", username) and 1 <= len(password) <= 128, "Проверьте логин и пароль."
    )
    members = SharedMember.objects.filter(space=space)
    if action == "register":
        check(len(password) >= 8, "Введите пароль длиной от 8 символов.")
        reserve(space, member_count=1)
        check(not members.filter(username=username).exists(), "Этот логин занят.", 409)
        member = SharedMember.objects.create(space=space, username=username, password=make_password(password))
    else:
        member = members.filter(username=username).first()
        if not member:
            make_password(password)
        check(member and check_password(password, member.password), "Проверьте логин и пароль.", 401)
    signed = signing.dumps([str(member.pk), member.session_epoch], salt=cookie)
    response = JsonResponse({"id": str(member.pk), "role": member.role, "username": username})
    response.set_cookie(
        cookie, signed, path=root, httponly=True, secure=request.is_secure(), samesite="Lax", max_age=86400
    )
    return response


def account(request, space, cookie):
    try:
        raw = request.COOKIES.get(cookie)
        if not raw:
            return None
        key, epoch = signing.loads(raw, salt=cookie, max_age=86400)
        return SharedMember.objects.filter(space=space, pk=key, session_epoch=epoch).first()
    except signing.BadSignature:
        return None


def reserve(space, **delta):
    """Serialize new allocations per project; old preview versions share its draft budget."""
    limits = {
        "file_bytes": settings.BRUNOHOST_SHARED_STORAGE_BYTES,
        "file_count": settings.BRUNOHOST_SHARED_RECORDS,
        "record_count": settings.BRUNOHOST_SHARED_RECORDS,
        "member_count": settings.BRUNOHOST_SHARED_MEMBERS,
    }
    spaces = SharedSpace.objects.filter(startup_id=space.startup_id, live=space.live)
    totals = spaces.aggregate(**{name + "_used": Sum(name) for name in delta})
    for name, amount in delta.items():
        check((totals[name + "_used"] or 0) + amount <= limits[name], "Достигнут лимит данных сайта.", 409)
        setattr(space, name, getattr(space, name) + amount)
    space.save(update_fields=list(delta))


def store_file(space, member, key, name, data):
    check(0 < len(data) <= settings.BRUNOHOST_SHARED_FILE_BYTES, "Недопустимый размер файла.", 413)
    check(
        isinstance(name, str)
        and 0 < len(name) <= 150
        and PurePath(name).name == name
        and all(ord(c) >= 32 and c not in "/\\" for c in name)
    )
    # Only verified raster images are served inline. No active documents on guest or platform origins.
    try:
        with Image.open(BytesIO(data)) as img:
            check(img.format in ("JPEG", "PNG", "WEBP") and img.width * img.height <= 20_000_000)
            mime = Image.MIME[img.format]
            img.verify()
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError):
        raise Rejected("Поддерживаются фотографии JPEG, PNG и WebP.", 415) from None
    reserve(space, file_bytes=len(data), file_count=1)
    return SharedFile.objects.create(
        space=space, owner=member, collection=key, name=name, mime=mime, size=len(data), content=data
    )


def files(request, space, member, parts):
    if request.method == "POST" and len(parts) == 1:
        key = request.GET.get("table", "")
        table, access = rights(space, key, member)
        check(
            (access["create"] or access["update"] != "none")
            and any(f["type"] == "file" and writable(f, member.role) for f in table["fields"].values()),
            "Загрузка недоступна.",
            403,
        )
        item = store_file(
            space,
            member,
            key,
            request.GET.get("name", ""),
            body(request, settings.BRUNOHOST_SHARED_FILE_BYTES),
        )
        return JsonResponse({"id": str(item.pk), "name": item.name}, status=201)
    check(request.method == "GET" and len(parts) == 2, "Файл не найден.", 404)
    item = SharedFile.objects.defer("content").filter(space=space, pk=uid(parts[1])).first()
    check(item and file_visible(item, space, member), "Файл не найден.", 404)
    response = HttpResponse(bytes(item.content), content_type=item.mime)
    response["Content-Disposition"] = content_disposition_header(False, item.name)
    return response


def display_rows(rows, space, key, member):
    """Resolve permitted relationship labels once per field for the whole page."""
    fields = space.schema["tables"][key]["fields"]
    for name, field in fields.items():
        if field["type"] not in ("member", "ref"):
            continue
        ids = {row["values"][name] for row in rows if row["values"].get(name) is not None}
        if not ids:
            continue
        if field["type"] == "member":
            labels = {
                str(pk): username
                for pk, username in SharedMember.objects.filter(space=space, pk__in=ids).values_list(
                    "pk", "username"
                )
            }
        else:
            target = field["target"]
            table, access = rights(space, target, member)
            text_fields = [k for k, f in table["fields"].items() if f["type"] == "text"]
            labels = {
                pk: next((values[k] for k in text_fields if values.get(k)), f"#{pk}")
                for pk, values in visible(space, target, member, access["read"])
                .filter(pk__in=ids)
                .values_list("id", "values")
            }
        for row in rows:
            value = row["values"].get(name)
            if value is not None:
                row.setdefault("display", {})[name] = labels.get(value, "—")
    return rows


def records(request, space, member, parts):
    check(len(parts) in (2, 3), "Запись не найдена.", 404)
    key = parts[1]
    table, access = rights(space, key, member)
    fields = table["fields"]
    pk = row_id(int(parts[2])) if len(parts) == 3 else None
    if request.method == "GET":
        rows = visible(space, key, member, access["read"])
        check(access["read"] != "none", "Просмотр недоступен.", 403)
        if pk:
            row = rows.filter(pk=pk).values("id", "values").first()
            check(row, "Запись не найдена.", 404)
            return JsonResponse(row)
        if "field" in request.GET:
            field = request.GET["field"]
            check(field in fields and "value" in request.GET)
            value = json.loads(request.GET["value"])
            rows = rows.filter(
                values__contains={field: typed_value(space, key, fields[field], value, member)}
            )
        after = int(request.GET.get("after", "0"))
        check(0 <= after < 2**63)
        page = list(rows.filter(pk__gt=after).order_by("id").values("id", "owner_id", "values")[:100])
        return JsonResponse({"records": display_rows(page, space, key, member)})
    if request.method == "POST" and not pk:
        check(access["create"], "Создание недоступно.", 403)
        reserve(space, record_count=1)
        row = SharedRecord(space=space, owner=member, collection=key, values={})
    elif request.method == "PATCH" and pk:
        row = visible(space, key, member, access["update"]).filter(pk=pk).first()
        check(row, "Запись недоступна для изменения.", 404)
    else:
        raise Rejected("Метод недоступен.", 405)
    changes = json_body(request)
    check(changes.keys() <= fields.keys(), "В запросе неизвестное поле.")
    for name, value in changes.items():
        check(writable(fields[name], member.role), "Ваша роль не может изменять это поле.", 403)
        row.values[name] = typed_value(space, key, fields[name], value, member)
    for name, field in fields.items():
        check(
            not field.get("required") or row.values.get(name) is not None,
            "Заполните поле «" + field["label"] + "».",
        )
    check(len(json.dumps(row.values, ensure_ascii=False).encode()) <= 16384, "Запись слишком большая.", 413)
    row.save()
    if booking := table.get("booking"):
        starts, ends = (datetime.fromisoformat(row.values[booking[k]]) for k in ("start", "end"))
        check(ends > starts, "Окончание должно быть позже начала.")
        SharedBooking.objects.update_or_create(
            record=row,
            defaults={
                "space": space,
                "resource_id": row.values[booking["resource"]],
                "starts_at": starts,
                "ends_at": ends,
            },
        )
    return JsonResponse({"id": row.pk, "values": row.values}, status=200 if pk else 201)


def dispatch(request, version, live, endpoint, root):
    key = space_key(version, live)
    cookie = "bh_" + key.hex
    bind_space(key)
    spaces = SharedSpace.objects.filter(pk=key, version_id=version.pk, startup_id=version.startup_id)
    if request.method == "POST" and (
        endpoint.strip("/") in ("api/register", "api/files") or endpoint.startswith("api/records/")
    ):
        StartupProfile.objects.select_for_update().only("pk").get(pk=version.startup_id)
    if request.method not in ("GET", "HEAD") and endpoint != "api/login":
        spaces = spaces.select_for_update()
    space = spaces.first()
    check(
        space
        and (
            not live
            or HostedSite.objects.filter(startup_id=space.startup_id, version=version, active=True).exists()
        ),
        "Версия сайта обновилась. Перезагрузите страницу.",
        409,
    )
    if not endpoint:
        check(request.method in ("GET", "HEAD"), "Метод недоступен.", 405)
        nonce = secrets.token_urlsafe(24)
        response = HttpResponse(
            render_to_string(
                "founder/shared_site.html",
                {
                    "schema": space.schema,
                    "root": root,
                    "csrf": get_token(request) if not live else "",
                    "nonce": nonce,
                },
            )
        )
        response["Content-Security-Policy"] = (
            f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            "img-src 'self'; connect-src 'self'; form-action 'none'; base-uri 'none'; object-src 'none'; "
            + ("frame-ancestors 'none'" if live else "frame-ancestors 'self'")
        )
        response["X-Frame-Options"] = "DENY" if live else "SAMEORIGIN"
        if request.method == "HEAD":
            response.content = b""
        return response
    check(
        re.fullmatch(r"api/[a-z0-9_/-]+", endpoint) and "//" not in endpoint and not endpoint.endswith("/"),
        "Страница не найдена.",
        404,
    )
    parts = endpoint[4:].split("/")
    member = account(request, space, cookie)
    if len(parts) == 1 and parts[0] in ("register", "login", "logout"):
        return auth(request, space, parts[0], member, cookie, root)
    check(member, "Войдите в аккаунт сайта.", 401)
    if parts == ["me"] and request.method == "GET":
        return JsonResponse({"id": str(member.pk), "username": member.username, "role": member.role})
    if parts == ["members"] and request.method == "GET":
        q = request.GET.get("q", "")[:60]
        return JsonResponse(
            {
                "members": list(
                    members_visible(space, member)
                    .filter(username__icontains=q, username__gt=request.GET.get("after", "")[:60])
                    .order_by("username")
                    .values("id", "username")[:100]
                )
            }
        )
    if parts[0] == "records":
        return records(request, space, member, parts)
    if parts[0] == "files":
        return files(request, space, member, parts)
    raise Rejected("Действие не найдено.", 404)


def check_release(version, *, live):
    """Read-only smoke test inside the activation transaction, before guests see the release."""
    from django.http import HttpRequest

    probe = HttpRequest()
    probe.method = "GET"
    page = serve(probe, version, live=live)
    anonymous = serve(probe, version, live=live, endpoint="api/me")
    if page.status_code != 200 or b'id="definition"' not in page.content or anonymous.status_code != 401:
        raise QwenOutputError("Проверка страницы или входа не прошла. Предыдущая версия сохранена.")


def serve(request, version, *, live, endpoint="", root="/"):
    try:
        if endpoint.strip("/") in ("api/login", "api/register") and request.method == "POST":
            consume_limit(
                f"bh-auth:{space_key(version, live)}:{request.META.get('REMOTE_ADDR', '')}", 24, 900
            )
        with transaction.atomic():
            response = dispatch(request, version, live, endpoint, root)
    except Rejected as exc:
        response = JsonResponse({"error": str(exc)}, status=exc.status)
    except RequestLimitExceeded as exc:
        response = JsonResponse({"error": str(exc)}, status=429)
    except QwenOutputError as exc:
        response = JsonResponse({"error": str(exc)}, status=503)
    except IntegrityError as exc:
        if getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None) != "bh_no_overlap":
            raise
        response = JsonResponse({"error": "Это время уже занято. Выберите другой интервал."}, status=409)
    except (ValueError, TypeError, OverflowError, RecursionError):
        response = JsonResponse({"error": "Некорректное значение."}, status=400)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    response.brunohost_shared = True
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    if "Content-Security-Policy" not in response:
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
