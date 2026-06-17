from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_default_unit_variants(apps, schema_editor):
    product_model = apps.get_model("core", "Product")
    product_variant_model = apps.get_model("core", "ProductVariant")
    for product in product_model.objects.filter(has_variants=False):
        product_variant_model.objects.get_or_create(
            product=product,
            name="Unidad",
            defaults={"stock_total": 0, "is_active": product.is_active},
        )


def assign_redemption_codes(apps, schema_editor):
    redemption_model = apps.get_model("core", "ProductRedemption")
    for redemption in redemption_model.objects.filter(code="PENDING-CODE"):
        redemption.code = f"PROD-{str(redemption.pk).zfill(6)}"
        redemption.save(update_fields=["code"])


def generate_product_redemption_code():
    import uuid

    return f"PROD-{uuid.uuid4().hex[:6].upper()}"


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0035_product"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductVariant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("stock_total", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="variants",
                        to="core.product",
                    ),
                ),
            ],
            options={
                "ordering": ["product_id", "name", "id"],
            },
        ),
        migrations.CreateModel(
            name="ProductRedemption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(default=generate_product_redemption_code, max_length=20, unique=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("PENDING", "Pendiente"), ("DELIVERED", "Entregado")],
                        default="PENDING",
                        max_length=16,
                    ),
                ),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "delivered_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="delivered_product_redemptions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "event",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="product_redemptions",
                        to="core.event",
                    ),
                ),
                (
                    "order",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="product_redemption",
                        to="core.order",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="product_redemptions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddField(
            model_name="cartitem",
            name="product_variant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cart_items",
                to="core.productvariant",
            ),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="product_variant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="order_items",
                to="core.productvariant",
            ),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="variant_name",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AlterField(
            model_name="cartitem",
            name="item_type",
            field=models.CharField(
                choices=[("TICKET", "Boleta"), ("PRODUCT", "Producto")],
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="orderitem",
            name="item_type",
            field=models.CharField(
                choices=[("TICKET", "Boleta"), ("PRODUCT", "Producto")],
                max_length=16,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="cartitem",
            name="cart_item_matches_selected_target",
        ),
        migrations.RemoveConstraint(
            model_name="orderitem",
            name="order_item_matches_selected_target",
        ),
        migrations.AddConstraint(
            model_name="productvariant",
            constraint=models.UniqueConstraint(
                fields=("product", "name"),
                name="unique_variant_name_per_product",
            ),
        ),
        migrations.AddConstraint(
            model_name="cartitem",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("item_type", "TICKET"), ("product_variant__isnull", True), ("ticket_type__isnull", False))
                    | models.Q(("item_type", "PRODUCT"), ("product_variant__isnull", False), ("ticket_type__isnull", True))
                ),
                name="cart_item_matches_selected_target",
            ),
        ),
        migrations.AddConstraint(
            model_name="cartitem",
            constraint=models.UniqueConstraint(
                condition=models.Q(("product_variant__isnull", False)),
                fields=("cart", "product_variant"),
                name="unique_cart_product_variant_item",
            ),
        ),
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("item_type", "TICKET"), ("product_variant__isnull", True), ("ticket_type__isnull", False))
                    | models.Q(("item_type", "PRODUCT"), ("product_variant__isnull", False), ("ticket_type__isnull", True))
                ),
                name="order_item_matches_selected_target",
            ),
        ),
        migrations.RunPython(create_default_unit_variants, migrations.RunPython.noop),
        migrations.RunPython(assign_redemption_codes, migrations.RunPython.noop),
    ]
