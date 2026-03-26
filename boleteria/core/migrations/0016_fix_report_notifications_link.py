from django.db import migrations


def fix_report_notifications_link(apps, schema_editor):
    Notification = apps.get_model("core", "Notification")
    Notification.objects.filter(title="Nuevo reporte de comentario").update(
        link_url="/staff/reports/"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_notification_link_url"),
    ]

    operations = [
        migrations.RunPython(fix_report_notifications_link, migrations.RunPython.noop),
    ]
