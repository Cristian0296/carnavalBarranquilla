from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from core.models import Event, Notification, Ticket


class Command(BaseCommand):
    help = "Send raffle reminders for 8, 3, and 1 day(s) before draw date."

    def handle(self, *args, **options):
        now = timezone.now()
        sent_count = 0
        milestones = {
            8: "Faltan 8 dias para el sorteo",
            3: "Faltan 3 dias para el sorteo",
            1: "Falta 1 dia para el sorteo",
        }
        active_user_ids = set(User.objects.filter(is_active=True).values_list("id", flat=True))

        events = Event.objects.filter(status=Event.Status.ACTIVE, end_datetime__isnull=False)
        for event in events:
            if event.has_finished():
                continue
            days_remaining = (event.end_datetime.date() - now.date()).days
            if days_remaining not in milestones:
                continue

            title = milestones[days_remaining]
            link_url = f"{reverse('event_detail', args=[event.pk])}?reminder={days_remaining}d"
            participant_ids = set(
                Ticket.objects.filter(event=event)
                .values_list("order__user_id", flat=True)
                .distinct()
            )
            participant_ids &= active_user_ids
            for user_id in participant_ids:
                body = (
                    f'La obra "{event.title}" se juega el {event.end_datetime}. '
                    f"Revisa tus boletas de rifa."
                )
                exists = Notification.objects.filter(
                    user_id=user_id,
                    title=title,
                    link_url=link_url,
                ).exists()
                if exists:
                    continue
                Notification.objects.create(
                    user_id=user_id,
                    title=title,
                    body=body,
                    link_url=link_url,
                )
                sent_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Recordatorios enviados: {sent_count}"
            )
        )
