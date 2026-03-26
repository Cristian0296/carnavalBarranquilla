import uuid
from decimal import Decimal
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Max, Q
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
        PAID = "PAID", "Pagada"
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
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PAID,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Order #{self.pk} - {self.user} - {self.event.title}"


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


