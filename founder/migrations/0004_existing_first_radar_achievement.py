from django.db import migrations
from django.db.models import Min


def award_existing_first_radars(apps, schema_editor):
    Metrics = apps.get_model("founder", "StartupMetrics")
    Achievement = apps.get_model("founder", "StartupAchievement")
    alias = schema_editor.connection.alias
    first_radars = (
        Metrics.objects.using(alias).filter(source="ai").order_by()
        .values("startup_id").annotate(first_at=Min("assessed_at"))
    )
    for item in first_radars.iterator():
        Achievement.objects.using(alias).get_or_create(
            startup_id=item["startup_id"], code="first_radar",
            defaults={"earned_at": item["first_at"]},
        )


class Migration(migrations.Migration):
    dependencies = [("founder", "0003_chatsession_focus_axis_and_more")]
    operations = [migrations.RunPython(award_existing_first_radars, migrations.RunPython.noop)]
