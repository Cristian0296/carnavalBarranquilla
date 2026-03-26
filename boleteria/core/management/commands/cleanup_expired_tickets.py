from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Ticket


class Command(BaseCommand):
    help = "Delete tickets 45 days after raffle end date."

    def handle(self, *args, **options):
        now = timezone.now()
        ticket_ids_to_delete = []

        tickets = Ticket.objects.select_related("event").only(
            "id",
            "event__datetime",
            "event__end_datetime",
        )
        for ticket in tickets:
            cleanup_at = ticket.event.cleanup_at
            if cleanup_at and cleanup_at <= now:
                ticket_ids_to_delete.append(ticket.id)

        deleted_count = 0
        if ticket_ids_to_delete:
            deleted_count, _ = Ticket.objects.filter(id__in=ticket_ids_to_delete).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Cleanup complete. Deleted {deleted_count} expired ticket(s)."
            )
        )
