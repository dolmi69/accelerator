from django.core.management.base import BaseCommand
from founder.services.lab_jobs import run


class Command(BaseCommand):
    help = 'Execute one durable laboratory job (safe to deliver more than once).'

    def add_arguments(self, parser):
        parser.add_argument('job_id')

    def handle(self, *args, **options):
        run(options['job_id'])
