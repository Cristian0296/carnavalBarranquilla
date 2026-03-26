from django.contrib import admin

from .models import Event, Order, Profile, Review, Ticket, ValidationLog


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "category",
        "location",
        "organizer",
        "age_rating",
        "unit_price_usd",
        "datetime",
        "end_datetime",
        "status",
        "created_at",
    )
    list_filter = ("status", "age_rating", "category")
    search_fields = ("title", "description", "location", "organizer", "category")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "event", "quantity", "unit_price_usd", "total_usd", "status", "created_at")
    list_filter = ("status", "event")
    search_fields = ("user__username", "user__email", "event__title")


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket_uuid", "event", "status", "issued_at", "used_at")
    list_filter = ("status", "event")
    search_fields = ("ticket_uuid", "order__user__username", "order__user__email")


@admin.register(ValidationLog)
class ValidationLogAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "admin", "outcome", "validated_at", "detail")
    list_filter = ("outcome", "validated_at")
    search_fields = ("ticket__ticket_uuid", "admin__username", "detail")


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "display_name")
    search_fields = ("user__username", "display_name")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("id", "event", "user", "updated_at")
    list_filter = ("event", "updated_at")
    search_fields = ("event__title", "user__username", "comment")
