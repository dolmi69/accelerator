from django.db import migrations

TABLES = ("sharedmember", "sharedrecord", "sharedfile", "sharedbooking")


def install(apps, editor):
    if editor.connection.vendor != "postgresql":
        return
    for table in TABLES:
        name = "founder_" + table
        editor.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
        editor.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
        predicate = "space_id = NULLIF(current_setting('brunohost.space', true), '')::uuid"
        editor.execute(f"CREATE POLICY bh_scope ON {name} USING ({predicate}) WITH CHECK ({predicate})")
        editor.execute(
            f"ALTER TABLE {name} ADD CONSTRAINT bh_space_cascade FOREIGN KEY (space_id) "
            "REFERENCES founder_sharedspace (id) ON DELETE CASCADE"
        )
    for table in ("sharedmember", "sharedrecord"):
        editor.execute(f"ALTER TABLE founder_{table} ADD UNIQUE (space_id, id)")
    for child, col, parent in [
        ("sharedrecord", "owner", "sharedmember"),
        ("sharedfile", "owner", "sharedmember"),
        ("sharedbooking", "record", "sharedrecord"),
        ("sharedbooking", "resource", "sharedrecord"),
    ]:
        editor.execute(
            f"ALTER TABLE founder_{child} ADD CONSTRAINT bh_tenant_{col} "
            f"FOREIGN KEY (space_id, {col}_id) REFERENCES founder_{parent} (space_id, id) ON DELETE CASCADE"
        )
    editor.execute(
        'CREATE INDEX bh_record_values ON founder_sharedrecord USING gin ("values" jsonb_path_ops)'
    )
    # A singleton integer range identifies one globally unique resource record.
    # Both range operators use built-in GiST classes. No extension or superuser needed.
    editor.execute("""ALTER TABLE founder_sharedbooking ADD CONSTRAINT bh_no_overlap
        EXCLUDE USING gist (int8range(resource_id, resource_id, '[]') WITH &&,
                            tstzrange(starts_at, ends_at, '[)') WITH &&)""")


def remove(apps, editor):
    if editor.connection.vendor != "postgresql":
        return
    editor.execute("DROP INDEX bh_record_values")
    editor.execute("ALTER TABLE founder_sharedbooking DROP CONSTRAINT bh_no_overlap")
    for child, col in [
        ("sharedrecord", "owner"),
        ("sharedfile", "owner"),
        ("sharedbooking", "record"),
        ("sharedbooking", "resource"),
    ]:
        editor.execute(f"ALTER TABLE founder_{child} DROP CONSTRAINT bh_tenant_{col}")
    for table in ("sharedmember", "sharedrecord"):
        editor.execute(f"ALTER TABLE founder_{table} DROP CONSTRAINT founder_{table}_space_id_id_key")
    for table in TABLES:
        name = "founder_" + table
        editor.execute(f"ALTER TABLE {name} DROP CONSTRAINT bh_space_cascade")
        editor.execute(f"DROP POLICY bh_scope ON {name}")
        editor.execute(f"ALTER TABLE {name} NO FORCE ROW LEVEL SECURITY")
        editor.execute(f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY")


class Migration(migrations.Migration):
    dependencies = [("founder", "0045_brunohost_shared")]
    operations = [migrations.RunPython(install, remove)]
