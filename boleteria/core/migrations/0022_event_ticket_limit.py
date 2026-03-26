from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0021_order_pricing_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="ticket_limit",
            field=models.PositiveIntegerField(
                default=100,
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
    ]
