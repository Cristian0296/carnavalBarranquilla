from django.contrib import admin

from .models import (
    Cart,
    CartItem,
    Event,
    MomentBlock,
    MomentMedia,
    Order,
    OrderItem,
    Profile,
    Product,
    ProductRedemption,
    ProductVariant,
    Review,
    SiteSettings,
    Ticket,
    ValidationLog,
)


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


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "item_name", "variant_name", "quantity", "total_usd", "created_at")
    search_fields = ("item_name", "order__user__username", "order__event__title")


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "event", "status", "updated_at", "created_at")
    list_filter = ("status", "event")
    search_fields = ("user__username", "user__email", "event__title")


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("id", "cart", "item_type", "ticket_type", "product_variant", "quantity", "unit_price_usd", "created_at")
    search_fields = ("cart__user__username", "ticket_type__name", "product_variant__name", "product_variant__product__name")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "event", "price_usd", "has_variants", "is_active", "updated_at")
    list_filter = ("is_active", "has_variants", "event")
    search_fields = ("name", "description", "event__title")


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "name", "stock_total", "is_active", "updated_at")
    list_filter = ("is_active", "product__event")
    search_fields = ("name", "product__name", "product__event__title")


@admin.register(ProductRedemption)
class ProductRedemptionAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "order", "user", "event", "status", "delivered_at", "created_at")
    list_filter = ("status", "event")
    search_fields = ("code", "user__username", "user__email", "event__title")


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


@admin.register(MomentBlock)
class MomentBlockAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "is_active", "display_order", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("title", "description")


@admin.register(MomentMedia)
class MomentMediaAdmin(admin.ModelAdmin):
    list_display = ("id", "block", "media_type", "display_order", "created_at")
    list_filter = ("media_type",)
    search_fields = ("block__title",)


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ("id", "updated_at")
