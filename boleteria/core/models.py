import uuid
from decimal import Decimal
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Max, Q, Sum
from django.core.validators import MinValueValidator
from django.utils import timezone


class Event(models.Model):
    class AgeRating(models.TextChoices):
        ALL = "ALL", "Toda la familia"
        PLUS_18 = "18+", "Mayores de edad"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente de aprobacion"
        ACTIVE = "ACTIVE", "Activo"
        INACTIVE = "INACTIVE", "Inactivo"

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=200, blank=True)
    organizer = models.CharField(max_length=200, blank=True)
    category = models.CharField(max_length=100, blank=True)
    unit_price_usd = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    ticket_limit = models.PositiveIntegerField(
        default=100,
        validators=[MinValueValidator(1)],
    )
    age_rating = models.CharField(
        max_length=8,
        choices=AgeRating.choices,
        default=AgeRating.ALL,
    )
    datetime = models.DateTimeField()
    end_datetime = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    buyer_image = models.ImageField(upload_to="events/purchased/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_events",
        null=True,
        blank=True,
    )

    def ensure_general_ticket_type(self):
        defaults = {
            "name": "General",
            "price_usd": self.unit_price_usd,
            "stock_total": self.ticket_limit,
            "is_active": True,
            "display_order": 1,
            "number_prefix": "G",
        }
        ticket_type, created = self.ticket_types.get_or_create(
            code=EventTicketType.Code.GENERAL,
            defaults=defaults,
        )
        if not created:
            updated_fields = []
            if ticket_type.name != defaults["name"]:
                ticket_type.name = defaults["name"]
                updated_fields.append("name")
            if ticket_type.number_prefix != defaults["number_prefix"]:
                ticket_type.number_prefix = defaults["number_prefix"]
                updated_fields.append("number_prefix")
            if ticket_type.display_order != defaults["display_order"]:
                ticket_type.display_order = defaults["display_order"]
                updated_fields.append("display_order")
            if updated_fields:
                ticket_type.save(update_fields=updated_fields)
        return ticket_type

    def __str__(self):
        return f"{self.title} ({self.datetime:%Y-%m-%d %H:%M})"

    @property
    def end_at(self):
        return self.end_datetime or self.datetime

    @property
    def cleanup_at(self):
        if self.end_datetime is None:
            return None
        return self.end_datetime + timedelta(days=45)

    def has_finished(self, at=None):
        now = at or timezone.now()
        if self.end_datetime is None:
            return False
        return now > self.end_datetime

    def has_started(self, at=None):
        now = at or timezone.now()
        return now >= self.datetime

    @property
    def sold_tickets_count(self):
        return self.tickets.count()

    @property
    def remaining_tickets_count(self):
        return max(self.ticket_limit - self.sold_tickets_count, 0)

    @property
    def raffle_number_width(self):
        return max(1, len(str(self.ticket_limit)))


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente de pago"
        PAID = "PAID", "Pagada"
        FAILED = "FAILED", "Pago fallido"
        CANCELED = "CANCELED", "Pago cancelado"
        REFUNDED = "REFUNDED", "Reembolsada"
        VOID = "VOID", "Anulada"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="orders",
        null=True,
        blank=True,
    )
    ticket_type = models.ForeignKey(
        "EventTicketType",
        on_delete=models.PROTECT,
        related_name="orders",
        null=True,
        blank=True,
    )
    unit_price_usd = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1.00"))
    quantity = models.PositiveIntegerField(default=1)
    total_usd = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1.00"))
    payment_provider = models.CharField(max_length=32, blank=True)
    payment_status_detail = models.CharField(max_length=120, blank=True)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True)
    payment_confirmed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        event_label = self.event.title if self.event_id and self.event else "Sin evento"
        return f"Order #{self.pk} - {self.user} - {event_label}"

    @property
    def is_payment_final(self):
        return self.status in {
            self.Status.PAID,
            self.Status.FAILED,
            self.Status.CANCELED,
            self.Status.REFUNDED,
            self.Status.VOID,
        }


class Ticket(models.Model):
    class Status(models.TextChoices):
        UNUSED = "UNUSED", "Disponible"
        USED = "USED", "Usada"
        VOID = "VOID", "Anulada"

    ticket_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="tickets",
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="tickets",
    )
    ticket_type = models.ForeignKey(
        "EventTicketType",
        on_delete=models.PROTECT,
        related_name="tickets",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.UNUSED,
    )
    raffle_number = models.PositiveIntegerField(null=True, blank=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)
    token_ref = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ticket_type", "raffle_number"],
                condition=Q(raffle_number__isnull=False),
                name="unique_raffle_number_per_ticket_type",
            )
        ]

    def __str__(self):
        return f"Ticket {self.ticket_uuid} ({self.status})"

    @property
    def raffle_number_display(self):
        width = 1
        prefix = ""
        if self.ticket_type_id and self.ticket_type:
            width = self.ticket_type.raffle_number_width
            prefix = (self.ticket_type.number_prefix or "").strip()
        elif self.event_id and self.event:
            width = self.event.raffle_number_width
        if self.raffle_number is None:
            return ""
        number = str(self.raffle_number).zfill(width)
        return f"{prefix}-{number}" if prefix else number

    def save(self, *args, **kwargs):
        if self.ticket_type_id is None:
            if self.order_id and self.order and self.order.ticket_type_id:
                self.ticket_type = self.order.ticket_type
            elif self.event_id:
                self.ticket_type = self.event.ensure_general_ticket_type()
        if self.raffle_number is None and self.event_id:
            ticket_type_id = self.ticket_type_id
            row = (
                Ticket.objects.filter(ticket_type_id=ticket_type_id)
                .exclude(pk=self.pk)
                .aggregate(max_number=Max("raffle_number"))
            )
            current_max = row.get("max_number")
            next_number = 1 if current_max is None else current_max + 1
            stock_total = None
            if self.ticket_type_id and self.ticket_type:
                stock_total = self.ticket_type.stock_total
            elif self.event_id:
                stock_total = Event.objects.filter(pk=self.event_id).values_list("ticket_limit", flat=True).first()
            if stock_total is not None and next_number > stock_total:
                raise ValueError("There are no more raffle numbers available for this ticket type.")
            self.raffle_number = next_number
        super().save(*args, **kwargs)


class EventTicketType(models.Model):
    class Code(models.TextChoices):
        GENERAL = "general", "General"
        VIP = "vip", "VIP"

    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="ticket_types",
    )
    code = models.CharField(max_length=32, choices=Code.choices)
    name = models.CharField(max_length=80)
    price_usd = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    stock_total = models.PositiveIntegerField(
        default=100,
        validators=[MinValueValidator(1)],
    )
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=1)
    number_prefix = models.CharField(max_length=16, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "code"],
                name="unique_ticket_type_code_per_event",
            )
        ]

    def __str__(self):
        return f"{self.event.title} - {self.name}"

    @property
    def sold_tickets_count(self):
        return self.tickets.count()

    @property
    def remaining_tickets_count(self):
        return max(self.stock_total - self.sold_tickets_count, 0)

    @property
    def raffle_number_width(self):
        return max(1, len(str(self.stock_total)))


class Product(models.Model):
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="products",
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="event_products/", blank=True)
    price_usd = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    is_active = models.BooleanField(default=True)
    has_variants = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return f"{self.event.title} - {self.name}"

    @property
    def active_variants(self):
        return self.variants.filter(is_active=True)


class ProductVariant(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    name = models.CharField(max_length=120)
    stock_total = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["product_id", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "name"],
                name="unique_variant_name_per_product",
            )
        ]

    def __str__(self):
        return f"{self.product.name} - {self.name}"

    @property
    def sold_quantity(self):
        return (
            self.order_items.filter(
                item_type="PRODUCT",
                order__status=Order.Status.PAID,
            ).aggregate(total=Sum("quantity")).get("total")
            or 0
        )

    @property
    def remaining_stock(self):
        return max(self.stock_total - self.sold_quantity, 0)


class Cart(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Activo"
        CONVERTED = "CONVERTED", "Convertido"
        ABANDONED = "ABANDONED", "Abandonado"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="carts",
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="carts",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status="ACTIVE"),
                name="unique_active_cart_per_user",
            )
        ]

    def __str__(self):
        return f"Cart #{self.pk} - {self.user}"

    @property
    def total_usd(self):
        total = Decimal("0.00")
        for item in self.items.all():
            total += item.subtotal_usd
        return total


class CartItem(models.Model):
    class ItemType(models.TextChoices):
        TICKET = "TICKET", "Boleta"
        PRODUCT = "PRODUCT", "Producto"

    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name="items",
    )
    item_type = models.CharField(max_length=16, choices=ItemType.choices)
    ticket_type = models.ForeignKey(
        EventTicketType,
        on_delete=models.PROTECT,
        related_name="cart_items",
        null=True,
        blank=True,
    )
    product_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="cart_items",
        null=True,
        blank=True,
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)], default=1)
    unit_price_usd = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(item_type="TICKET", ticket_type__isnull=False, product_variant__isnull=True)
                    | Q(item_type="PRODUCT", ticket_type__isnull=True, product_variant__isnull=False)
                ),
                name="cart_item_matches_selected_target",
            ),
            models.UniqueConstraint(
                fields=["cart", "ticket_type"],
                condition=Q(ticket_type__isnull=False),
                name="unique_cart_ticket_type_item",
            ),
            models.UniqueConstraint(
                fields=["cart", "product_variant"],
                condition=Q(product_variant__isnull=False),
                name="unique_cart_product_variant_item",
            ),
        ]

    def __str__(self):
        return f"CartItem #{self.pk} - {self.item_type}"

    @property
    def event(self):
        if self.ticket_type_id and self.ticket_type:
            return self.ticket_type.event
        if self.product_variant_id and self.product_variant:
            return self.product_variant.product.event
        return None

    @property
    def item_name(self):
        if self.ticket_type_id and self.ticket_type:
            return f"{self.ticket_type.event.title} - {self.ticket_type.name}"
        if self.product_variant_id and self.product_variant:
            return f"{self.product_variant.product.event.title} - {self.product_variant.product.name}"
        return ""

    @property
    def subtotal_usd(self):
        return self.unit_price_usd * self.quantity


class OrderItem(models.Model):
    class ItemType(models.TextChoices):
        TICKET = "TICKET", "Boleta"
        PRODUCT = "PRODUCT", "Producto"

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="order_items",
        null=True,
        blank=True,
    )
    item_type = models.CharField(max_length=16, choices=ItemType.choices)
    ticket_type = models.ForeignKey(
        EventTicketType,
        on_delete=models.PROTECT,
        related_name="order_items",
        null=True,
        blank=True,
    )
    product_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="order_items",
        null=True,
        blank=True,
    )
    item_name = models.CharField(max_length=255)
    variant_name = models.CharField(max_length=120, blank=True)
    unit_price_usd = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)], default=1)
    total_usd = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(item_type="TICKET", ticket_type__isnull=False, product_variant__isnull=True)
                    | Q(item_type="PRODUCT", ticket_type__isnull=True, product_variant__isnull=False)
                ),
                name="order_item_matches_selected_target",
            )
        ]

    def __str__(self):
        return f"OrderItem #{self.pk} - {self.item_name}"


def _generate_product_redemption_code():
    return f"PROD-{uuid.uuid4().hex[:6].upper()}"


class ProductRedemption(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        DELIVERED = "DELIVERED", "Entregado"

    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="product_redemption",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="product_redemptions",
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="product_redemptions",
        null=True,
        blank=True,
    )
    code = models.CharField(max_length=20, unique=True, default=_generate_product_redemption_code)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="delivered_product_redemptions",
        null=True,
        blank=True,
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        event_label = self.event.title if self.event_id and self.event else "Sin evento"
        return f"{self.code} - {self.user} - {event_label}"


class ValidationLog(models.Model):
    class Outcome(models.TextChoices):
        ACCEPTED = "ACCEPTED", "Accepted"
        REJECTED = "REJECTED", "Rejected"

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="validation_logs",
        null=True,
        blank=True,
    )
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="validation_logs",
        null=True,
        blank=True,
    )
    validated_at = models.DateTimeField(auto_now_add=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    detail = models.CharField(max_length=255, blank=True)

    class Meta:
        permissions = [
            ("can_validate_tickets", "Can validate ticket tokens"),
        ]

    def __str__(self):
        ticket = self.ticket.ticket_uuid if self.ticket else "no-ticket"
        return f"{self.outcome} - {ticket} - {self.validated_at:%Y-%m-%d %H:%M:%S}"


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    contact_number = models.CharField(max_length=32, blank=True)
    display_name = models.CharField(max_length=120, blank=True)
    bio = models.TextField(blank=True)
    photo = models.ImageField(upload_to="profiles/", blank=True)
    email_verified = models.BooleanField(default=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.display_name or self.user.username


class Review(models.Model):
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="event_reviews",
    )
    comment = models.TextField()
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="replies",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Review {self.event_id} - {self.user_id}"

    @property
    def author_name(self):
        try:
            display_name = (self.user.profile.display_name or "").strip()
            return display_name or self.user.username
        except Exception:
            return self.user.username

    @property
    def author_photo_url(self):
        try:
            return self.user.profile.photo.url if self.user.profile.photo else ""
        except Exception:
            return ""


class ReviewReaction(models.Model):
    class Reaction(models.TextChoices):
        LIKE = "LIKE", "Like"
        DISLIKE = "DISLIKE", "No Like"

    review = models.ForeignKey(
        Review,
        on_delete=models.CASCADE,
        related_name="reactions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="review_reactions",
    )
    reaction = models.CharField(max_length=8, choices=Reaction.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["review", "user"],
                name="unique_review_reaction_per_user",
            )
        ]

    def __str__(self):
        return f"ReviewReaction {self.review_id} - {self.user_id} - {self.reaction}"


class EventImage(models.Model):
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField(upload_to="events/")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"EventImage {self.pk} - Event {self.event_id}"


class MomentBlock(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "-created_at", "-id"]

    def __str__(self):
        return self.title


class MomentMedia(models.Model):
    class MediaType(models.TextChoices):
        IMAGE = "IMAGE", "Imagen"
        VIDEO = "VIDEO", "Video"

    block = models.ForeignKey(
        MomentBlock,
        on_delete=models.CASCADE,
        related_name="media_items",
    )
    media_type = models.CharField(max_length=16, choices=MediaType.choices)
    file = models.FileField(upload_to="moments/")
    display_order = models.PositiveIntegerField(default=1)
    focal_point_x = models.FloatField(default=50.0)
    focal_point_y = models.FloatField(default=50.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "id"]

    def __str__(self):
        return f"{self.block.title} - {self.media_type}"


class SiteSettings(models.Model):
    whatsapp_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    facebook_url = models.URLField(blank=True)
    tiktok_url = models.URLField(blank=True)
    x_url = models.URLField(blank=True)
    telegram_url = models.URLField(blank=True)
    home_video_url = models.URLField(blank=True)
    footer_primary_text = models.TextField(blank=True)
    footer_tagline = models.CharField(max_length=160, blank=True)
    footer_copyright_text = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuracion del sitio"
        verbose_name_plural = "Configuracion del sitio"

    def __str__(self):
        return "Configuracion del sitio"

    @classmethod
    def get_solo(cls):
        settings_obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                "footer_primary_text": "Una experiencia que une arte, cultura y la alegria del Carnaval de Barranquilla en Atlanta.",
                "footer_tagline": "Vive la experiencia.",
                "footer_copyright_text": "2026 MaruVision. Todos los derechos reservados.",
            },
        )
        return settings_obj


class ReviewReport(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        OMITTED = "OMITTED", "Dismissed"

    review = models.ForeignKey(
        Review,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="submitted_review_reports",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="handled_review_reports",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["review", "reporter"],
                name="unique_review_report_per_user",
            ),
        ]

    def __str__(self):
        return f"ReviewReport {self.review_id} by {self.reporter_id}"


class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=160)
    body = models.TextField()
    link_url = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Notification {self.pk} - User {self.user_id}"


