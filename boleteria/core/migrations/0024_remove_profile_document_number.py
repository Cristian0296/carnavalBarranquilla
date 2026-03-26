from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_ticket_raffle_number"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="profile",
            name="document_number",
        ),
    ]

