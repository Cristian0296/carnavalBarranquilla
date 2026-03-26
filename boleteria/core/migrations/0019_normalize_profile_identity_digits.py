import re

from django.db import migrations


def normalize_profile_identity_digits(apps, schema_editor):
    Profile = apps.get_model("core", "Profile")

    for profile in Profile.objects.all().iterator():
        raw_doc = (profile.document_number or "").strip()
        raw_contact = (profile.contact_number or "").strip()

        doc_digits = re.sub(r"\D+", "", raw_doc)
        contact_digits = re.sub(r"\D+", "", raw_contact)

        if not doc_digits:
            doc_digits = str(100000000 + profile.user_id)
        if not contact_digits:
            contact_digits = str(3000000000 + profile.user_id)

        update_fields = []
        if doc_digits != profile.document_number:
            profile.document_number = doc_digits
            update_fields.append("document_number")
        if contact_digits != profile.contact_number:
            profile.contact_number = contact_digits
            update_fields.append("contact_number")
        if update_fields:
            profile.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_profile_document_and_contact"),
    ]

    operations = [
        migrations.RunPython(normalize_profile_identity_digits, migrations.RunPython.noop),
    ]
