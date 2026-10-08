from django.db import migrations, models


class Migration(migrations.Migration):
    """Объединяет панель акул с beta 0.6.

    У speaker появляется значение по умолчанию в самой базе: если на этой базе
    снова запустят код main без панели, вставка сообщений чата не упадёт.
    """

    dependencies = [
        ('founder', '0012_shark_panel'),
        ('founder', '0018_selectable_base_features'),
    ]

    operations = [
        migrations.AlterField(
            model_name='chatmessage',
            name='speaker',
            field=models.CharField(blank=True, db_default='', max_length=12),
        ),
    ]
