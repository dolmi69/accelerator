"""Import a mapped collection from a closed SQLite snapshot into a test preview."""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from founder.models import LabSiteVersion, StartupProfile
from founder.services.qwen import QwenOutputError
from founder.services.shared_schema import activate, identifier
from founder.shared_api import Rejected, reserve, store_file, typed_value
from founder.shared_models import SharedFile, SharedMember, SharedRecord


class Command(BaseCommand):
    help = "Импорт одной таблицы SQLite и фотографий BLOB в пустой предпросмотр BrunoHost."

    def add_arguments(self, parser):
        parser.add_argument("--site-version", required=True)
        parser.add_argument("--snapshot", type=Path, required=True)
        parser.add_argument("--table", required=True)
        parser.add_argument("--collection", required=True)
        parser.add_argument("--field", action="append", required=True, help="поле_схемы=колонка_SQLite")

    def handle(self, **opts):
        path = opts["snapshot"].resolve()
        if not path.is_file() or any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")):
            raise CommandError("Нужна отдельная согласованная копия SQLite без открытого журнала.")
        try:
            mapping = dict(pair.split("=", 1) for pair in opts["field"])
            if (
                len(mapping) != len(opts["field"])
                or not identifier(opts["table"])
                or not all(identifier(k) and identifier(v) for k, v in mapping.items())
            ):
                raise ValueError
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256")
            digest.update(json.dumps([opts["table"], opts["collection"], mapping], sort_keys=True).encode())
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as source:
                source.execute("PRAGMA trusted_schema=OFF")
                source.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024**2)
                tables = {row[1] for row in source.execute("PRAGMA table_list") if row[2] == "table"}
                columns = {row[1] for row in source.execute(f'PRAGMA table_info("{opts["table"]}")')}
                if opts["table"] not in tables or not set(mapping.values()) <= columns:
                    raise CommandError("Таблица или колонка отсутствует в снимке.")
                with transaction.atomic():
                    version = LabSiteVersion.objects.get(pk=opts["site_version"], kind="shared")
                    StartupProfile.objects.select_for_update().get(pk=version.startup_id)
                    space = activate(version, live=False)
                    if space.import_digest == digest.hexdigest():
                        self.stdout.write("Этот снимок уже перенесён.")
                        return
                    if space.import_digest or space.record_count or space.file_count:
                        raise CommandError("Для импорта нужен пустой предпросмотр.")
                    key = opts["collection"]
                    table = space.schema["tables"][key]
                    fields = table["fields"]
                    if (
                        table.get("booking")
                        or not mapping.keys() <= fields.keys()
                        or any(f.get("required") and name not in mapping for name, f in fields.items())
                        or any(fields[k]["type"] in ("member", "ref") for k in mapping)
                    ):
                        raise CommandError(
                            "Для связей, бронирований или обязательных полей нужна отдельная карта переноса."
                        )
                    reserve(space, member_count=1)
                    owner = SharedMember.objects.create(
                        space=space,
                        username="импорт-" + digest.hexdigest()[:12],
                        password=make_password(None),
                    )
                    names = ",".join('"' + name + '"' for name in mapping.values())
                    rows = files = 0
                    for values in source.execute(f'SELECT {names} FROM "{opts["table"]}"'):
                        result = {}
                        for name, val in zip(mapping, values):
                            field = fields[name]
                            if val is not None and field["type"] == "file":
                                if not isinstance(val, bytes):
                                    raise CommandError("Ожидалась фотография BLOB в SQLite.")
                                item = store_file(space, owner, key, f"photo-{rows + 1}", val)
                                files += 1
                                val = str(item.pk)
                            elif field["type"] == "boolean" and val in (0, 1):
                                val = bool(val)
                            result[name] = typed_value(space, key, field, val, owner)
                        if len(json.dumps(result, ensure_ascii=False).encode()) > 16384:
                            raise CommandError("Запись превышает максимальный размер.")
                        reserve(space, record_count=1)
                        SharedRecord.objects.create(space=space, collection=key, owner=owner, values=result)
                        rows += 1
                    if (
                        rows != source.execute(f'SELECT COUNT(*) FROM "{opts["table"]}"').fetchone()[0]
                        or rows != SharedRecord.objects.filter(space=space).count()
                        or files != SharedFile.objects.filter(space=space).count()
                    ):
                        raise CommandError("Количество записей или файлов различается.")
                    space.import_digest = digest.hexdigest()
                    space.save(update_fields=["import_digest"])
        except (
            sqlite3.Error,
            ValueError,
            KeyError,
            TypeError,
            OSError,
            Rejected,
            QwenOutputError,
            LabSiteVersion.DoesNotExist,
        ) as exc:
            raise CommandError("Перенос отменён: " + str(exc)) from exc
        self.stdout.write(f"Перенесено записей {rows}, фотографий {files}.")
