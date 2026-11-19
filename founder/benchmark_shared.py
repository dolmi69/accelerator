"""Explicit local comparison. Run with Django's test runner, never a customer database."""

import json
import os
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from founder.models import HostedSite, LabAIUsage, StartupProfile, User
from founder.services import hosting_runtime
from founder.services.shared_schema import bind_space, space_key
from founder.shared_models import SharedRecord
from founder.test_generated_backend import APP, HTML, REPORT
from founder.test_shared_host import Guest, answer, field, table


def pg_memory():
    # PostgreSQL RSS includes shared pages. Keep it separate from the app or worker RSS.
    with connection.cursor() as cur:
        cur.execute("SELECT pg_backend_pid()")
        pid = cur.fetchone()[0]
    db_parent = int(subprocess.check_output(["ps", "-p", str(pid), "-o", "ppid="], text=True))
    snapshot = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss="], text=True)
    kib = sum(
        int(m)
        for p, parent, m in (line.split() for line in snapshot.splitlines())
        if int(p) == db_parent or int(parent) == db_parent
    )
    return round(kib / 1024, 2)


def rss(pid):
    kib = subprocess.check_output(["ps", "-p", str(pid), "-o", "rss="], text=True).strip()
    return round(int(kib) / 1024, 2)


@skipUnless(
    sys.platform == "darwin" and connection.vendor == "postgresql", "macOS sandbox and PostgreSQL required"
)
@override_settings(
    DEBUG=True,
    BRUNOHOST_BASE_URL="http://localhost:8000",
    BRUNOHOST_PATH_MODE=False,
    BRUNOHOST_SHARED_ENABLED=True,
    BRUNOHOST_BACKEND_PROJECTS=(),
    BRUNOHOST_ENGINE="sandbox",
    LAB_RAW_AI_TEST_MODE=True,
    LAB_UNRESTRICTED_TEST_MODE=True,
    LAB_BACKEND_STAGED_GENERATION=True,
)
class Comparison(TransactionTestCase):
    def test_three_sites(self):
        owner = User.objects.create_user(username="benchmark")
        self.client.force_login(owner)
        schema = {
            "title": "Записи",
            "roles": {"member": "Участник", "manager": "Владелец"},
            "tables": {"items": table("Записи", {"name": field("Название", required=True)})},
        }
        output, versions = {}, {}
        with (
            tempfile.TemporaryDirectory() as root,
            override_settings(BRUNOHOST_ROOT=Path(root)),
            ExitStack() as cleanup,
        ):
            for kind in ("shared", "generated"):
                timings, worker_pids, code_calls = [], [], 0
                guests = []
                before = rss(os.getpid())
                database_before = pg_memory()
                for n in range(3):
                    project = StartupProfile.objects.create(owner=owner, name=f"Сайт {n} {kind}")
                    task = "Создай сайт для записей"
                    decision = {"action": "edit", "kind": kind, "task": task, "answer": "", "suggestion": ""}
                    parts = [
                        answer({"files": {"app.py": APP}, "report": REPORT}),
                        answer({"files": {"index.html": HTML}, "report": REPORT}),
                    ]
                    with (
                        patch(
                            "founder.services.lab_intent.generate_code", return_value=answer(decision)
                        ) as plan,
                        patch("founder.services.backend_stages.generate_code", side_effect=parts) as staged,
                        patch(
                            "founder.services.shared_generation.generate_code", return_value=answer(schema)
                        ) as shared,
                    ):
                        response = self.client.post(
                            reverse("lab_generate", args=[project.pk]), {"automatic": "1", "prompt": task}
                        )
                        self.assertEqual(response.status_code, 302)
                        code_calls += plan.call_count + staged.call_count + shared.call_count
                    version = project.lab_versions.get()
                    response = self.client.post(
                        reverse("lab_hosting", args=[project.pk]),
                        {"version": str(version.pk)},
                        HTTP_ACCEPT="application/json",
                    )
                    self.assertEqual(response.status_code, 200, response.content)
                    guest = Guest(version)
                    site = HostedSite.objects.get(startup=project)
                    if kind == "generated":
                        cleanup.callback(hosting_runtime.stop, site)
                        worker_pids.append(hosting_runtime._read(site)["pid"])
                        guest.call("GET", "api/session")
                    else:
                        guest.register("worker")
                    guests.append(guest)
                    versions[kind] = version
                visitor_calls = LabAIUsage.objects.count()
                errors = 0
                url = "api/records/items" if kind == "shared" else "api/items"
                for n in range(20):
                    for guest in guests:
                        for method in ("POST", "GET"):
                            start = time.perf_counter()
                            response = guest.call(
                                method, url, {"name": f"Запись {n}"} if method == "POST" else None
                            )
                            timings.append((time.perf_counter() - start) * 1000)
                            errors += int(response.status_code not in (200, 201))
                            if response.status_code in (200, 201) and method == "GET":
                                data = response.json()
                                self.assertEqual(len(data["records"] if kind == "shared" else data), n + 1)
                self.assertEqual(errors, 0)
                output[kind] = {
                    "sites": 3,
                    "requests": len(timings),
                    "errors": errors,
                    "lab_model_calls_mocked": code_calls,
                    "visitor_usage_rows": LabAIUsage.objects.count() - visitor_calls,
                    "p50_ms": round(statistics.median(timings), 2),
                    "p95_ms": round(sorted(timings)[(95 * len(timings) + 99) // 100 - 1], 2),
                    "runner_rss_before_mib": before,
                    "runner_rss_after_mib": rss(os.getpid()),
                    "extra_worker_rss_mib": [rss(pid) for pid in worker_pids],
                    "pg_cluster_rss_sum_before_mib": database_before,
                    "pg_cluster_rss_sum_after_mib": pg_memory(),
                }
                if kind == "generated":
                    # Exercise migration with the database produced by the actual isolated worker.
                    site = HostedSite.objects.get(version=versions[kind])
                    db = hosting_runtime.directory(site) / "app" / "data" / "data.sqlite3"
                    snap = Path(root) / "snapshot.sqlite3"
                    with (
                        sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as src,
                        sqlite3.connect(snap) as dest,
                    ):
                        src.backup(dest)
                    target = versions["shared"]
                    for _ in range(2):
                        call_command(
                            "import_shared_sqlite",
                            "--snapshot",
                            str(snap),
                            "--site-version",
                            str(target.pk),
                            "--table",
                            "items",
                            "--collection",
                            "items",
                            "--field",
                            "name=name",
                        )
                    with transaction.atomic():
                        bind_space(space_key(target, False))
                        self.assertEqual(SharedRecord.objects.count(), 20)
                    for existing in HostedSite.objects.filter(version__kind="generated"):
                        hosting_runtime.stop(existing)
        print("BRUNOHOST_BENCHMARK " + json.dumps(output))
