from django.db import migrations, models
import django.db.models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_event_ticket_limit"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="raffle_number",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="ticket",
            constraint=models.UniqueConstraint(
                condition=django.db.models.Q(("raffle_number__isnull", False)),
                fields=("event", "raffle_number"),
                name="unique_raffle_number_per_event",
            ),
        ),
    ]
