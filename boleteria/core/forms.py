from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.utils import timezone

from .models import Event, Profile, Review


def _clean_digits_only(value, required_message, invalid_message):
    normalized = (value or "").strip()
    if not normalized:
        raise forms.ValidationError(required_message)
    if not normalized.isdigit():
        raise forms.ValidationError(invalid_message)
    return normalized


class EventCreateForm(forms.ModelForm):
    ticket_limit = forms.IntegerField(
        required=True,
        min_value=1,
        initial=100,
        label="Cantidad de entradas QR disponibles",
    )
    datetime = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [
            choice for choice in Event.Status.choices if choice[0] != Event.Status.PENDING
        ]
        self.fields["age_rating"].label = "Clasificación de edad"
        self.fields["datetime"].label = "Fecha y hora de inicio"

    class Meta:
        model = Event
        fields = [
            "title",
            "description",
            "location",
            "unit_price_usd",
            "ticket_limit",
            "age_rating",
            "datetime",
            "status",
        ]
        labels = {
            "title": "Título",
            "description": "Descripción",
            "location": "Ubicación",
            "unit_price_usd": "Precio por entrada QR (USD)",
            "ticket_limit": "Cantidad de entradas QR disponibles",
            "age_rating": "Clasificación de edad",
            "datetime": "Fecha y hora de inicio",
            "status": "Estado",
        }

    def clean_datetime(self):
        value = self.cleaned_data["datetime"]
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value

    def clean_ticket_limit(self):
        value = self.cleaned_data.get("ticket_limit")
        if value:
            return value
        if self.instance and self.instance.pk and self.instance.ticket_limit:
            return self.instance.ticket_limit
        return 100


class EventUpdateForm(forms.ModelForm):
    ticket_limit = forms.IntegerField(
        required=True,
        min_value=1,
        initial=100,
        label="Cantidad de entradas QR disponibles",
    )
    datetime = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    end_datetime = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [
            choice for choice in Event.Status.choices if choice[0] != Event.Status.PENDING
        ]
        self.fields["age_rating"].label = "Clasificación de edad"
        self.fields["datetime"].label = "Fecha y hora de inicio"
        self.fields["end_datetime"].label = "Fecha y hora de finalización"
        if (
            self.instance
            and self.instance.pk
            and self.instance.status == Event.Status.PENDING
            and not self.is_bound
        ):
            self.initial["status"] = Event.Status.INACTIVE

    class Meta:
        model = Event
        fields = [
            "title",
            "description",
            "location",
            "organizer",
            "category",
            "unit_price_usd",
            "ticket_limit",
            "age_rating",
            "datetime",
            "end_datetime",
            "status",
        ]
        labels = {
            "title": "Título",
            "description": "Descripción",
            "location": "Ubicación",
            "organizer": "Organizador o productor",
            "category": "Categoría",
            "unit_price_usd": "Precio por entrada QR (USD)",
            "ticket_limit": "Cantidad de entradas QR disponibles",
            "age_rating": "Clasificación de edad",
            "datetime": "Fecha y hora de inicio",
            "end_datetime": "Fecha y hora de finalización",
            "status": "Estado",
        }

    def clean_datetime(self):
        value = self.cleaned_data["datetime"]
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value

    def clean(self):
        cleaned_data = super().clean()
        start_at = cleaned_data.get("datetime")
        end_at = cleaned_data.get("end_datetime")

        if end_at and timezone.is_naive(end_at):
            end_at = timezone.make_aware(end_at, timezone.get_current_timezone())

        if start_at and end_at and end_at < start_at:
            self.add_error("end_datetime", "La fecha de fin debe ser mayor o igual a la fecha de inicio.")

        cleaned_data["end_datetime"] = end_at
        return cleaned_data

    def clean_ticket_limit(self):
        value = self.cleaned_data.get("ticket_limit")
        if value:
            return value
        if self.instance and self.instance.pk and self.instance.ticket_limit:
            return self.instance.ticket_limit
        return 100


class EventProposalForm(forms.ModelForm):
    ticket_limit = forms.IntegerField(
        required=True,
        min_value=1,
        initial=100,
        label="Cantidad de entradas QR disponibles",
    )
    datetime = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    end_datetime = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
        required=False,
    )

    class Meta:
        model = Event
        fields = [
            "title",
            "description",
            "location",
            "organizer",
            "category",
            "unit_price_usd",
            "ticket_limit",
            "age_rating",
            "datetime",
            "end_datetime",
        ]
        labels = {
            "title": "Título",
            "description": "Descripción",
            "location": "Ubicación",
            "organizer": "Organizador o productor",
            "category": "Categoría",
            "unit_price_usd": "Precio por entrada QR (USD)",
            "ticket_limit": "Cantidad de entradas QR disponibles",
            "age_rating": "Clasificación de edad",
            "datetime": "Fecha y hora de inicio",
            "end_datetime": "Fecha y hora de finalización",
        }

    def clean_datetime(self):
        value = self.cleaned_data["datetime"]
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value

    def clean(self):
        cleaned_data = super().clean()
        start_at = cleaned_data.get("datetime")
        end_at = cleaned_data.get("end_datetime")

        if end_at and timezone.is_naive(end_at):
            end_at = timezone.make_aware(end_at, timezone.get_current_timezone())

        if start_at and end_at and end_at < start_at:
            self.add_error("end_datetime", "La fecha de fin debe ser mayor o igual a la fecha de inicio.")

        cleaned_data["end_datetime"] = end_at
        return cleaned_data

    def clean_ticket_limit(self):
        value = self.cleaned_data.get("ticket_limit")
        if value:
            return value
        if self.instance and self.instance.pk and self.instance.ticket_limit:
            return self.instance.ticket_limit
        return 100


class ProfileForm(forms.ModelForm):
    contact_number = forms.CharField(
        required=True,
        max_length=32,
        label="Número de contacto (celular o fijo)",
        widget=forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]+"}),
    )

    class Meta:
        model = Profile
        fields = ["contact_number", "display_name", "bio", "photo"]
        labels = {
            "contact_number": "Número de contacto (celular o fijo)",
            "display_name": "Nombre para mostrar",
            "bio": "Descripción de perfil",
            "photo": "Foto de perfil",
        }

    def clean_contact_number(self):
        return _clean_digits_only(
            self.cleaned_data.get("contact_number"),
            "El número de contacto es obligatorio.",
            "El número de contacto solo puede contener dígitos.",
        )

    def clean_display_name(self):
        display_name = (self.cleaned_data.get("display_name") or "").strip()
        if not display_name:
            return display_name
        exists = (
            Profile.objects.exclude(pk=self.instance.pk)
            .filter(display_name__iexact=display_name)
            .exists()
        )
        if exists:
            raise forms.ValidationError("Este nombre para mostrar ya está registrado.")
        return display_name


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ["comment"]
        labels = {"comment": "Comentario"}
        widgets = {
            "comment": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Escribe tu opinión sobre la obra"}
            ),
        }


class EmailRequiredUserCreationForm(UserCreationForm):
    username = forms.CharField(
        max_length=30,
        label="Usuario",
        help_text="",
    )
    email = forms.EmailField(required=True, label="Correo electrónico")
    display_name = forms.CharField(
        required=True,
        max_length=120,
        label="Nombre para mostrar",
    )
    contact_number = forms.CharField(
        required=True,
        max_length=32,
        label="Número de contacto (celular o fijo)",
        widget=forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]+"}),
    )
    password1 = forms.CharField(
        label="Contraseña",
        strip=False,
        help_text="Debe tener al menos 8 caracteres y no puede ser una contraseña común ni solo numérica.",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirmar contraseña",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "password1", "password2")

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            raise forms.ValidationError("El correo electrónico es obligatorio.")
        if "@" not in email:
            raise forms.ValidationError("El correo electrónico debe incluir '@'.")
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Este correo electrónico ya está registrado.")
        return email

    def clean_contact_number(self):
        return _clean_digits_only(
            self.cleaned_data.get("contact_number"),
            "El número de contacto es obligatorio.",
            "El número de contacto solo puede contener dígitos.",
        )

    def clean_display_name(self):
        display_name = (self.cleaned_data.get("display_name") or "").strip()
        if not display_name:
            raise forms.ValidationError("El nombre para mostrar es obligatorio.")
        if Profile.objects.filter(display_name__iexact=display_name).exists():
            raise forms.ValidationError("Este nombre para mostrar ya está registrado.")
        return display_name

    def save(self, commit=True):
        user = super().save(commit=commit)
        if not commit:
            return user

        profile, _ = Profile.objects.get_or_create(user=user)
        profile.contact_number = self.cleaned_data["contact_number"]
        profile.display_name = self.cleaned_data["display_name"]
        profile.save(update_fields=["contact_number", "display_name"])
        return user


class EmailOrUsernameAuthenticationForm(AuthenticationForm):
    username = forms.CharField(label="Usuario o correo electrónico")

    def clean(self):
        username = (self.cleaned_data.get("username") or "").strip()
        password = self.cleaned_data.get("password")
        if username and password:
            lookup_user = None
            if "@" in username:
                lookup_user = User.objects.filter(email__iexact=username).first()
            else:
                lookup_user = User.objects.filter(username__iexact=username).first()
            if lookup_user and not lookup_user.is_active:
                raise forms.ValidationError(
                    "Tu cuenta ha sido bloqueada. Comunícate con soporte.",
                    code="inactive",
                )
        return super().clean()


class UserEmailUpdateForm(forms.Form):
    email = forms.EmailField(required=True, label="Correo electrónico")

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["email"].initial = self.user.email

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            raise forms.ValidationError("El correo electrónico es obligatorio.")
        if "@" not in email:
            raise forms.ValidationError("El correo electrónico debe incluir '@'.")
        exists = User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists()
        if exists:
            raise forms.ValidationError("Este correo electrónico ya está registrado.")
        return email


class RulesContentForm(forms.Form):
    content = forms.CharField(
        label="Contenido de reglas (Markdown)",
        widget=forms.Textarea(
            attrs={
                "rows": 28,
                "spellcheck": "false",
                "class": "font-mono text-sm",
            }
        ),
    )



