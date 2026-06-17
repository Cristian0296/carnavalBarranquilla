from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_remove_product_delivery_mode"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="cartitem",
            name="unique_cart_product_item",
        ),
        migrations.RemoveConstraint(
            model_name="cartitem",
            name="cart_item_matches_selected_target",
        ),
        migrations.RemoveConstraint(
            model_name="orderitem",
            name="order_item_matches_selected_target",
        ),
        migrations.RunSQL(
            sql=[
                "DELETE FROM core_productpickuplog;",
                "DELETE FROM core_productpickup;",
                "DELETE FROM core_cartitem WHERE item_type = 'PRODUCT' OR ticket_type_id IS NULL;",
                "DELETE FROM core_orderitem WHERE item_type = 'PRODUCT' OR ticket_type_id IS NULL;",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name="cartitem",
            name="item_type",
            field=models.CharField(
                choices=[("TICKET", "Boleta")],
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="orderitem",
            name="item_type",
            field=models.CharField(
                choices=[("TICKET", "Boleta")],
                max_length=16,
            ),
        ),
        migrations.RemoveField(
            model_name="cartitem",
            name="product",
        ),
        migrations.RemoveField(
            model_name="orderitem",
            name="product",
        ),
        migrations.AddConstraint(
            model_name="cartitem",
            constraint=models.CheckConstraint(
                condition=models.Q(item_type="TICKET", ticket_type__isnull=False),
                name="cart_item_matches_selected_target",
            ),
        ),
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.CheckConstraint(
                condition=models.Q(item_type="TICKET", ticket_type__isnull=False),
                name="order_item_matches_selected_target",
            ),
        ),
        migrations.DeleteModel(
            name="ProductPickupLog",
        ),
        migrations.DeleteModel(
            name="ProductPickup",
        ),
        migrations.DeleteModel(
            name="Product",
        ),
    ]
