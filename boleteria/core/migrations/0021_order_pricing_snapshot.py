from decimal import Decimal

from django.db import migrations, models


def backfill_order_pricing(apps, schema_editor):
    Order = apps.get_model("core", "Order")
    Ticket = apps.get_model("core", "Ticket")

    ticket_counts = {}
    for row in Ticket.objects.values("order_id"):
        order_id = row["order_id"]
        ticket_counts[order_id] = ticket_counts.get(order_id, 0) + 1

    for order in Order.objects.select_related("event").all().iterator():
        quantity = ticket_counts.get(order.id, 1) or 1
        unit_price = getattr(order.event, "unit_price_usd", Decimal("1.00")) or Decimal("1.00")
        total = (Decimal(unit_price) * Decimal(quantity)).quantize(Decimal("0.01"))
        order.quantity = quantity
        order.unit_price_usd = unit_price
        order.total_usd = total
        order.save(update_fields=["quantity", "unit_price_usd", "total_usd"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_event_unit_price_usd"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="quantity",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="order",
            name="total_usd",
            field=models.DecimalField(decimal_places=2, default=Decimal("1.00"), max_digits=10),
        ),
        migrations.AddField(
            model_name="order",
            name="unit_price_usd",
            field=models.DecimalField(decimal_places=2, default=Decimal("1.00"), max_digits=10),
        ),
        migrations.RunPython(backfill_order_pricing, migrations.RunPython.noop),
    ]
