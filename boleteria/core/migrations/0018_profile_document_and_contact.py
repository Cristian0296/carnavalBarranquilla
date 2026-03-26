from django.conf import settings
from django.db import migrations, models


def backfill_profile_identity_fields(apps, schema_editor):
    Profile = apps.get_model("core", "Profile")
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)

    for user in User.objects.all().iterator():
        profile, _ = Profile.objects.get_or_create(
            user_id=user.pk,
            defaults={
                "display_name": "",
                "bio": "",
            },
        )
        update_fields = []
        if not (profile.document_number or "").strip():
            profile.document_number = f"DOC-{user.pk:08d}"
            update_fields.append("document_number")
        if not (profile.contact_number or "").strip():
            profile.contact_number = str(3000000000 + user.pk)
            update_fields.append("contact_number")
        if update_fields:
            profile.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_event_end_datetime"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="contact_number",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="profile",
            name="document_number",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.RunPython(backfill_profile_identity_fields, migrations.RunPython.noop),
    ]
