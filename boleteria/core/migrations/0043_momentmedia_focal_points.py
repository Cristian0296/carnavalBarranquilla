from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0042_alter_order_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="momentmedia",
            name="focal_point_x",
            field=models.FloatField(default=50.0),
        ),
        migrations.AddField(
            model_name="momentmedia",
            name="focal_point_y",
            field=models.FloatField(default=50.0),
        ),
    ]
