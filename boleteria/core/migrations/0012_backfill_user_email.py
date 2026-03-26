import re

from django.db import migrations


def _slugify_username(username):
    value = (username or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", ".", value)
    value = re.sub(r"\.+", ".", value).strip(".")
    return value or "usuario"


def backfill_missing_emails(apps, schema_editor):
    User = apps.get_model("auth", "User")
    existing = {email.lower() for email in User.objects.exclude(email="").values_list("email", flat=True)}

    for user in User.objects.filter(email="").iterator():
        base = _slugify_username(user.username)
        candidate = f"{base}@ejemplo.com"
        index = 2
        while candidate.lower() in existing:
            candidate = f"{base}{index}@ejemplo.com"
            index += 1
        user.email = candidate
        user.save(update_fields=["email"])
        existing.add(candidate.lower())


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_review_parent"),
    ]

    operations = [
        migrations.RunPython(backfill_missing_emails, migrations.RunPython.noop),
    ]
