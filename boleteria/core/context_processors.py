from django.conf import settings
from django.db.models import Sum

from .models import Cart, Notification, SiteSettings


def notifications_context(request):
    count = 0
    cart_items_count = 0
    site_settings = SiteSettings.objects.filter(pk=1).first()
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        count = Notification.objects.filter(user=user, is_read=False).count()
        if not user.is_staff:
            cart_items_count = (
                Cart.objects.filter(user=user, status=Cart.Status.ACTIVE)
                .aggregate(total=Sum("items__quantity"))
                .get("total")
                or 0
            )
    return {
        "unread_notifications_count": count,
        "cart_items_count": cart_items_count,
        "enable_google_auth": getattr(settings, "ENABLE_GOOGLE_AUTH", False),
        "site_social_links": {
            "whatsapp": site_settings.whatsapp_url if site_settings else "",
            "instagram": site_settings.instagram_url if site_settings else "",
            "facebook": site_settings.facebook_url if site_settings else "",
            "tiktok": site_settings.tiktok_url if site_settings else "",
            "x": site_settings.x_url if site_settings else "",
            "telegram": site_settings.telegram_url if site_settings else "",
        },
        "site_footer_content": {
            "primary_text": (
                site_settings.footer_primary_text
                if site_settings and site_settings.footer_primary_text
                else "Una experiencia que une arte, cultura y la alegria del Carnaval de Barranquilla en Atlanta."
            ),
            "tagline": (
                site_settings.footer_tagline
                if site_settings and site_settings.footer_tagline
                else "Vive la experiencia."
            ),
            "copyright_text": (
                site_settings.footer_copyright_text
                if site_settings and site_settings.footer_copyright_text
                else "2026 MaruVision. Todos los derechos reservados."
            ),
        },
    }
