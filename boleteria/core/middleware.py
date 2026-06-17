from datetime import datetime

from django.utils.cache import add_never_cache_headers, patch_vary_headers
from django.utils import timezone


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
