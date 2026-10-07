import secrets
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from core.models import Membership, SiteControl


class Command(BaseCommand):
    help = 'Prepare the private one-time owner link. Never includes accelerator credentials.'

    def handle(self,*args,**options):
        control,_ = SiteControl.objects.get_or_create(pk=1)
        path = settings.BASE_DIR / '.owner-setup'
        if Membership.objects.filter(role='owner').exists() or get_user_model().objects.filter(is_superuser=True).exists():
            control.setup_claimed=True; control.save(update_fields=['setup_claimed'])
        if control.setup_claimed:
            path.unlink(missing_ok=True)
        else:
            if not path.exists():
                with path.open('x') as file:
                    file.write(secrets.token_urlsafe(32))
                path.chmod(0o600)
            self.stdout.write('Ссылка владельца подготовлена. Откройте /setup/<содержимое .owner-setup>/ на своём сайте.')
