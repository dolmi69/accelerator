from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("founder", "0018_selectable_base_features"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="pro_until",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Pro до"),
        ),
    ]
