import os

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update django.contrib.sites Site using environment variables."

    def handle(self, *args, **options):
        if not apps.is_installed("django.contrib.sites"):
            raise CommandError(
                "django.contrib.sites is not installed. Enable Google auth first."
            )

        domain = os.getenv("DJANGO_SITE_DOMAIN", "").strip()
        if not domain:
            raise CommandError("DJANGO_SITE_DOMAIN is required.")

        name = os.getenv("DJANGO_SITE_NAME", domain).strip() or domain

        Site = apps.get_model("sites", "Site")
        site, _ = Site.objects.get_or_create(id=settings.SITE_ID)
        site.domain = domain
        site.name = name
        site.save(update_fields=["domain", "name"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Site id={settings.SITE_ID} configured with domain='{domain}' and name='{name}'."
            )
        )
