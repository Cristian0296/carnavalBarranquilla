from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0031_productpickup_resolution_method"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="event",
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.CASCADE, related_name="orders", to="core.event"),
        ),
        migrations.AlterField(
            model_name="product",
            name="event",
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.CASCADE, related_name="products", to="core.event"),
        ),
        migrations.AlterField(
            model_name="productpickup",
            name="event",
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.CASCADE, related_name="product_pickups", to="core.event"),
        ),
    ]
