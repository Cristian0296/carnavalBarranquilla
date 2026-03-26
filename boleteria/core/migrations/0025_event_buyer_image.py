from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_remove_profile_document_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="buyer_image",
            field=models.ImageField(blank=True, upload_to="events/purchased/"),
        ),
    ]

