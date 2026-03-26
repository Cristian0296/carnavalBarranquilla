from datetime import datetime, timezone

from django.core.management.base import BaseCommand

from core.models import Event


class Command(BaseCommand):
    help = "Seed two active demo events for local MVP usage."

    def handle(self, *args, **options):
        seeds = [
            {
                "title": "Simulacro Tech Summit 2026",
                "datetime": datetime(2026, 3, 15, 15, 0, tzinfo=timezone.utc),
            },
            {
                "title": "Simulacro Music Night 2026",
                "datetime": datetime(2026, 4, 20, 1, 0, tzinfo=timezone.utc),
            },
        ]

        created = 0
        for item in seeds:
            _, was_created = Event.objects.get_or_create(
                title=item["title"],
                defaults={
                    "datetime": item["datetime"],
                    "status": Event.Status.ACTIVE,
                },
            )
            if was_created:
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Event seeding finished. Created {created} event(s), total active: "
                f"{Event.objects.filter(status=Event.Status.ACTIVE).count()}."
            )
        )
