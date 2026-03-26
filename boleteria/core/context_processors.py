from django.conf import settings

from .models import Notification


def notifications_context(request):
    count = 0
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        count = Notification.objects.filter(user=user, is_read=False).count()
    return {
        "unread_notifications_count": count,
        "enable_google_auth": getattr(settings, "ENABLE_GOOGLE_AUTH", False),
    }
