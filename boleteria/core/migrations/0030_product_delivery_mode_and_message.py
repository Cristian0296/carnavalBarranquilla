from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0029_productpickuplog"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="delivery_message",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="product",
            name="delivery_mode",
            field=models.CharField(
                choices=[
                    ("EVENT_PICKUP", "Retiro en evento"),
                    ("COORDINATED_DELIVERY", "Entrega coordinada"),
                    ("BOTH", "Retiro o entrega coordinada"),
                ],
                default="EVENT_PICKUP",
                max_length=24,
            ),
        ),
    ]
