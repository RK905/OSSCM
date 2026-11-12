from django.db import migrations


def install(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute('''
      CREATE FUNCTION osscm_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'OSSCM ledger and audit records are append-only'; END; $$;
      CREATE TRIGGER movement_immutable BEFORE UPDATE OR DELETE ON core_movement
        FOR EACH ROW EXECUTE FUNCTION osscm_append_only();
      CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON core_auditevent
        FOR EACH ROW EXECUTE FUNCTION osscm_append_only();
    ''')


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('DROP TRIGGER movement_immutable ON core_movement; DROP TRIGGER audit_immutable ON core_auditevent; DROP FUNCTION osscm_append_only();')


class Migration(migrations.Migration):
    dependencies = [('core', '0001_initial')]
    operations = [migrations.RunPython(install, uninstall)]
