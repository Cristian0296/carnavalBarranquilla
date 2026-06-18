from django import forms
import json

from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.utils.translation import get_language
from django.utils import timezone

from .models import Event, EventTicketType, MomentBlock, Product, ProductVariant, Profile, Review, SiteSettings


def _clean_digits_only(value, required_message, invalid_message):
    normalized = (value or "").strip()
    if not normalized:
        raise forms.ValidationError(required_message)
    if not normalized.isdigit():
        raise forms.ValidationError(invalid_message)
    return normalized


def _is_english_language():
    return (get_language() or "").lower().startswith("en")


class EventTicketTypesFormMixin:
    stock_limit_error_message = "No puedes reducir el limite por debajo de las entradas ya emitidas."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["general_price_usd"] = forms.DecimalField(
            required=True,
            min_value=0.01,
            decimal_places=2,
            max_digits=10,
            label="Precio boleta general (USD)",
        )
        self.fields["general_ticket_limit"] = forms.IntegerField(
            required=True,
            min_value=1,
            initial=100,
            label="Cantidad boleta general",
        )
        self.fields["vip_price_usd"] = forms.DecimalField(
            required=False,
            min_value=0.01,
            decimal_places=2,
            max_digits=10,
            label="Precio boleta VIP (USD)",
        )
        self.fields["vip_ticket_limit"] = forms.IntegerField(
            required=False,
            min_value=1,
            label="Cantidad boleta VIP",
        )
        self.fields["products_enabled"] = forms.BooleanField(
            required=False,
            label="Vender productos en este evento",
        )
        self.fields["products_payload"] = forms.CharField(
            required=False,
            widget=forms.HiddenInput(),
        )
        general_type = self._get_existing_ticket_type(EventTicketType.Code.GENERAL)
        vip_type = self._get_existing_ticket_type(EventTicketType.Code.VIP)
        if general_type:
            self.fields["general_price_usd"].initial = general_type.price_usd
            self.fields["general_ticket_limit"].initial = general_type.stock_total
        elif self.instance and self.instance.pk:
            self.fields["general_price_usd"].initial = self.instance.unit_price_usd
            self.fields["general_ticket_limit"].initial = self.instance.ticket_limit

        if vip_type:
            self.fields["vip_price_usd"].initial = vip_type.price_usd
            self.fields["vip_ticket_limit"].initial = vip_type.stock_total
        existing_products_payload = self._serialize_existing_products()
        if existing_products_payload:
            self.fields["products_enabled"].initial = True
            self.fields["products_payload"].initial = json.dumps(existing_products_payload)

    def _serialize_existing_products(self):
        if not getattr(self.instance, "pk", None):
            return []
        products = []
        for product in self.instance.products.prefetch_related("variants").order_by("name", "id"):
            variants = []
            simple_stock = 0
            simple_variant_id = None
            for variant in product.variants.order_by("name", "id"):
                if product.has_variants:
                    variants.append(
                        {
                            "id": variant.pk,
                            "name": variant.name,
                            "stock_total": variant.stock_total,
                            "is_active": variant.is_active,
                        }
                    )
                elif variant.name == "Unidad":
                    simple_stock = variant.stock_total
                    simple_variant_id = variant.pk
            products.append(
                {
                    "id": product.pk,
                    "name": product.name,
                    "description": product.description,
                    "price_usd": str(product.price_usd),
                    "is_active": product.is_active,
                    "has_variants": product.has_variants,
                    "image_input_key": f"product_image_{product.pk}",
                    "image_url": product.image.url if product.image else "",
                    "simple_stock": simple_stock,
                    "simple_variant_id": simple_variant_id,
                    "variants": variants,
                }
            )
        return products

    def _get_existing_ticket_type(self, code):
        if not getattr(self.instance, "pk", None):
            return None
        if not hasattr(self, "_ticket_types_by_code"):
            self._ticket_types_by_code = {
                ticket_type.code: ticket_type
                for ticket_type in self.instance.ticket_types.all()
            }
        return self._ticket_types_by_code.get(code)

    def _issued_tickets_count_for(self, code):
        ticket_type = self._get_existing_ticket_type(code)
        if not ticket_type:
            return 0
        return ticket_type.tickets.count()

    def clean_general_ticket_limit(self):
        value = self.cleaned_data.get("general_ticket_limit")
        sold_tickets = self._issued_tickets_count_for(EventTicketType.Code.GENERAL)
        if value is not None and value < sold_tickets:
            raise forms.ValidationError(
                f"No puedes definir menos de {sold_tickets} entradas generales porque ya fueron emitidas."
            )
        return value

    def clean(self):
        cleaned_data = super().clean()
        vip_price = cleaned_data.get("vip_price_usd")
        vip_limit = cleaned_data.get("vip_ticket_limit")
        vip_sold_tickets = self._issued_tickets_count_for(EventTicketType.Code.VIP)
        has_vip_price = vip_price is not None
        has_vip_limit = vip_limit not in (None, "")

        if has_vip_price and not has_vip_limit:
            self.add_error("vip_ticket_limit", "Debes indicar la cantidad de boletas VIP.")
        if has_vip_limit and not has_vip_price:
            self.add_error("vip_price_usd", "Debes indicar el precio de la boleta VIP.")

        if not has_vip_price and not has_vip_limit and vip_sold_tickets > 0:
            message = "No puedes quitar VIP porque ya existen boletas VIP emitidas."
            self.add_error("vip_price_usd", message)
            self.add_error("vip_ticket_limit", message)

        if vip_limit not in (None, "") and vip_limit < vip_sold_tickets:
            self.add_error(
                "vip_ticket_limit",
                f"No puedes definir menos de {vip_sold_tickets} entradas VIP porque ya fueron emitidas.",
            )

        try:
            cleaned_data["parsed_products"] = self._clean_products_payload(cleaned_data)
        except forms.ValidationError as exc:
            self.add_error("products_payload", exc)

        return cleaned_data

    def _clean_products_payload(self, cleaned_data):
        products_enabled = bool(cleaned_data.get("products_enabled"))
        raw_payload = (cleaned_data.get("products_payload") or "").strip()
        if not products_enabled:
            return []
        if not raw_payload:
            raise forms.ValidationError("Debes agregar al menos un producto o desactivar la venta de productos.")
        try:
            parsed = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"No se pudo leer la configuracion de productos: {exc.msg}.")
        if not isinstance(parsed, list) or not parsed:
            raise forms.ValidationError("Debes agregar al menos un producto.")

        normalized_products = []
        for index, raw_product in enumerate(parsed, start=1):
            if not isinstance(raw_product, dict):
                raise forms.ValidationError(f"El producto #{index} no tiene un formato valido.")
            name = (raw_product.get("name") or "").strip()
            if not name:
                raise forms.ValidationError(f"El producto #{index} debe tener nombre.")
            description = (raw_product.get("description") or "").strip()
            price_raw = str(raw_product.get("price_usd") or "").strip()
            try:
                price_usd = float(price_raw)
            except (TypeError, ValueError):
                raise forms.ValidationError(f'El producto "{name}" debe tener un precio valido.')
            if price_usd <= 0:
                raise forms.ValidationError(f'El producto "{name}" debe tener un precio mayor a 0.')

            normalized_product = {
                "id": int(raw_product["id"]) if str(raw_product.get("id")).isdigit() else None,
                "name": name,
                "description": description,
                "price_usd": price_raw,
                "is_active": bool(raw_product.get("is_active", True)),
                "has_variants": bool(raw_product.get("has_variants")),
                "image_input_key": (raw_product.get("image_input_key") or "").strip(),
                "variants": [],
            }

            if normalized_product["has_variants"]:
                raw_variants = raw_product.get("variants") or []
                if not isinstance(raw_variants, list) or not raw_variants:
                    raise forms.ValidationError(f'El producto "{name}" debe tener al menos una variante.')
                has_active_stock = False
                seen_variant_names = set()
                for raw_variant in raw_variants:
                    if not isinstance(raw_variant, dict):
                        raise forms.ValidationError(f'El producto "{name}" tiene una variante invalida.')
                    variant_name = (raw_variant.get("name") or "").strip()
                    if not variant_name:
                        raise forms.ValidationError(f'Cada variante del producto "{name}" debe tener nombre.')
                    variant_name_key = variant_name.casefold()
                    if variant_name_key in seen_variant_names:
                        raise forms.ValidationError(
                            f'El producto "{name}" no puede tener variantes repetidas: "{variant_name}".'
                        )
                    seen_variant_names.add(variant_name_key)
                    try:
                        stock_total = int(raw_variant.get("stock_total"))
                    except (TypeError, ValueError):
                        raise forms.ValidationError(
                            f'La variante "{variant_name}" del producto "{name}" debe tener stock valido.'
                        )
                    if stock_total < 0:
                        raise forms.ValidationError(
                            f'La variante "{variant_name}" del producto "{name}" no puede tener stock negativo.'
                        )
                    variant_is_active = bool(raw_variant.get("is_active", True))
                    if variant_is_active and stock_total > 0:
                        has_active_stock = True
                    normalized_product["variants"].append(
                        {
                            "id": int(raw_variant["id"]) if str(raw_variant.get("id")).isdigit() else None,
                            "name": variant_name,
                            "stock_total": stock_total,
                            "is_active": variant_is_active,
                        }
                    )
                if not has_active_stock:
                    raise forms.ValidationError(
                        f'El producto "{name}" debe tener al menos una variante activa con stock.'
                    )
            else:
                try:
                    simple_stock = int(raw_product.get("simple_stock"))
                except (TypeError, ValueError):
                    raise forms.ValidationError(f'El producto "{name}" debe tener stock valido.')
                if simple_stock <= 0:
                    raise forms.ValidationError(f'El producto "{name}" debe tener stock mayor a 0.')
                normalized_product["variants"].append(
                    {
                        "id": int(raw_product["simple_variant_id"])
                        if str(raw_product.get("simple_variant_id")).isdigit()
                        else None,
                        "name": "Unidad",
                        "stock_total": simple_stock,
                        "is_active": normalized_product["is_active"],
                    }
                )

            normalized_products.append(normalized_product)

        return normalized_products

    def save(self, commit=True):
        event = super().save(commit=False)
        event.unit_price_usd = self.cleaned_data["general_price_usd"]
        event.ticket_limit = self.cleaned_data["general_ticket_limit"]
        event.status = Event.Status.ACTIVE
        if commit:
            event.save()
            self._save_m2m()
            self._save_ticket_types(event)
            self._save_products(event)
        return event

    def _save_ticket_types(self, event):
        general_defaults = {
            "name": "General",
            "price_usd": self.cleaned_data["general_price_usd"],
            "stock_total": self.cleaned_data["general_ticket_limit"],
            "is_active": True,
            "display_order": 1,
            "number_prefix": "G",
        }
        EventTicketType.objects.update_or_create(
            event=event,
            code=EventTicketType.Code.GENERAL,
            defaults=general_defaults,
        )

        vip_defaults = {
            "name": "VIP",
            "price_usd": self.cleaned_data.get("vip_price_usd") or self.cleaned_data["general_price_usd"],
            "stock_total": self.cleaned_data.get("vip_ticket_limit") or 1,
            "is_active": bool(
                self.cleaned_data.get("vip_price_usd") is not None
                and self.cleaned_data.get("vip_ticket_limit") not in (None, "")
            ),
            "display_order": 2,
            "number_prefix": "VIP",
        }
        vip_type, vip_created = EventTicketType.objects.get_or_create(
            event=event,
            code=EventTicketType.Code.VIP,
            defaults=vip_defaults,
        )
        if vip_created:
            return
        updated_fields = []
        for field, value in vip_defaults.items():
            if getattr(vip_type, field) != value:
                setattr(vip_type, field, value)
                updated_fields.append(field)
        if updated_fields:
            vip_type.save(update_fields=updated_fields)

    def _save_products(self, event, uploaded_files=None):
        products_data = self.cleaned_data.get("parsed_products", [])
        existing_products = {product.pk: product for product in event.products.prefetch_related("variants")}
        kept_product_ids = []
        for product_data in products_data:
            product = existing_products.get(product_data["id"])
            if product is None:
                product = Product(event=event)
            product.name = product_data["name"]
            product.description = product_data["description"]
            product.price_usd = product_data["price_usd"]
            product.is_active = product_data["is_active"]
            product.has_variants = product_data["has_variants"]
            image_input_key = product_data.get("image_input_key")
            if uploaded_files and image_input_key:
                image_file = uploaded_files.get(image_input_key)
                if image_file:
                    product.image = image_file
            product.save()
            kept_product_ids.append(product.pk)

            existing_variants = {variant.pk: variant for variant in product.variants.all()}
            kept_variant_ids = []
            for variant_data in product_data["variants"]:
                variant = existing_variants.get(variant_data["id"])
                if variant is None:
                    variant = ProductVariant(product=product)
                variant.name = variant_data["name"]
                variant.stock_total = variant_data["stock_total"]
                variant.is_active = variant_data["is_active"]
                variant.save()
                kept_variant_ids.append(variant.pk)

            if kept_variant_ids:
                product.variants.exclude(pk__in=kept_variant_ids).delete()
            else:
                product.variants.all().delete()

        if kept_product_ids:
            event.products.exclude(pk__in=kept_product_ids).delete()
        else:
            event.products.all().delete()


class EventCreateForm(EventTicketTypesFormMixin, forms.ModelForm):
    datetime = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    end_datetime = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
        required=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["age_rating"].label = "Clasificación de edad"
        self.fields["datetime"].label = "Fecha y hora de inicio"
        self.fields["end_datetime"].label = "Fecha y hora de finalización"

    class Meta:
        model = Event
        fields = [
            "title",
            "description",
            "location",
            "age_rating",
            "datetime",
            "end_datetime",
        ]
        labels = {
            "title": "Título",
            "description": "Descripción",
            "location": "Ubicación",
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

class EventUpdateForm(EventTicketTypesFormMixin, forms.ModelForm):
    datetime = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    end_datetime = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
        required=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["age_rating"].label = "Clasificación de edad"
        self.fields["datetime"].label = "Fecha y hora de inicio"
        self.fields["end_datetime"].label = "Fecha y hora de finalización"

    class Meta:
        model = Event
        fields = [
            "title",
            "description",
            "location",
            "organizer",
            "category",
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


class MomentBlockForm(forms.ModelForm):
    class Meta:
        model = MomentBlock
        fields = ["title", "description"]
        labels = {
            "title": "Titulo",
            "description": "Descripcion",
        }


class SiteSettingsForm(forms.ModelForm):
    home_video_url = forms.URLField(
        required=False,
        label="Video de inicio (YouTube)",
        widget=forms.URLInput(
            attrs={
                "placeholder": "https://www.youtube.com/watch?v=... o https://youtu.be/...",
            }
        ),
    )

    class Meta:
        model = SiteSettings
        fields = [
            "whatsapp_url",
            "instagram_url",
            "facebook_url",
            "tiktok_url",
            "x_url",
            "telegram_url",
            "home_video_url",
            "footer_primary_text",
            "footer_tagline",
            "footer_copyright_text",
        ]
        labels = {
            "whatsapp_url": "Enlace de WhatsApp",
            "instagram_url": "Enlace de Instagram",
            "facebook_url": "Enlace de Facebook",
            "tiktok_url": "Enlace de TikTok",
            "x_url": "Enlace de X",
            "telegram_url": "Enlace de Telegram",
            "footer_primary_text": "Texto principal del pie de pagina",
            "footer_tagline": "Frase destacada del pie de pagina",
            "footer_copyright_text": "Texto legal del pie de pagina",
        }

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
        return display_name


class ReviewForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if _is_english_language():
            self.fields["comment"].label = "Comment"
            self.fields["comment"].widget.attrs["placeholder"] = "Write your opinion about the event"

    class Meta:
        model = Review
        fields = ["comment"]
        labels = {"comment": "Comentario"}
        widgets = {
            "comment": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Escribe tu opinion sobre el evento"}
            ),
        }


class EmailRequiredUserCreationForm(UserCreationForm):
    username = forms.CharField(
        max_length=30,
        label="Usuario",
        help_text="",
    )
    email = forms.EmailField(required=True, label="Correo electronico")
    display_name = forms.CharField(
        required=True,
        max_length=120,
        label="Nombre para mostrar",
    )
    contact_number = forms.CharField(
        required=True,
        max_length=32,
        label="Numero de contacto (celular o fijo)",
        widget=forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]+"}),
    )
    password1 = forms.CharField(
        label="Contrasena",
        strip=False,
        help_text="Debe tener al menos 8 caracteres y no puede ser una contrasena comun ni solo numerica.",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirmar contrasena",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if _is_english_language():
            self.fields["username"].label = "Username"
            self.fields["email"].label = "Email address"
            self.fields["display_name"].label = "Display name"
            self.fields["contact_number"].label = "Contact number (mobile or landline)"
            self.fields["password1"].label = "Password"
            self.fields["password1"].help_text = (
                "It must be at least 8 characters long and cannot be a common or numeric-only password."
            )
            self.fields["password2"].label = "Confirm password"

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            raise forms.ValidationError(
                "Email address is required." if _is_english_language() else "El correo electronico es obligatorio."
            )
        if "@" not in email:
            raise forms.ValidationError(
                "Email address must include '@'."
                if _is_english_language()
                else "El correo electronico debe incluir '@'."
            )
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "This email address is already registered."
                if _is_english_language()
                else "Este correo electronico ya esta registrado."
            )
        return email

    def clean_contact_number(self):
        return _clean_digits_only(
            self.cleaned_data.get("contact_number"),
            "Contact number is required." if _is_english_language() else "El numero de contacto es obligatorio.",
            "Contact number can only contain digits."
            if _is_english_language()
            else "El numero de contacto solo puede contener digitos.",
        )

    def clean_display_name(self):
        display_name = (self.cleaned_data.get("display_name") or "").strip()
        if not display_name:
            raise forms.ValidationError(
                "Display name is required." if _is_english_language() else "El nombre para mostrar es obligatorio."
            )
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
    username = forms.CharField(label="Usuario o correo electronico")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if _is_english_language():
            self.fields["username"].label = "Username or email address"
            self.fields["password"].label = "Password"

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
                    "Your account has been blocked. Please contact support."
                    if _is_english_language()
                    else "Tu cuenta ha sido bloqueada. Comunicate con soporte.",
                    code="inactive",
                )
            if lookup_user and lookup_user.is_active:
                try:
                    if not lookup_user.is_staff and not lookup_user.profile.email_verified:
                        if self.request is not None:
                            self.request.session["pending_verification_email"] = lookup_user.email
                        raise forms.ValidationError(
                            "Your account has not been verified yet. Check your email and confirm your account before signing in."
                            if _is_english_language()
                            else "Tu cuenta aun no ha sido verificada. Revisa tu correo y confirma tu cuenta para poder ingresar.",
                            code="email_not_verified",
                        )
                except Profile.DoesNotExist:
                    pass
        return super().clean()


class EmailVerificationResendForm(forms.Form):
    email = forms.EmailField(required=True, label="Correo electronico")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if _is_english_language():
            self.fields["email"].label = "Email address"

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()


class UserEmailUpdateForm(forms.Form):
    email = forms.EmailField(required=True, label="Correo electronico")

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["email"].initial = self.user.email
        if _is_english_language():
            self.fields["email"].label = "Email address"

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            raise forms.ValidationError(
                "Email address is required." if _is_english_language() else "El correo electronico es obligatorio."
            )
        if "@" not in email:
            raise forms.ValidationError(
                "Email address must include '@'."
                if _is_english_language()
                else "El correo electronico debe incluir '@'."
            )
        exists = User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists()
        if exists:
            raise forms.ValidationError(
                "This email address is already registered."
                if _is_english_language()
                else "Este correo electronico ya esta registrado."
            )
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



