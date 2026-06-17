from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0032_product_order_and_pickup_event_nullable"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="product",
            name="delivery_mode",
        ),
    ]
