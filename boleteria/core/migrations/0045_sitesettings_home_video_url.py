from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0044_profile_email_verification"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="home_video_url",
            field=models.URLField(blank=True),
        ),
    ]
