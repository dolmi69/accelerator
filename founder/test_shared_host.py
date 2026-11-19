"""Exercise the public laboratory and guest HTTP contracts on real PostgreSQL."""

import copy
import io
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import connection, connections, transaction
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from PIL import Image

from founder.models import (
    HostedSite,
    LabAIUsage,
    LabGenerationJob,
    LabSiteVersion,
    StartupProfile,
    User,
)
from founder.services import lab_models
from founder.services.lab_jobs import run
from founder.services.qwen import CodeResult, QwenOutputError
from founder.services.shared_schema import bind_space, space_key
from founder.shared_models import SharedBooking, SharedFile, SharedRecord

MEMBER = {"read": "own", "create": True, "update": "none"}
MANAGER = {"read": "all", "create": True, "update": "all"}
ROLES = {"member": "Рабочий", "manager": "Прораб"}


def field(label, kind="text", **kw):
    return {"label": label, "type": kind, **kw}


def table(label, fields, member=MEMBER, **kw):
    return {"label": label, "fields": fields, "access": {"member": member, "manager": MANAGER}, **kw}


TRACKER = {
    "title": "Фотоотчёты",
    "roles": ROLES,
    "tables": {
        "defects": table(
            "Дефекты",
            {
                "room": field("Комната", required=True),
                "photo": field("Снимок", "file"),
                "assignee": field("Ответственный", "member", write_roles=["manager"]),
                "state": field("Статус", "choice", options=["Открыт", "Исправлен"]),
            },
        )
    },
}
TUTOR = {
    "title": "Расписание",
    "roles": {"member": "Ученик", "manager": "Администратор"},
    "tables": {
        "tutors": table(
            "Репетиторы",
            {"name": field("Имя", required=True)},
            member={"read": "all", "create": False, "update": "none"},
        ),
        "lessons": table(
            "Занятия",
            {
                "tutor": field("Репетитор", "ref", target="tutors", required=True),
                "start": field("Начало", "datetime", required=True),
                "end": field("Окончание", "datetime", required=True),
            },
            booking={"resource": "tutor", "start": "start", "end": "end"},
        ),
    },
}


def answer(data):
    return CodeResult(json.dumps(data, ensure_ascii=False), lab_models.active_model(), 11, 12)


def photo():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(buf, format="PNG")
    return buf.getvalue()


class Guest:
    def __init__(self, version, *, path=False):
        site = HostedSite.objects.get(startup=version.startup)
        self.client = Client()
        self.host = "localhost:8000" if path else site.slug + ".localhost:8000"
        self.root = "/sites/" + site.slug + "/" if path else "/"

    def call(self, method, route="", data=None):
        payload = data if isinstance(data, bytes) else json.dumps(data).encode()
        return self.client.generic(
            method,
            self.root + route,
            payload,
            content_type="image/png" if isinstance(data, bytes) else "application/json",
            HTTP_HOST=self.host,
            HTTP_ORIGIN="http://" + self.host,
        )

    def register(self, username):
        response = self.call("POST", "api/register", {"username": username, "password": "sample-password"})
        assert response.status_code == 200, response.content
        return response.json()["id"]


@skipUnless(
    connection.vendor == "postgresql", "PostgreSQL tenant policies and exclusion constraints required"
)
@override_settings(
    DEBUG=True,
    BRUNOHOST_SHARED_ENABLED=True,
    BRUNOHOST_BASE_URL="http://localhost:8000",
    BRUNOHOST_PATH_MODE=False,
    LAB_RAW_AI_TEST_MODE=True,
    LAB_UNRESTRICTED_TEST_MODE=True,
    LAB_CODE_PROVIDER="ofox",
    LAB_MODEL_ROUTING=True,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class SharedHostTests(TransactionTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(BRUNOHOST_ROOT=Path(self.tmp.name))
        override.enable()
        self.addCleanup(override.disable)
        self.owner = User.objects.create_user(username="brunohost-owner")
        self.project = StartupProfile.objects.create(owner=self.owner, name="Тестовая площадка")
        self.client.force_login(self.owner)

    def build(self, schema, project=None, source=None, *, complexity="standard"):
        project = project or self.project
        task = ("Добавь необязательное поле" if source else "Создай сайт") + " " + schema["title"]
        intent = {
            "action": "edit",
            "kind": "shared",
            "task": task,
            "answer": "",
            "suggestion": "",
            "complexity": complexity,
            "complexity_reason": "Несколько связанных сценариев" if complexity == "complex" else "",
        }
        with (
            patch("founder.lab_job_views.launch"),
            patch(
                "founder.services.lab_intent.generate_code", side_effect=lambda *a, **kw: answer(intent)
            ) as plan,
            patch(
                "founder.services.shared_generation.generate_code",
                side_effect=lambda *a, **kw: answer(schema),
            ) as code,
        ):
            response = self.client.post(
                reverse("lab_job_start", args=[project.pk]),
                {
                    "request_id": str(uuid4()),
                    "automatic": "1",
                    "prompt": task,
                    **({"source_version": str(source.pk)} if source else {}),
                },
            )
            self.assertEqual(response.status_code, 202, response.content[:400])
            idx = response.json()["job"]["id"]
            run(idx)
            run(idx)
            job = LabGenerationJob.objects.get(pk=idx)
            self.assertEqual(job.status, "succeeded", job.error)
            self.assertEqual((plan.call_count, code.call_count), (1, 1))
        version = job.version
        self.assertEqual(version.kind, "shared")
        self.assertEqual(
            version.model, lab_models.SOL if not source or complexity == "complex" else lab_models.LUNA
        )
        return version

    def publish(self, version, status=200):
        response = self.client.post(
            reverse("lab_hosting", args=[version.startup_id]),
            {"version": str(version.pk)},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, status, response.content[:500])

    def role(self, version, username):
        response = self.client.post(
            reverse("shared_grant", args=[version.startup_id, version.pk]),
            {"environment": "live", "role": "manager", "username": username},
        )
        self.assertEqual(response.status_code, 302)

    def test_tracker_publication_access_files_versions_and_isolation(self):
        version = self.build(TRACKER)
        preview = reverse("lab_preview", args=[self.project.pk, version.pk])
        self.assertContains(self.client.get(preview), "Фотоотчёты")
        self.assertEqual(
            self.client.post(
                preview + "api/register",
                {"username": "draft", "password": "test-only-123"},
                content_type="application/json",
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                preview + "api/records/defects", {"room": "Черновик"}, content_type="application/json"
            ).status_code,
            201,
        )
        with patch(
            "founder.services.hosting_runtime.ensure", side_effect=AssertionError("unexpected worker")
        ):
            self.publish(version)
            worker, manager = Guest(version), Guest(version)
            self.assertContains(worker.call("GET"), "Фотоотчёты")
            worker_id = worker.register("рабочий")
            manager.register("прораб")
            self.role(version, "прораб")
            self.assertEqual(manager.call("GET", "api/records/defects").json()["records"], [])
            unauthorized = worker.call("POST", "api/records/defects", {"room": "Кухня", "assignee": worker_id})
            self.assertEqual(unauthorized.status_code, 403)
            self.assertEqual(manager.call("GET", "api/records/defects").json()["records"], [])
            upload = worker.call("POST", "api/files?table=defects&name=photo.png", photo())
            self.assertEqual(upload.status_code, 201, upload.content)
            file_id = upload.json()["id"]
            self.assertEqual(manager.call("GET", "api/files/" + file_id).status_code, 404)
            record = worker.call("POST", "api/records/defects", {"room": "Кухня", "photo": file_id}).json()[
                "id"
            ]
            route = "api/records/defects/" + str(record)
            self.assertEqual(worker.call("PATCH", route, {"state": "Исправлен"}).status_code, 404)
            self.assertEqual(manager.call("PATCH", route, {"assignee": worker_id}).status_code, 200)
            self.assertEqual(manager.call("GET", "api/files/" + file_id).content, photo())
            self.assertEqual(
                len(
                    manager.call("GET", "api/records/defects?field=room&value=%22Кухня%22").json()["records"]
                ),
                1,
            )
            second = StartupProfile.objects.create(owner=self.owner, name="Другая стройка")
            foreign = self.build(TRACKER, second)
            self.publish(foreign)
            alien = Guest(foreign)
            alien.register("прораб")
            self.role(foreign, "прораб")
            self.assertEqual(alien.call("GET", route).status_code, 404)
            self.assertEqual(alien.call("PATCH", route, {"room": "X"}).status_code, 404)
            self.assertEqual(alien.call("GET", "api/files/" + file_id).status_code, 404)
            self.assertEqual(
                alien.call("POST", "api/records/defects", {"room": "X", "photo": file_id}).status_code, 400
            )
            with transaction.atomic():
                bind_space(space_key(foreign, True))
                self.assertFalse(SharedRecord.objects.filter(pk=record).exists())
                self.assertFalse(SharedFile.objects.filter(pk=file_id).exists())
            schema = copy.deepcopy(TRACKER)
            schema["tables"]["defects"]["fields"]["room"]["label"] = "Помещение"
            schema["tables"]["defects"]["fields"]["floor"] = field("Этаж", "number")
            updated = self.build(schema, source=version)
            self.publish(updated)
            self.publish(updated)
            connections.close_all()
            import re

            page = manager.call("GET").content.decode()
            embedded = json.loads(
                re.search(r'<script id="definition" type="application/json">(.*?)</script>', page)[1]
            )
            self.assertEqual(embedded["tables"]["defects"]["fields"]["room"]["label"], "Помещение")
            self.assertEqual(manager.call("GET", route).json()["values"]["room"], "Кухня")
            self.assertEqual(manager.call("GET", "api/files/" + file_id).content, photo())
            self.assertEqual(manager.call("PATCH", route, {"floor": 3}).status_code, 200)
            bad = copy.deepcopy(schema)
            del bad["tables"]["defects"]["fields"]["room"]
            broken = LabSiteVersion.objects.create(startup=self.project, kind="shared", shared_schema=bad)
            self.publish(broken, 400)
            candidate = LabSiteVersion.objects.create(startup=self.project, kind="shared", shared_schema=schema)
            with patch("founder.shared_api.check_release", side_effect=QwenOutputError("Проверка страницы не прошла")):
                self.publish(candidate, 400)
            self.assertEqual(HostedSite.objects.get(startup=self.project).version_id, updated.pk)
            self.assertEqual(worker.call("GET", route).json()["values"]["floor"], 3)
            worker.call("POST", "api/logout")
            self.assertEqual(worker.call("GET", route).status_code, 401)
            self.assertEqual(
                worker.call(
                    "POST", "api/login", {"username": "рабочий", "password": "sample-password"}
                ).status_code,
                200,
            )
            self.assertEqual(LabAIUsage.objects.count(), 0)
            with override_settings(BRUNOHOST_SHARED_ENABLED=False):
                self.assertEqual(worker.call("GET", route).status_code, 200)

    def test_routing_and_database_write_guard(self):
        from django.db import DatabaseError

        from founder.shared_models import SharedMember

        v = self.build(TRACKER)
        updated = self.build(TRACKER, source=v, complexity="complex")
        with transaction.atomic():
            bind_space(space_key(updated, False))
            with self.assertRaises(DatabaseError), transaction.atomic():
                SharedMember.objects.create(space_id=space_key(v, False), username="fixture", password="!")
        self.assertFalse(SharedRecord.objects.exists())

    def test_two_students_one_slot(self):
        version = self.build(TUTOR)
        self.publish(version)
        admin = Guest(version)
        admin.register("teacher")
        self.role(version, "teacher")
        tutor = admin.call("POST", "api/records/tutors", {"name": "Анна"}).json()["id"]
        slots = {"tutor": tutor, "start": "2026-12-10T12:00:00+03:00", "end": "2026-12-10T13:00:00+03:00"}
        clients = [Guest(version), Guest(version)]
        for i, client in enumerate(clients):
            client.register("student" + str(i))
        barrier = Barrier(2)

        def book(guest):
            try:
                barrier.wait(timeout=10)
                return guest.call("POST", "api/records/lessons", slots).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(book, clients)), [201, 409])
        with transaction.atomic():
            bind_space(space_key(version, True))
            self.assertEqual(SharedBooking.objects.count(), 1)
            self.assertEqual(SharedRecord.objects.filter(collection="lessons").count(), 1)
        slots.update(start=slots["end"], end="2026-12-10T14:00:00+03:00")
        self.assertEqual(admin.call("POST", "api/records/lessons", slots).status_code, 201)

    def test_legacy_sqlite_snapshot_with_photos_is_atomic_and_repeatable(self):
        import sqlite3

        from django.core.management import call_command
        from django.core.management.base import CommandError

        from founder.shared_models import SharedSpace

        v = self.build(TRACKER)
        snapshot = Path(self.tmp.name) / "legacy.sqlite3"
        # Same items table as the existing generated SQLite app fixture, extended with a BLOB photo.
        with sqlite3.connect(snapshot) as db:
            db.execute("CREATE TABLE items (name TEXT, picture BLOB)")
            db.executemany("INSERT INTO items VALUES (?,?)", [("Кухня", photo()), ("Холл", b"invalid")])
        args = [
            "--site-version",
            str(v.pk),
            "--snapshot",
            str(snapshot),
            "--table",
            "items",
            "--collection",
            "defects",
            "--field",
            "room=name",
            "--field",
            "photo=picture",
        ]
        with self.assertRaises(CommandError):
            call_command("import_shared_sqlite", *args, stdout=io.StringIO())
        with transaction.atomic():
            bind_space(space_key(v, False))
            self.assertEqual((SharedRecord.objects.count(), SharedFile.objects.count()), (0, 0))
        with sqlite3.connect(snapshot) as db:
            db.execute("UPDATE items SET picture=? WHERE name=?", (photo(), "Холл"))
        for _ in range(2):
            call_command("import_shared_sqlite", *args, stdout=io.StringIO())
        with transaction.atomic():
            bind_space(space_key(v, False))
            self.assertEqual((SharedRecord.objects.count(), SharedFile.objects.count()), (2, 2))
            self.assertEqual(bytes(SharedFile.objects.first().content), photo())
            self.assertEqual(SharedSpace.objects.get(pk=space_key(v, False)).file_bytes, 2 * len(photo()))

    @override_settings(BRUNOHOST_PATH_MODE=True, BRUNOHOST_SHARED_STORAGE_BYTES=128)
    def test_path_mode_quota_role_and_csrf(self):
        v = self.build(TRACKER)
        self.publish(v)
        g = Guest(v, path=True)
        self.assertContains(g.call("GET"), "Фотоотчёты")
        g.register("worker")
        self.assertEqual(
            g.call(
                "POST", "api/register", {"username": "x", "password": "test-1234", "role": "manager"}
            ).status_code,
            400,
        )
        self.assertEqual(g.call("POST", "api/files?table=defects&name=t.png", photo()).status_code, 201)
        self.assertEqual(g.call("POST", "api/files?table=defects&name=t.png", photo()).status_code, 409)
        res = g.client.post(
            g.root + "api/records/defects",
            {"room": "A"},
            content_type="application/json",
            HTTP_HOST=g.host,
            HTTP_ORIGIN="https://outside.example",
        )
        self.assertEqual(res.status_code, 403)
