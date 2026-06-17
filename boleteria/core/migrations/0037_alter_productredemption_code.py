import core.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0036_productvariant_productredemption_and_item_support"),
    ]

    operations = [
        migrations.AlterField(
            model_name="productredemption",
            name="code",
            field=models.CharField(default=core.models._generate_product_redemption_code, max_length=20, unique=True),
        ),
    ]
