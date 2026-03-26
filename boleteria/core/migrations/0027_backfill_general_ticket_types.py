from django.db import migrations


def backfill_general_ticket_types(apps, schema_editor):
    Event = apps.get_model("core", "Event")
    EventTicketType = apps.get_model("core", "EventTicketType")
    Order = apps.get_model("core", "Order")
    Ticket = apps.get_model("core", "Ticket")

    general_ids_by_event = {}
    for event in Event.objects.all().iterator():
        ticket_type, _ = EventTicketType.objects.get_or_create(
            event_id=event.pk,
            code="general",
            defaults={
                "name": "General",
                "price_usd": event.unit_price_usd,
                "stock_total": event.ticket_limit,
                "is_active": True,
                "display_order": 1,
                "number_prefix": "G",
            },
        )
        general_ids_by_event[event.pk] = ticket_type.pk

    for event_id, ticket_type_id in general_ids_by_event.items():
        Order.objects.filter(event_id=event_id, ticket_type__isnull=True).update(ticket_type_id=ticket_type_id)
        Ticket.objects.filter(event_id=event_id, ticket_type__isnull=True).update(ticket_type_id=ticket_type_id)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_eventtickettype_alter_validationlog_options_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_general_ticket_types, noop_reverse),
    ]
