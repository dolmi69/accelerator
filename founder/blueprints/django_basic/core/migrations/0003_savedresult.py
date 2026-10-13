import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0002_ready_modules'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='SavedResult', fields=[
        ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
        ('title', models.CharField(max_length=120)),
        ('content', models.TextField(max_length=1800)),
        ('nonce', models.UUIDField()),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='saved_results', to=settings.AUTH_USER_MODEL)),
    ], options={'ordering': ['-created_at', '-pk'], 'constraints': [models.UniqueConstraint(fields=('owner', 'nonce'), name='result_nonce')]})]
