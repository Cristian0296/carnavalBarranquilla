from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User


class EmailOrUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        login_value = (username or kwargs.get("username") or "").strip()
        if not login_value or password is None:
            return None

        lookup_username = login_value
        if "@" in login_value:
            matched_user = User.objects.filter(email__iexact=login_value).first()
            if not matched_user:
                return None
            lookup_username = matched_user.get_username()

        return super().authenticate(
            request,
            username=lookup_username,
            password=password,
            **kwargs,
        )
