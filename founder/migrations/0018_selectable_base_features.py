from django.db import migrations


def preserve_existing_features(apps, schema_editor):
    Version = apps.get_model('founder', 'LabSiteVersion')
    # These features were implicit before the module checkboxes were introduced.
    for version in Version.objects.filter(kind='django').iterator():
        chosen = list(version.backend_modules)
        for key in ('registration', 'password_reset', 'chat', 'notifications'):
            if key not in chosen:
                chosen.append(key)
        Version.objects.filter(pk=version.pk).update(backend_modules=chosen)


class Migration(migrations.Migration):
    dependencies = [('founder', '0017_lab_publication_visibility')]
    operations = [migrations.RunPython(preserve_existing_features, migrations.RunPython.noop)]
