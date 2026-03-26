from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_fix_report_notifications_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="end_datetime",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
