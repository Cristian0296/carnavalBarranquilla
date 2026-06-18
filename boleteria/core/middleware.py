from datetime import datetime

from django.utils.cache import add_never_cache_headers, patch_vary_headers
from django.utils import timezone, translation


PUBLIC_LANGUAGE_COOKIE_NAME = "site_language"
PUBLIC_LANGUAGE_CODES = {"es", "en"}


class PublicLanguageMiddleware:
    """
    Keep admin/staff flows in Spanish while allowing public pages to render
    in English or Spanish for visitors and regular users.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language_code = "es"
        user = getattr(request, "user", None)
        is_internal_user = bool(
            user
            and user.is_authenticated
            and (user.is_staff or user.has_perm("core.can_validate_tickets"))
        )

        if not request.path.startswith("/admin/") and not request.path.startswith("/staff/") and not is_internal_user:
            cookie_language = (request.COOKIES.get(PUBLIC_LANGUAGE_COOKIE_NAME) or "").strip().lower()
            header_language = (request.headers.get("Accept-Language") or "").lower()
            if cookie_language in PUBLIC_LANGUAGE_CODES:
                language_code = cookie_language
            elif header_language.startswith("en"):
                language_code = "en"

        translation.activate(language_code)
        request.LANGUAGE_CODE = language_code
        response = self.get_response(request)
        response.headers["Content-Language"] = language_code
        translation.deactivate()
        return response


class SystemTimezoneMiddleware:
    """
    Activate the host system timezone on each request.
    This is useful for local deployments where server and user share the machine.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        system_tz = datetime.now().astimezone().tzinfo
        if system_tz is not None:
            timezone.activate(system_tz)
        response = self.get_response(request)
        timezone.deactivate()
        return response


class DisableHtmlCacheMiddleware:
    """
    Prevent browsers from reusing stale HTML pages that may have been rendered
    before login/logout, especially when navigating back and forward.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.get("Content-Type", "")
        if "text/html" in content_type:
            add_never_cache_headers(response)
            patch_vary_headers(response, ("Cookie",))
        return response
