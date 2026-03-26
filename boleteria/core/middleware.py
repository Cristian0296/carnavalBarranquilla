from datetime import datetime

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

