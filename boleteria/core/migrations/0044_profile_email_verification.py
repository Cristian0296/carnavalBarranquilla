from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0043_momentmedia_focal_points"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="email_verified",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="profile",
            name="email_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
