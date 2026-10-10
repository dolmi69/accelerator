from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('founder', '0018_selectable_base_features')]
    operations = [
        migrations.AddField(
            model_name='labsiteversion', name='bruno_report',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
