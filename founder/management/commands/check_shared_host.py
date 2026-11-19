from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from founder.hosting import base_url
from founder.services.qwen import QwenOutputError
from founder.services.shared_schema import bind_space, enabled
from founder.shared_models import SharedBooking, SharedFile, SharedMember, SharedRecord


class Command(BaseCommand):
    help = "Проверяет PostgreSQL, реальные политики изоляции, ограничение брони и публичный адрес BrunoHost."

    def handle(self, **options):
        if not enabled() or not base_url():
            raise CommandError("Проверьте DATABASE_URL, BRUNOHOST_BASE_URL и BRUNOHOST_SHARED_ENABLED.")
        names = [m._meta.db_table for m in (SharedBooking, SharedFile, SharedMember, SharedRecord)]
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                bind_space(uuid4())
                cursor.execute(
                    """
                    SELECT c.relname FROM pg_class c JOIN pg_policy p ON p.polrelid = c.oid
                    WHERE c.oid = ANY(ARRAY(SELECT to_regclass(n) FROM unnest(%s::text[]) AS n))
                      AND c.relrowsecurity AND c.relforcerowsecurity AND p.polname = 'bh_scope'
                      AND p.polqual IS NOT NULL AND p.polwithcheck IS NOT NULL""",
                    [names],
                )
                if {r[0] for r in cursor.fetchall()} != set(names):
                    raise CommandError("Политики изоляции BrunoHost отсутствуют или выключены.")
                cursor.execute(
                    """SELECT EXISTS(SELECT FROM pg_constraint WHERE conrelid=to_regclass(%s)
                               AND conname='bh_no_overlap' AND contype='x' AND convalidated)""",
                    [names[0]],
                )
                if not cursor.fetchone()[0]:
                    raise CommandError("Ограничение пересечения брони отсутствует.")
        except QwenOutputError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            "PostgreSQL, принудительная изоляция строк, защита от двойной брони и адрес настроены."
        )
