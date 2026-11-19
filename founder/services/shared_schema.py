"""Versioned data contract. IDs are storage keys, labels are presentation."""

import json
import re
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings
from django.db import connection

from founder.services.qwen import QwenOutputError
from founder.shared_models import SharedSpace

KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")
TYPES = {"text", "number", "boolean", "date", "datetime", "choice", "ref", "member", "file"}


def require(test, message):
    if not test:
        raise QwenOutputError(message)


def enabled():
    return settings.BRUNOHOST_SHARED_ENABLED and connection.vendor == "postgresql"


def identifier(key):
    return isinstance(key, str) and KEY.fullmatch(key) and key not in {"constructor", "prototype", "null"}


def label(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 100 and "\x00" not in value


def mapping(value, maximum):
    return isinstance(value, dict) and 0 < len(value) <= maximum and all(identifier(k) for k in value)


def validate(schema):
    require(
        isinstance(schema, dict)
        and {"title", "roles", "tables"} <= schema.keys() <= {"title", "roles", "tables", "theme"}
        and label(schema["title"]),
        "Нужны название, роли и таблицы сайта.",
    )
    require(len(json.dumps(schema, ensure_ascii=False).encode()) <= 48000, "Описание сайта слишком большое.")
    require(
        schema.get("theme", "green") in ("green", "blue", "plum", "graphite"), "Недоступная цветовая тема."
    )
    roles, tables = schema["roles"], schema["tables"]
    require(
        mapping(roles, 8) and "member" in roles and all(label(v) for v in roles.values()),
        "Укажите роли и начальную роль member.",
    )
    require(mapping(tables, 16), "Проверьте идентификаторы таблиц (от 1 до 16 таблиц).")
    for table in tables.values():
        require(
            isinstance(table, dict)
            and {"label", "fields", "access"} <= table.keys()
            and table.keys() <= {"label", "fields", "access", "booking", "ui"}
            and label(table["label"]),
            "Проверьте описание таблицы.",
        )
        access, fields = table["access"], table["fields"]
        require(isinstance(access, dict) and set(access) == set(roles), "Укажите права для каждой роли.")
        for rights in access.values():
            require(
                isinstance(rights, dict)
                and set(rights) == {"read", "update", "create"}
                and type(rights["create"]) is bool
                and rights["read"] in ("all", "own", "none")
                and rights["update"] in ("all", "own", "none")
                and ("none", "own", "all").index(rights["update"])
                <= ("none", "own", "all").index(rights["read"]),
                "Права на изменение требуют соответствующих прав на чтение.",
            )
        require(mapping(fields, 32), "Проверьте идентификаторы полей (от 1 до 32 полей).")
        ui = table.get("ui", {})
        require(
            isinstance(ui, dict) and ui.keys() <= {"order", "add", "save", "empty"},
            "Некорректные настройки формы.",
        )
        order = ui.get("order", list(fields))
        require(
            isinstance(order, list)
            and all(identifier(k) for k in order)
            and len(order) == len(fields)
            and set(order) == set(fields),
            "Проверьте порядок полей формы.",
        )
        require(all(label(value) for key, value in ui.items() if key != "order"), "Проверьте подписи формы.")
        for field in fields.values():
            require(
                isinstance(field, dict)
                and isinstance(field.get("type"), str)
                and field["type"] in TYPES
                and label(field.get("label"))
                and type(field.get("required", False)) is bool,
                "Проверьте название и тип поля.",
            )
            kind = field["type"]
            extra = {"choice": {"options"}, "ref": {"target"}}.get(kind, set())
            require(field.keys() <= {"label", "type", "required", "write_roles"} | extra, "Неизвестная настройка поля.")
            if "write_roles" in field:
                writers = field["write_roles"]
                require(isinstance(writers, list) and 0 < len(writers) <= len(roles)
                        and all(identifier(r) and r in roles for r in writers)
                        and len(set(writers)) == len(writers), "Проверьте роли, которым разрешено менять поле.")
                require(not field.get("required") or all(not grant["create"] or role in writers
                        for role, grant in access.items()), "Обязательное поле должна заполнять каждая роль с правом создания.")
            if kind == "choice":
                options = field.get("options")
                require(
                    isinstance(options, list)
                    and 0 < len(options) <= 40
                    and all(label(v) for v in options)
                    and len(set(options)) == len(options),
                    "Укажите варианты выбора без повторов.",
                )
            if kind == "ref":
                require(
                    identifier(field.get("target")) and field["target"] in tables,
                    "Связанная таблица не найдена.",
                )
        if "booking" in table:
            booking = table["booking"]
            require(
                isinstance(booking, dict)
                and set(booking) == {"resource", "start", "end"}
                and all(identifier(v) for v in booking.values())
                and len(set(booking.values())) == 3,
                "Укажите ресурс, начало и окончание брони.",
            )
            for prop, kind in [("resource", "ref"), ("start", "datetime"), ("end", "datetime")]:
                field = fields.get(booking[prop], {})
                require(
                    field.get("type") == kind and field.get("required"),
                    "Для бронирования нужны три обязательных поля.",
                )
    for table in tables.values():
        for field in table["fields"].values():
            if field["type"] == "ref" and field.get("required"):
                target_rights = tables[field["target"]]["access"]
                for role, rights in table["access"].items():
                    require(not rights["create"] or target_rights[role]["read"] != "none",
                            "Для создания записи роль должна видеть обязательный связанный ресурс.")
    return schema


def compatible(old, new):
    require(
        old["roles"].keys() <= new["roles"].keys(),
        "Удаление существующей роли требует переноса пользователей.",
    )
    for key, before in old["tables"].items():
        after = new["tables"].get(key)
        require(
            after is not None and before.get("booking") == after.get("booking"),
            "Удаление таблицы или смена правила брони требует отдельного переноса.",
        )
        for name, field in before["fields"].items():
            other = after["fields"].get(name, {})
            require(
                field["type"] == other.get("type")
                and field.get("target") == other.get("target")
                and set(field.get("options", [])) <= set(other.get("options", [])),
                "Удаление поля, варианта или смена типа требует отдельного переноса.",
            )
        require(
            all(
                not field.get("required") or before["fields"].get(name, {}).get("required")
                for name, field in after["fields"].items()
            ),
            "Новое поле для существующих записей должно быть необязательным.",
        )


def space_key(version, live):
    return uuid5(NAMESPACE_URL, f"brunohost/{version.startup_id}/" + ("live" if live else str(version.pk)))


def bind_space(key):
    # Pausing new releases must leave published sites online.
    if connection.vendor != "postgresql":
        raise QwenOutputError("Общему серверу нужна PostgreSQL.")
    if not connection.in_atomic_block:
        raise RuntimeError("Tenant scope requires a transaction")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('brunohost.space', %s, true), rolsuper OR rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user",
            [str(key)],
        )
        require(not cursor.fetchone()[1], "BrunoHost требует роль PostgreSQL без обхода изоляции данных.")


def activate(version, *, live):
    """Caller owns the release transaction. Failed schema checks roll everything back."""
    schema = validate(version.shared_schema)
    key = space_key(version, live)
    bind_space(key)
    space, created = SharedSpace.objects.select_for_update().get_or_create(
        id=key,
        defaults={"startup_id": version.startup_id, "version": version, "live": live, "schema": schema},
    )
    if not created and space.version_id != version.pk:
        compatible(space.schema, schema)
        space.version, space.schema = version, schema
        space.save(update_fields=["version", "schema"])
    return space
