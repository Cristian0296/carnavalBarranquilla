from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0030_product_delivery_mode_and_message"),
    ]

    operations = [
        migrations.AddField(
            model_name="productpickup",
            name="resolution_method",
            field=models.CharField(
                blank=True,
                choices=[
                    ("EVENT_PICKUP", "Entregado en evento"),
                    ("COORDINATED", "Entregado por coordinacion"),
                    ("MANUAL", "Entrega manual"),
                ],
                max_length=24,
            ),
        ),
    ]
