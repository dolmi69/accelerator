from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('founder', '0016_lab_module_label')]
    operations = [migrations.AddField(
        model_name='labpublication', name='visibility',
        field=models.CharField(choices=[('private', 'Приватно'), ('public', 'Для всех')],
            default='public', max_length=10),
    )]
