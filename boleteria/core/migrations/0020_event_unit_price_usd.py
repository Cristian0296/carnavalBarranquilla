from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_normalize_profile_identity_digits"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="unit_price_usd",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("1.00"),
                max_digits=10,
                validators=[MinValueValidator(Decimal("0.01"))],
            ),
        ),
    ]
