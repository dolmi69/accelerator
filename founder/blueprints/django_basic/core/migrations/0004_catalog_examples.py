from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('core', '0003_savedresult'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name='sitecontrol', name='catalog_examples_initialized', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='sitecontrol', name='catalog_example_owner', field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
    ]
