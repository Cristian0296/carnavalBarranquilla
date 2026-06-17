from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import tempfile
from urllib.parse import urlsplit
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.core import signing
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from .models import (
    Event,
    EventImage,
    EventTicketType,
    Cart,
    CartItem,
    MomentBlock,
    MomentMedia,
    Notification,
    Order,
    OrderItem,
    Profile,
    Product,
    ProductRedemption,
    ProductVariant,
    Review,
    ReviewReport,
    ReviewReaction,
    SiteSettings,
    Ticket,
    ValidationLog,
)
from .services import consume_ticket_atomic, generate_ticket_token


class AuthFlowTests(TestCase):
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_signup_creates_customer_user(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "cliente1",
                "email": "cliente1@example.com",
                "display_name": "Cliente Uno",
                "document_number": "100000001",
                "contact_number": "3001234567",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("home"))
        self.assertTrue(
            User.objects.filter(username="cliente1", email="cliente1@example.com").exists()
        )
        profile = Profile.objects.get(user__username="cliente1")
        self.assertEqual(profile.display_name, "Cliente Uno")
        self.assertFalse(profile.email_verified)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Confirma tu correo", mail.outbox[0].subject)
        self.assertIn("/accounts/verify-email/confirm/", mail.outbox[0].body)
        self.assertContains(response, "Revisa tu correo")

    def test_login_with_valid_credentials(self):
        User.objects.create_user(username="cliente2", password="StrongPass123!")

        response = self.client.post(
            reverse("login"),
            {"username": "cliente2", "password": "StrongPass123!"},
        )

        self.assertRedirects(response, reverse("home"))

    def test_login_blocked_account_shows_contact_message(self):
        User.objects.create_user(
            username="cliente_bloqueado",
            password="StrongPass123!",
            is_active=False,
        )

        response = self.client.post(
            reverse("login"),
            {"username": "cliente_bloqueado", "password": "StrongPass123!"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu cuenta ha sido bloqueada. Comunícate con soporte.")

    def test_login_rejects_unverified_account(self):
        user = User.objects.create_user(
            username="cliente_pendiente",
            email="cliente_pendiente@example.com",
            password="StrongPass123!",
        )
        Profile.objects.update_or_create(
            user=user,
            defaults={"email_verified": False},
        )

        response = self.client.post(
            reverse("login"),
            {"username": "cliente_pendiente", "password": "StrongPass123!"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Tu cuenta aun no ha sido verificada. Revisa tu correo y confirma tu cuenta para poder ingresar.",
        )

    def test_login_with_email_and_password(self):
        User.objects.create_user(
            username="cliente_email_login",
            email="cliente_email_login@example.com",
            password="StrongPass123!",
        )

        response = self.client.post(
            reverse("login"),
            {"username": "cliente_email_login@example.com", "password": "StrongPass123!"},
        )

        self.assertRedirects(response, reverse("home"))

    def test_login_respects_next_parameter(self):
        User.objects.create_user(username="cliente_next", password="StrongPass123!")

        response = self.client.post(
            reverse("login"),
            {
                "username": "cliente_next",
                "password": "StrongPass123!",
                "next": reverse("event_list"),
            },
        )

        self.assertRedirects(response, reverse("event_list"))

    def test_login_page_shows_password_toggle_control(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'input[name="password"]', html=False)
        self.assertContains(response, "password-toggle-button")
        self.assertContains(response, "Mostrar contraseña")

    def test_home_is_accessible(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

    def test_home_shows_two_latest_public_events_for_guest(self):
        older_event = Event.objects.create(
            title="Evento Publico Uno",
            datetime=datetime.now(timezone.utc) + timedelta(days=1),
            end_datetime=datetime.now(timezone.utc) + timedelta(days=3),
            status=Event.Status.ACTIVE,
        )
        newest_event = Event.objects.create(
            title="Evento Mas Reciente",
            datetime=datetime.now(timezone.utc) + timedelta(days=2),
            end_datetime=datetime.now(timezone.utc) + timedelta(days=4),
            status=Event.Status.ACTIVE,
        )
        ended_event = Event.objects.create(
            title="Evento No Visible",
            datetime=datetime.now(timezone.utc) - timedelta(days=3),
            end_datetime=datetime.now(timezone.utc) - timedelta(days=1),
            status=Event.Status.ACTIVE,
        )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Conoce lo mas reciente")
        self.assertContains(response, "Evento Mas Reciente")
        self.assertContains(response, "Evento Publico Uno")
        self.assertNotContains(response, "Evento No Visible")

    def test_home_uses_configured_youtube_video_embed_url(self):
        settings_obj = SiteSettings.get_solo()
        settings_obj.home_video_url = "https://www.youtube.com/watch?v=abc123XYZ98"
        settings_obj.save(update_fields=["home_video_url"])

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://www.youtube.com/embed/abc123XYZ98?rel=0")

    def test_home_login_link_preserves_next(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'{reverse("login")}?next={reverse("home")}')

    def test_email_verification_sent_page_is_accessible(self):
        session = self.client.session
        session["pending_verification_email"] = "cliente_verificacion@example.com"
        session.save()

        response = self.client.get(reverse("email_verification_sent"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Revisa tu correo")
        self.assertContains(response, "cliente_verificacion@example.com")

    def test_signup_requires_email(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "cliente_sin_correo",
                "display_name": "Sin Correo",
                "document_number": "100000002",
                "contact_number": "3001234568",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Este campo es obligatorio")
        self.assertFalse(User.objects.filter(username="cliente_sin_correo").exists())

    def test_password_reset_page_is_accessible(self):
        response = self.client.get(reverse("password_reset"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recuperar")
        self.assertContains(response, 'name="email"', html=False)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_verification_confirm_marks_profile_verified(self):
        user = User.objects.create_user(
            username="cliente_verificar",
            email="cliente_verificar@example.com",
            password="StrongPass123!",
        )
        Profile.objects.update_or_create(
            user=user,
            defaults={"email_verified": False, "display_name": "Cliente Verificar"},
        )
        token = signing.dumps(
            {"user_id": user.pk, "email": user.email},
            salt="core.email_verification",
        )

        response = self.client.get(
            f"{reverse('verify_email_confirm')}?token={token}",
            follow=True,
        )

        self.assertRedirects(response, reverse("login"))
        user.refresh_from_db()
        self.assertTrue(user.profile.email_verified)
        self.assertContains(response, "Tu correo fue confirmado correctamente. Ya puedes iniciar sesion.")
        self.assertContains(response, "Ya puedes iniciar sesion")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_resend_email_verification_sends_new_email_for_unverified_account(self):
        user = User.objects.create_user(
            username="cliente_reenvio",
            email="cliente_reenvio@example.com",
            password="StrongPass123!",
        )
        Profile.objects.update_or_create(
            user=user,
            defaults={"email_verified": False},
        )

        response = self.client.post(
            reverse("resend_email_verification"),
            {"email": user.email},
            follow=True,
        )

        self.assertRedirects(response, reverse("email_verification_sent"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/accounts/verify-email/confirm/", mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_resend_email_verification_limits_attempts_per_hour(self):
        user = User.objects.create_user(
            username="cliente_reenvio_limite",
            email="cliente_reenvio_limite@example.com",
            password="StrongPass123!",
        )
        Profile.objects.update_or_create(
            user=user,
            defaults={"email_verified": False},
        )

        for _ in range(3):
            self.client.post(reverse("resend_email_verification"), {"email": user.email})

        response = self.client.post(
            reverse("resend_email_verification"),
            {"email": user.email},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya alcanzaste el limite de 3 reenvios por hora.")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_password_reset_sends_email_for_registered_user(self):
        user = User.objects.create_user(
            username="cliente_reset",
            email="cliente_reset@example.com",
            password="StrongPass123!",
        )

        response = self.client.post(reverse("password_reset"), {"email": user.email}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Recupera tu acceso", mail.outbox[0].subject)
        self.assertIn("/accounts/reset/", mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_password_reset_allows_setting_new_password_from_link(self):
        user = User.objects.create_user(
            username="cliente_reset_confirm",
            email="cliente_reset_confirm@example.com",
            password="StrongPass123!",
        )

        self.client.post(reverse("password_reset"), {"email": user.email}, follow=True)
        reset_email = mail.outbox[0]
        reset_url = next(part for part in reset_email.body.split() if "/accounts/reset/" in part)
        reset_path = urlsplit(reset_url).path

        confirm_response = self.client.get(reset_path, follow=True)
        self.assertEqual(confirm_response.status_code, 200)

        post_response = self.client.post(
            confirm_response.request["PATH_INFO"],
            {
                "new_password1": "NewStrongPass123!",
                "new_password2": "NewStrongPass123!",
            },
            follow=True,
        )

        self.assertEqual(post_response.status_code, 200)
        self.assertRedirects(post_response, reverse("password_reset_complete"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewStrongPass123!"))

    def test_signup_requires_display_name(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "cliente_sin_nombre_mostrar",
                "email": "cliente_sin_nombre_mostrar@example.com",
                "contact_number": "3001234569",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Este campo es obligatorio")
        self.assertFalse(User.objects.filter(username="cliente_sin_nombre_mostrar").exists())

    def test_signup_rejects_duplicate_email(self):
        User.objects.create_user(
            username="cliente_existente",
            email="repetido@example.com",
            password="StrongPass123!",
        )

        response = self.client.post(
            reverse("signup"),
            {
                "username": "cliente_nuevo",
                "email": "repetido@example.com",
                "display_name": "Cliente Nuevo",
                "contact_number": "3001234570",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Este correo electrónico ya está registrado.")
        self.assertContains(response, "Este correo ya esta en uso. Intenta iniciar sesion o recuperar tu contrasena.")
        self.assertFalse(User.objects.filter(username="cliente_nuevo").exists())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_signup_allows_duplicate_display_name(self):
        existing_user = User.objects.create_user(
            username="cliente_display_existente",
            email="display_existente@example.com",
            password="StrongPass123!",
        )
        Profile.objects.update_or_create(
            user=existing_user,
            defaults={"display_name": "Nombre Festival"},
        )

        response = self.client.post(
            reverse("signup"),
            {
                "username": "cliente_display_nuevo",
                "email": "display_nuevo@example.com",
                "display_name": "Nombre Festival",
                "contact_number": "3001234571",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertRedirects(response, reverse("home"))
        self.assertTrue(User.objects.filter(username="cliente_display_nuevo").exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_signup_rejects_non_numeric_contact(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "cliente_contacto_invalido",
                "email": "cliente_contacto_invalido@example.com",
                "document_number": "100000003",
                "contact_number": "300-ABCD",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="cliente_contacto_invalido").exists())


class EventViewsTests(TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.active_event = Event.objects.create(
            title="Evento Activo",
            datetime=now - timedelta(hours=1),
            end_datetime=now + timedelta(days=2),
            unit_price_usd=Decimal("2.50"),
            status=Event.Status.ACTIVE,
        )
        self.scheduled_active_event = Event.objects.create(
            title="Evento Programado",
            datetime=now + timedelta(days=1),
            end_datetime=now + timedelta(days=3),
            status=Event.Status.ACTIVE,
        )
        self.ended_active_event = Event.objects.create(
            title="Evento Activo Finalizado",
            datetime=now - timedelta(days=3),
            end_datetime=now - timedelta(days=2),
            status=Event.Status.ACTIVE,
        )
        self.inactive_event = Event.objects.create(
            title="Evento Inactivo",
            datetime=now + timedelta(days=5),
            status=Event.Status.INACTIVE,
        )

    def _add_ticket(self, event, quantity="1", ticket_type=EventTicketType.Code.GENERAL):
        return self.client.post(
            reverse("add_ticket_to_cart", args=[event.pk]),
            {"quantity": quantity, "ticket_type": ticket_type},
        )

    def _checkout(self):
        return self.client.post(reverse("checkout_cart"))

    def test_event_list_shows_only_active_events(self):
        response = self.client.get(reverse("event_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Evento Activo")
        self.assertContains(response, "USD 2,50 por entrada QR")
        self.assertContains(response, "Evento Programado")
        self.assertNotContains(response, "Evento Activo Finalizado")
        self.assertNotContains(response, "Evento Inactivo")

    def test_moments_page_shows_block_titles_descriptions_and_media(self):
        block = MomentBlock.objects.create(
            title="Noche de comparsas",
            description="Una galeria llena de recuerdos y color.",
            is_active=True,
            display_order=1,
        )
        MomentMedia.objects.create(
            block=block,
            media_type=MomentMedia.MediaType.IMAGE,
            file=SimpleUploadedFile(
                "momento.jpg",
                b"fake-image-content",
                content_type="image/jpeg",
            ),
        )

        response = self.client.get(reverse("moments_page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Momentos del Carnaval")
        self.assertContains(response, block.title)
        self.assertContains(response, block.description)

    def test_footer_only_shows_social_links_with_configured_urls(self):
        SiteSettings.objects.update_or_create(
            pk=1,
            defaults={
                "whatsapp_url": "https://wa.me/123",
                "instagram_url": "https://instagram.com/demo",
                "facebook_url": "",
                "tiktok_url": "",
                "x_url": "",
                "telegram_url": "",
                "footer_primary_text": "Texto principal demo",
                "footer_tagline": "Frase demo",
                "footer_copyright_text": "Legal demo",
            },
        )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://wa.me/123")
        self.assertContains(response, "https://instagram.com/demo")
        self.assertContains(response, "Texto principal demo")
        self.assertContains(response, "Frase demo")
        self.assertContains(response, "Legal demo")
        self.assertNotContains(response, 'aria-label="Facebook"')
        self.assertNotContains(response, 'aria-label="TikTok"')
        self.assertNotContains(response, 'aria-label="X"')
        self.assertNotContains(response, 'aria-label="Telegram"')

    def test_event_list_highlights_vip_availability(self):
        EventTicketType.objects.create(
            event=self.active_event,
            code=EventTicketType.Code.VIP,
            name="VIP",
            price_usd=Decimal("5.00"),
            stock_total=20,
            is_active=True,
            display_order=2,
            number_prefix="VIP",
        )

        response = self.client.get(reverse("event_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "VIP disponible")
        self.assertContains(response, "Desde USD 2,50 en entradas QR")

    def test_event_detail_for_active_event(self):
        response = self.client.get(reverse("event_detail", args=[self.active_event.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Evento Activo")
        self.assertContains(response, "Debes iniciar sesión para comprar.")
        self.assertContains(
            response,
            f'{reverse("login")}?next={reverse("event_detail", args=[self.active_event.pk])}',
        )

    def test_event_detail_displays_raffle_range_with_dynamic_width(self):
        self.active_event.ticket_limit = 100
        self.active_event.save(update_fields=["ticket_limit"])

        response = self.client.get(reverse("event_detail", args=[self.active_event.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Total de entradas QR para la venta:")
        self.assertNotContains(response, "Boletas emitidas:")
        self.assertNotContains(response, "Boletas disponibles:")

    def test_event_detail_shows_general_and_vip_cards_when_vip_is_active(self):
        EventTicketType.objects.create(
            event=self.active_event,
            code=EventTicketType.Code.VIP,
            name="VIP",
            price_usd=Decimal("5.00"),
            stock_total=20,
            is_active=True,
            display_order=2,
            number_prefix="VIP",
        )

        user = User.objects.create_user(username="buyer_cards", password="StrongPass123!")
        self.client.login(username="buyer_cards", password="StrongPass123!")
        response = self.client.get(reverse("event_detail", args=[self.active_event.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Entrada General")
        self.assertContains(response, "Entrada VIP")
        self.assertContains(response, 'name="ticket_type" value="general"')
        self.assertContains(response, 'name="ticket_type" value="vip"')

    def test_event_detail_returns_404_for_inactive_event(self):
        response = self.client.get(reverse("event_detail", args=[self.inactive_event.pk]))
        self.assertEqual(response.status_code, 404)

    def test_event_detail_allows_viewing_scheduled_active_event(self):
        response = self.client.get(reverse("event_detail", args=[self.scheduled_active_event.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Evento Programado")
        self.assertContains(response, "Debes iniciar sesión para comprar.")
        self.assertNotContains(response, "La compra se habilitara cuando llegue la hora de inicio.")

    def test_global_nav_login_link_preserves_current_path(self):
        response = self.client.get(reverse("event_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'{reverse("login")}?next={reverse("event_list")}')

    def test_add_ticket_to_cart_requires_authentication(self):
        response = self.client.post(reverse("add_ticket_to_cart", args=[self.active_event.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_purchase_direct_is_disabled(self):
        response = self.client.post(reverse("purchase_event", args=[self.active_event.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_add_ticket_rejects_scheduled_active_event_before_start(self):
        user = User.objects.create_user(username="buyer_scheduled", password="StrongPass123!")
        self.client.login(username="buyer_scheduled", password="StrongPass123!")
        response = self._add_ticket(self.scheduled_active_event)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.count(), 0)

    def test_checkout_creates_pending_order_without_unused_ticket(self):
        user = User.objects.create_user(username="buyer1", password="StrongPass123!")
        self.client.login(username="buyer1", password="StrongPass123!")

        add_response = self._add_ticket(self.active_event)
        response = self._checkout()
        self.assertEqual(add_response.status_code, 302)
        self.assertEqual(response.status_code, 200)

        order = Order.objects.get(user=user, event=self.active_event)

        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.quantity, 1)
        self.assertEqual(order.unit_price_usd, Decimal("2.50"))
        self.assertEqual(order.total_usd, Decimal("2.50"))
        self.assertIsNotNone(order.ticket_type)
        self.assertEqual(order.ticket_type.code, EventTicketType.Code.GENERAL)
        self.assertEqual(order.items.count(), 1)
        self.assertFalse(Ticket.objects.filter(order=order).exists())
        self.assertContains(response, "Pago")
        self.assertContains(response, "pendiente")
        self.assertContains(response, "2,50")

    def test_checkout_multiple_tickets_in_single_pending_order(self):
        user = User.objects.create_user(username="buyer_multi", password="StrongPass123!")
        self.client.login(username="buyer_multi", password="StrongPass123!")

        add_response = self._add_ticket(self.active_event, quantity="3")
        response = self._checkout()
        self.assertEqual(add_response.status_code, 302)
        self.assertEqual(response.status_code, 200)
        order = Order.objects.get(user=user, event=self.active_event)
        tickets = Ticket.objects.filter(order=order).order_by("issued_at")
        self.assertEqual(tickets.count(), 0)
        self.assertEqual(order.quantity, 3)
        self.assertEqual(order.unit_price_usd, Decimal("2.50"))
        self.assertEqual(order.total_usd, Decimal("7.50"))
        self.assertIsNotNone(order.ticket_type)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertContains(response, "Pago")
        self.assertContains(response, "7,50")

    def test_checkout_can_prepare_pending_vip_ticket_type(self):
        EventTicketType.objects.create(
            event=self.active_event,
            code=EventTicketType.Code.VIP,
            name="VIP",
            price_usd=Decimal("5.00"),
            stock_total=20,
            is_active=True,
            display_order=2,
            number_prefix="VIP",
        )
        user = User.objects.create_user(username="buyer_vip", password="StrongPass123!")
        self.client.login(username="buyer_vip", password="StrongPass123!")

        add_response = self._add_ticket(self.active_event, quantity="2", ticket_type="vip")
        response = self._checkout()
        self.assertEqual(add_response.status_code, 302)
        self.assertEqual(response.status_code, 200)
        order = Order.objects.get(user=user, event=self.active_event)
        tickets = Ticket.objects.filter(order=order)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.ticket_type.code, EventTicketType.Code.VIP)
        self.assertEqual(order.unit_price_usd, Decimal("5.00"))
        self.assertEqual(order.total_usd, Decimal("10.00"))
        self.assertEqual(tickets.count(), 0)
        self.assertContains(response, "VIP")

    def test_add_ticket_rejects_when_exceeds_event_ticket_limit(self):
        user = User.objects.create_user(username="buyer_limit", password="StrongPass123!")
        self.active_event.ticket_limit = 1
        self.active_event.save(update_fields=["ticket_limit"])
        seeded_order = Order.objects.create(
            user=user,
            event=self.active_event,
            status=Order.Status.PAID,
        )
        Ticket.objects.create(
            order=seeded_order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
        )

        self.client.login(username="buyer_limit", password="StrongPass123!")
        response = self._add_ticket(self.active_event, quantity="2")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_user_can_accumulate_tickets_in_cart_for_same_event(self):
        user = User.objects.create_user(username="buyer_incremental", password="StrongPass123!")
        self.client.login(username="buyer_incremental", password="StrongPass123!")

        first = self._add_ticket(self.active_event, quantity="1")
        second = self._add_ticket(self.active_event, quantity="4")

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        cart = Cart.objects.get(user=user, status=Cart.Status.ACTIVE)
        self.assertEqual(cart.items.get(ticket_type=self.active_event.ensure_general_ticket_type()).quantity, 5)

    def test_checkout_does_not_assign_raffle_numbers_before_payment_confirmation(self):
        user = User.objects.create_user(username="buyer_random_numbers", password="StrongPass123!")
        self.active_event.ticket_limit = 10
        self.active_event.save(update_fields=["ticket_limit"])
        self.client.login(username="buyer_random_numbers", password="StrongPass123!")

        self._add_ticket(self.active_event, quantity="3")
        first = self._checkout()
        self._add_ticket(self.active_event, quantity="3")
        second = self._checkout()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        numbers = list(
            Ticket.objects.filter(event=self.active_event).values_list("raffle_number", flat=True)
        )
        self.assertEqual(len(numbers), 0)

    def test_checkout_does_not_create_new_tickets_before_payment_confirmation(self):
        user = User.objects.create_user(username="buyer_ordered_numbers", password="StrongPass123!")
        self.active_event.ticket_limit = 10
        self.active_event.save(update_fields=["ticket_limit"])
        self.client.login(username="buyer_ordered_numbers", password="StrongPass123!")

        existing_order = Order.objects.create(
            user=user,
            event=self.active_event,
            status=Order.Status.PAID,
        )
        Ticket.objects.create(
            order=existing_order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            raffle_number=1,
        )
        Ticket.objects.create(
            order=existing_order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            raffle_number=3,
        )

        self._add_ticket(self.active_event, quantity="3")
        response = self._checkout()
        self.assertEqual(response.status_code, 200)
        purchased_numbers = list(
            Ticket.objects.filter(order__user=user, event=self.active_event)
            .exclude(order=existing_order)
            .order_by("raffle_number")
            .values_list("raffle_number", flat=True)
        )
        self.assertEqual(purchased_numbers, [])

    def test_add_ticket_rejects_invalid_quantity(self):
        User.objects.create_user(username="buyer_bad_qty", password="StrongPass123!")
        self.client.login(username="buyer_bad_qty", password="StrongPass123!")

        response = self._add_ticket(self.active_event, quantity="0")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_add_ticket_rejects_finished_event(self):
        user = User.objects.create_user(username="buyer_finished_event", password="StrongPass123!")
        self.client.login(username="buyer_finished_event", password="StrongPass123!")

        response = self._add_ticket(self.ended_active_event)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.count(), 0)

    def test_add_ticket_rejects_event_without_end_datetime(self):
        user = User.objects.create_user(username="buyer_without_end", password="StrongPass123!")
        event_without_end = Event.objects.create(
            title="Evento sin cierre",
            datetime=datetime.now(timezone.utc) - timedelta(hours=1),
            end_datetime=None,
            status=Event.Status.ACTIVE,
        )
        self.client.login(username="buyer_without_end", password="StrongPass123!")

        response = self._add_ticket(event_without_end)

        self.assertEqual(response.status_code, 302)
        follow_response = self.client.get(response.url)
        self.assertContains(follow_response, "no tiene una fecha de finalizacion definida")
        self.assertEqual(CartItem.objects.count(), 0)

    def test_checkout_rejects_event_without_end_datetime(self):
        user = User.objects.create_user(username="buyer_checkout_without_end", password="StrongPass123!")
        event_without_end = Event.objects.create(
            title="Evento checkout sin cierre",
            datetime=datetime.now(timezone.utc) - timedelta(hours=1),
            end_datetime=None,
            status=Event.Status.ACTIVE,
        )
        cart = Cart.objects.create(user=user, status=Cart.Status.ACTIVE, event=event_without_end)
        CartItem.objects.create(
            cart=cart,
            item_type=CartItem.ItemType.TICKET,
            ticket_type=event_without_end.ensure_general_ticket_type(),
            quantity=1,
            unit_price_usd=event_without_end.unit_price_usd,
        )
        self.client.login(username="buyer_checkout_without_end", password="StrongPass123!")

        response = self.client.post(reverse("checkout_cart"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no tiene una fecha de finalizacion definida")
        self.assertFalse(Order.objects.filter(user=user, event=event_without_end).exists())

    def test_my_tickets_requires_authentication(self):
        response = self.client.get(reverse("my_tickets"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_download_ticket_qr_jpg_requires_ticket_owner(self):
        owner = User.objects.create_user(username="qr_owner", password="StrongPass123!")
        other = User.objects.create_user(username="qr_other", password="StrongPass123!")
        order = Order.objects.create(user=owner, event=self.active_event, status=Order.Status.PAID)
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            token_ref="qr.owner.token",
        )

        self.client.login(username="qr_other", password="StrongPass123!")
        response = self.client.get(reverse("download_ticket_qr_jpg", args=[ticket.pk]))
        self.assertEqual(response.status_code, 404)

    def test_download_ticket_qr_jpg_returns_attachment_for_owner(self):
        owner = User.objects.create_user(username="qr_owner_ok", password="StrongPass123!")
        order = Order.objects.create(user=owner, event=self.active_event, status=Order.Status.PAID)
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            token_ref="qr.owner.ok.token",
        )

        self.client.login(username="qr_owner_ok", password="StrongPass123!")
        response = self.client.get(reverse("download_ticket_qr_jpg", args=[ticket.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get("Content-Type"), "image/jpeg")
        self.assertIn("attachment", response.get("Content-Disposition", ""))

    def test_my_tickets_lists_only_current_user_tickets(self):
        owner = User.objects.create_user(username="owner1", password="StrongPass123!")
        other = User.objects.create_user(username="other1", password="StrongPass123!")

        owner_order = Order.objects.create(
            user=owner, event=self.active_event, status=Order.Status.PAID
        )
        owner_ticket = Ticket.objects.create(
            order=owner_order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            token_ref="owner.token",
        )

        other_order = Order.objects.create(
            user=other, event=self.active_event, status=Order.Status.PAID
        )
        Ticket.objects.create(
            order=other_order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            token_ref="other.token",
        )

        self.client.login(username="owner1", password="StrongPass123!")
        response = self.client.get(reverse("my_tickets"))
        detail_response = self.client.get(
            reverse("my_tickets"),
            {"event_id": str(self.active_event.pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tus eventos y entradas QR")
        self.assertContains(response, self.active_event.title)
        self.assertNotContains(response, "owner.token")
        self.assertNotContains(response, "other.token")
        self.assertContains(response, "1 entradas QR")
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, str(owner_ticket.ticket_uuid))
        self.assertNotContains(detail_response, "owner.token")
        self.assertNotContains(detail_response, "other.token")
        self.assertContains(detail_response, "data:image/png;base64,")

    def _deprecated_my_tickets_transfer_preview_shows_recipient_profile_name(self):
        owner = User.objects.create_user(
            username="owner_transfer_preview",
            password="StrongPass123!",
            email="owner_transfer_preview@example.com",
        )
        recipient = User.objects.create_user(
            username="recipient_transfer_preview",
            password="StrongPass123!",
            email="recipient_transfer_preview@example.com",
        )
        Profile.objects.create(user=recipient, display_name="Cristian")
        order = Order.objects.create(user=owner, event=self.active_event, status=Order.Status.PAID)
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            token_ref="transfer.preview.token",
        )

        self.client.login(username="owner_transfer_preview", password="StrongPass123!")
        response = self.client.post(
            reverse("my_tickets"),
            {
                "action": "preview",
                "ticket_id": str(ticket.pk),
                "target_email": "recipient_transfer_preview@example.com",
                "selected_event_id": str(self.active_event.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "¿Estas seguro de enviar boleta a")
        self.assertContains(response, "Cristian")
        self.assertEqual(response.context["transfer_state"]["mode"], "confirm")

    def _deprecated_my_tickets_transfer_confirm_moves_ticket_to_recipient(self):
        owner = User.objects.create_user(
            username="owner_transfer_confirm",
            password="StrongPass123!",
            email="owner_transfer_confirm@example.com",
        )
        recipient = User.objects.create_user(
            username="recipient_transfer_confirm",
            password="StrongPass123!",
            email="recipient_transfer_confirm@example.com",
        )
        Profile.objects.create(user=recipient, display_name="Maria")
        order = Order.objects.create(user=owner, event=self.active_event, status=Order.Status.PAID)
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            token_ref="transfer.confirm.token",
        )

        self.client.login(username="owner_transfer_confirm", password="StrongPass123!")
        preview = self.client.post(
            reverse("my_tickets"),
            {
                "action": "preview",
                "ticket_id": str(ticket.pk),
                "target_email": "recipient_transfer_confirm@example.com",
            },
        )
        self.assertEqual(preview.status_code, 200)
        transfer_token = preview.context["transfer_preview"]["transfer_token"]
        confirm = self.client.post(
            reverse("my_tickets"),
            {
                "action": "confirm",
                "ticket_id": str(ticket.pk),
                "transfer_token": transfer_token,
            },
            follow=True,
        )

        self.assertEqual(confirm.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.order.user_id, recipient.pk)
        self.assertFalse(
            Ticket.objects.filter(pk=ticket.pk, order__user=owner).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=recipient,
                title="Boleta recibida",
            ).exists()
        )

    def _deprecated_my_tickets_transfer_rejects_used_ticket(self):
        owner = User.objects.create_user(
            username="owner_transfer_used",
            password="StrongPass123!",
            email="owner_transfer_used@example.com",
        )
        recipient = User.objects.create_user(
            username="recipient_transfer_used",
            password="StrongPass123!",
            email="recipient_transfer_used@example.com",
        )
        order = Order.objects.create(user=owner, event=self.active_event, status=Order.Status.PAID)
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            status=Ticket.Status.USED,
            token_ref="transfer.used.token",
        )

        self.client.login(username="owner_transfer_used", password="StrongPass123!")
        response = self.client.post(
            reverse("my_tickets"),
            {
                "action": "preview",
                "ticket_id": str(ticket.pk),
                "target_email": recipient.email,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solo puedes transferir boletas no usadas")
        ticket.refresh_from_db()
        self.assertEqual(ticket.order.user_id, owner.pk)

    def _deprecated_my_tickets_transfer_rejects_self_transfer(self):
        owner = User.objects.create_user(
            username="owner_transfer_self",
            password="StrongPass123!",
            email="owner_transfer_self@example.com",
        )
        order = Order.objects.create(user=owner, event=self.active_event, status=Order.Status.PAID)
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            token_ref="transfer.self.token",
        )

        self.client.login(username="owner_transfer_self", password="StrongPass123!")
        response = self.client.post(
            reverse("my_tickets"),
            {
                "action": "preview",
                "ticket_id": str(ticket.pk),
                "target_email": owner.email,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No puedes transferirte una boleta a ti mismo")
        ticket.refresh_from_db()
        self.assertEqual(ticket.order.user_id, owner.pk)

    def _deprecated_my_tickets_transfer_rejects_unknown_email(self):
        owner = User.objects.create_user(
            username="owner_transfer_unknown",
            password="StrongPass123!",
            email="owner_transfer_unknown@example.com",
        )
        order = Order.objects.create(user=owner, event=self.active_event, status=Order.Status.PAID)
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            token_ref="transfer.unknown.token",
        )

        self.client.login(username="owner_transfer_unknown", password="StrongPass123!")
        response = self.client.post(
            reverse("my_tickets"),
            {
                "action": "preview",
                "ticket_id": str(ticket.pk),
                "target_email": "noexiste@example.com",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cuenta no encontrada")
        ticket.refresh_from_db()
        self.assertEqual(ticket.order.user_id, owner.pk)

    def _deprecated_my_tickets_transfer_is_disabled(self):
        owner = User.objects.create_user(
            username="owner_transfer_disabled",
            password="StrongPass123!",
            email="owner_transfer_disabled@example.com",
        )
        order = Order.objects.create(user=owner, event=self.active_event, status=Order.Status.PAID)
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            token_ref="transfer.disabled.token",
        )

        self.client.login(username="owner_transfer_disabled", password="StrongPass123!")
        response = self.client.post(
            reverse("my_tickets"),
            {
                "action": "preview",
                "ticket_id": str(ticket.pk),
                "target_email": "someone@example.com",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La transferencia de boletas no esta disponible.")
        ticket.refresh_from_db()
        self.assertEqual(ticket.order.user_id, owner.pk)

    def test_my_tickets_transfer_preview_shows_recipient_profile_name(self):
        owner = User.objects.create_user(
            username="owner_transfer_preview_new",
            password="StrongPass123!",
            email="owner_transfer_preview_new@example.com",
        )
        recipient = User.objects.create_user(
            username="recipient_transfer_preview_new",
            password="StrongPass123!",
            email="recipient_transfer_preview_new@example.com",
        )
        Profile.objects.create(user=recipient, display_name="Cristian")
        order = Order.objects.create(user=owner, event=self.active_event, status=Order.Status.PAID)
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            token_ref="transfer.preview.new.token",
        )

        self.client.login(username="owner_transfer_preview_new", password="StrongPass123!")
        response = self.client.post(
            reverse("my_tickets"),
            {
                "action": "preview",
                "ticket_id": str(ticket.pk),
                "target_email": recipient.email,
                "selected_event_id": str(self.active_event.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Estas seguro de transferir la boleta")
        self.assertContains(response, "Cristian")
        self.assertEqual(response.context["transfer_state"]["mode"], "confirm")

    def test_my_tickets_transfer_preview_shows_ticket_type_name(self):
        owner = User.objects.create_user(
            username="owner_transfer_preview_vip",
            password="StrongPass123!",
            email="owner_transfer_preview_vip@example.com",
        )
        recipient = User.objects.create_user(
            username="recipient_transfer_preview_vip",
            password="StrongPass123!",
            email="recipient_transfer_preview_vip@example.com",
        )
        vip_type = EventTicketType.objects.create(
            event=self.active_event,
            code=EventTicketType.Code.VIP,
            name="VIP",
            price_usd=Decimal("5.00"),
            stock_total=20,
            is_active=True,
            display_order=2,
            number_prefix="VIP",
        )
        order = Order.objects.create(
            user=owner,
            event=self.active_event,
            ticket_type=vip_type,
            status=Order.Status.PAID,
            unit_price_usd=Decimal("5.00"),
            quantity=1,
            total_usd=Decimal("5.00"),
        )
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            ticket_type=vip_type,
            status=Ticket.Status.UNUSED,
            token_ref="transfer.preview.vip.token",
        )

        self.client.login(username="owner_transfer_preview_vip", password="StrongPass123!")
        response = self.client.post(
            reverse("my_tickets"),
            {
                "action": "preview",
                "ticket_id": str(ticket.pk),
                "target_email": recipient.email,
                "selected_event_id": str(self.active_event.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "boleta VIP")
        self.assertContains(response, ticket.raffle_number_display)

    def test_my_tickets_transfer_confirm_moves_ticket_to_recipient(self):
        owner = User.objects.create_user(
            username="owner_transfer_confirm_new",
            password="StrongPass123!",
            email="owner_transfer_confirm_new@example.com",
        )
        recipient = User.objects.create_user(
            username="recipient_transfer_confirm_new",
            password="StrongPass123!",
            email="recipient_transfer_confirm_new@example.com",
        )
        Profile.objects.create(user=recipient, display_name="Maria")
        order = Order.objects.create(
            user=owner,
            event=self.active_event,
            status=Order.Status.PAID,
            quantity=2,
            total_usd=Decimal("5.00"),
            unit_price_usd=Decimal("2.50"),
        )
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            token_ref="transfer.confirm.new.token",
        )
        other_ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            token_ref="transfer.confirm.new.other.token",
        )

        self.client.login(username="owner_transfer_confirm_new", password="StrongPass123!")
        preview = self.client.post(
            reverse("my_tickets"),
            {
                "action": "preview",
                "ticket_id": str(ticket.pk),
                "target_email": recipient.email,
                "selected_event_id": str(self.active_event.pk),
            },
        )

        self.assertEqual(preview.status_code, 200)
        transfer_token = preview.context["transfer_state"]["transfer_token"]
        confirm = self.client.post(
            reverse("my_tickets"),
            {
                "action": "confirm",
                "ticket_id": str(ticket.pk),
                "transfer_token": transfer_token,
                "selected_event_id": str(self.active_event.pk),
            },
            follow=True,
        )

        self.assertEqual(confirm.status_code, 200)
        ticket.refresh_from_db()
        other_ticket.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(ticket.order.user_id, recipient.pk)
        self.assertEqual(other_ticket.order.user_id, owner.pk)
        self.assertEqual(order.quantity, 1)
        self.assertEqual(order.total_usd, Decimal("2.50"))
        self.assertTrue(Notification.objects.filter(user=recipient, title="Boleta recibida").exists())
        self.assertContains(confirm, "fue transferida a Maria")

    def test_transfer_notification_includes_ticket_type_name(self):
        owner = User.objects.create_user(
            username="owner_transfer_notify_vip",
            password="StrongPass123!",
            email="owner_transfer_notify_vip@example.com",
        )
        recipient = User.objects.create_user(
            username="recipient_transfer_notify_vip",
            password="StrongPass123!",
            email="recipient_transfer_notify_vip@example.com",
        )
        vip_type = EventTicketType.objects.create(
            event=self.active_event,
            code=EventTicketType.Code.VIP,
            name="VIP",
            price_usd=Decimal("5.00"),
            stock_total=20,
            is_active=True,
            display_order=2,
            number_prefix="VIP",
        )
        order = Order.objects.create(
            user=owner,
            event=self.active_event,
            ticket_type=vip_type,
            status=Order.Status.PAID,
            unit_price_usd=Decimal("5.00"),
            quantity=1,
            total_usd=Decimal("5.00"),
        )
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            ticket_type=vip_type,
            status=Ticket.Status.UNUSED,
            token_ref="transfer.notify.vip.token",
        )

        self.client.login(username="owner_transfer_notify_vip", password="StrongPass123!")
        preview = self.client.post(
            reverse("my_tickets"),
            {
                "action": "preview",
                "ticket_id": str(ticket.pk),
                "target_email": recipient.email,
                "selected_event_id": str(self.active_event.pk),
            },
        )
        transfer_token = preview.context["transfer_state"]["transfer_token"]
        self.client.post(
            reverse("my_tickets"),
            {
                "action": "confirm",
                "ticket_id": str(ticket.pk),
                "transfer_token": transfer_token,
                "selected_event_id": str(self.active_event.pk),
            },
            follow=True,
        )

        notification = Notification.objects.get(user=recipient, title="Boleta recibida")
        self.assertIn("boleta VIP", notification.body)

    def test_my_tickets_shows_product_redemption_details(self):
        owner = User.objects.create_user(username="owner_products", password="StrongPass123!")
        order = Order.objects.create(
            user=owner,
            event=self.active_event,
            status=Order.Status.PAID,
            total_usd=Decimal("25.00"),
        )
        product = Product.objects.create(
            event=self.active_event,
            name="Camiseta Carnaval",
            price_usd=Decimal("25.00"),
            is_active=True,
            has_variants=True,
        )
        variant = ProductVariant.objects.create(
            product=product,
            name="M / Roja",
            stock_total=10,
            is_active=True,
        )
        OrderItem.objects.create(
            order=order,
            event=self.active_event,
            item_type=OrderItem.ItemType.PRODUCT,
            product_variant=variant,
            item_name="Camiseta Carnaval",
            variant_name="M / Roja",
            unit_price_usd=Decimal("25.00"),
            quantity=1,
            total_usd=Decimal("25.00"),
        )
        redemption = ProductRedemption.objects.create(
            order=order,
            user=owner,
            event=self.active_event,
        )

        self.client.login(username="owner_products", password="StrongPass123!")
        response = self.client.get(reverse("my_tickets"), {"event_id": str(self.active_event.pk)})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, redemption.code)
        self.assertContains(response, "Productos para reclamar")
        self.assertContains(response, "Camiseta Carnaval")

    def test_my_tickets_transfer_rejects_used_ticket(self):
        owner = User.objects.create_user(
            username="owner_transfer_used_new",
            password="StrongPass123!",
            email="owner_transfer_used_new@example.com",
        )
        recipient = User.objects.create_user(
            username="recipient_transfer_used_new",
            password="StrongPass123!",
            email="recipient_transfer_used_new@example.com",
        )
        order = Order.objects.create(user=owner, event=self.active_event, status=Order.Status.PAID)
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            status=Ticket.Status.USED,
            token_ref="transfer.used.new.token",
        )

        self.client.login(username="owner_transfer_used_new", password="StrongPass123!")
        response = self.client.post(
            reverse("my_tickets"),
            {
                "action": "preview",
                "ticket_id": str(ticket.pk),
                "target_email": recipient.email,
                "selected_event_id": str(self.active_event.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solo puedes transferir boletas disponibles.")
        ticket.refresh_from_db()
        self.assertEqual(ticket.order.user_id, owner.pk)

    def test_my_tickets_transfer_rejects_unknown_email(self):
        owner = User.objects.create_user(
            username="owner_transfer_unknown_new",
            password="StrongPass123!",
            email="owner_transfer_unknown_new@example.com",
        )
        order = Order.objects.create(user=owner, event=self.active_event, status=Order.Status.PAID)
        ticket = Ticket.objects.create(
            order=order,
            event=self.active_event,
            status=Ticket.Status.UNUSED,
            token_ref="transfer.unknown.new.token",
        )

        self.client.login(username="owner_transfer_unknown_new", password="StrongPass123!")
        response = self.client.post(
            reverse("my_tickets"),
            {
                "action": "preview",
                "ticket_id": str(ticket.pk),
                "target_email": "noexiste@example.com",
                "selected_event_id": str(self.active_event.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Usuario invalido.")
        ticket.refresh_from_db()
        self.assertEqual(ticket.order.user_id, owner.pk)

    def test_notifications_list_marks_unread_as_read(self):
        user = User.objects.create_user(
            username="notify_user",
            password="StrongPass123!",
            email="notify_user@example.com",
        )
        Notification.objects.create(
            user=user,
            title="Notificacion de prueba",
            body="Texto de prueba",
            is_read=False,
        )

        self.client.login(username="notify_user", password="StrongPass123!")
        response = self.client.get(reverse("notifications_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Notificacion de prueba")
        self.assertEqual(Notification.objects.filter(user=user, is_read=False).count(), 0)

    def test_notifications_list_shows_generic_detail_link(self):
        user = User.objects.create_user(
            username="notify_detail_user",
            password="StrongPass123!",
            email="notify_detail_user@example.com",
        )
        Notification.objects.create(
            user=user,
            title="Boleta recibida",
            body="Recibiste la boleta VIP #VIP-01.",
            link_url=reverse("my_tickets"),
            is_read=False,
        )

        self.client.login(username="notify_detail_user", password="StrongPass123!")
        response = self.client.get(reverse("notifications_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ver detalle")

    def test_notifications_unread_api_returns_count_and_latest(self):
        user = User.objects.create_user(
            username="notify_api_user",
            password="StrongPass123!",
            email="notify_api_user@example.com",
        )
        Notification.objects.create(
            user=user,
            title="N1",
            body="B1",
            is_read=True,
        )
        latest = Notification.objects.create(
            user=user,
            title="N2",
            body="B2",
            is_read=False,
        )

        self.client.login(username="notify_api_user", password="StrongPass123!")
        response = self.client.get(reverse("notifications_unread_api"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["unread_count"], 1)
        self.assertEqual(payload["latest"]["id"], latest.id)
        self.assertEqual(payload["latest"]["title"], "N2")

    def test_notifications_unread_api_requires_login(self):
        response = self.client.get(reverse("notifications_unread_api"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_user_can_delete_own_notification(self):
        user = User.objects.create_user(
            username="notify_delete_owner",
            password="StrongPass123!",
            email="notify_delete_owner@example.com",
        )
        notification = Notification.objects.create(
            user=user,
            title="Eliminar esta",
            body="body",
            is_read=False,
        )
        self.client.login(username="notify_delete_owner", password="StrongPass123!")
        response = self.client.post(reverse("delete_notification", args=[notification.pk]))

        self.assertRedirects(response, reverse("notifications_list"))
        self.assertFalse(Notification.objects.filter(pk=notification.pk).exists())

    def test_user_cannot_delete_notification_from_another_user(self):
        owner = User.objects.create_user(
            username="notify_owner_other",
            password="StrongPass123!",
            email="notify_owner_other@example.com",
        )
        other = User.objects.create_user(
            username="notify_other_user",
            password="StrongPass123!",
            email="notify_other_user@example.com",
        )
        notification = Notification.objects.create(
            user=owner,
            title="Privada",
            body="body",
            is_read=False,
        )
        self.client.login(username="notify_other_user", password="StrongPass123!")
        response = self.client.post(reverse("delete_notification", args=[notification.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Notification.objects.filter(pk=notification.pk).exists())

    def test_reply_to_comment_creates_notification_for_parent_owner(self):
        author = User.objects.create_user(username="notify_comment_author", password="StrongPass123!")
        replier = User.objects.create_user(username="notify_comment_replier", password="StrongPass123!")
        parent = Review.objects.create(
            event=self.active_event,
            user=author,
            comment="Comentario inicial",
        )
        self.client.login(username="notify_comment_replier", password="StrongPass123!")
        response = self.client.post(
            reverse("submit_event_review", args=[self.active_event.pk]),
            {"comment": "Respuesta al comentario", "parent_id": str(parent.pk)},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Notification.objects.filter(
                user=author,
                title="Respondieron tu comentario",
            ).exists()
        )

    def test_submit_review_requires_authentication(self):
        response = self.client.post(
            reverse("submit_event_review", args=[self.active_event.pk]),
            {"comment": "Muy buen evento"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_authenticated_user_can_create_review_for_event(self):
        user = User.objects.create_user(username="review_user", password="StrongPass123!")
        self.client.login(username="review_user", password="StrongPass123!")

        response = self.client.post(
            reverse("submit_event_review", args=[self.active_event.pk]),
            {"comment": "Excelente organizacion"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu opinión se guardó correctamente.")
        self.assertTrue(
            Review.objects.filter(
                event=self.active_event,
                user=user,
                comment="Excelente organizacion",
            ).exists()
        )

    def test_authenticated_user_can_create_review_for_scheduled_active_event(self):
        user = User.objects.create_user(username="review_scheduled_user", password="StrongPass123!")
        self.client.login(username="review_scheduled_user", password="StrongPass123!")

        response = self.client.post(
            reverse("submit_event_review", args=[self.scheduled_active_event.pk]),
            {"comment": "Quiero ir a este evento"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu opinión se guardó correctamente.")
        self.assertTrue(
            Review.objects.filter(
                event=self.scheduled_active_event,
                user=user,
                comment="Quiero ir a este evento",
            ).exists()
        )

    def test_submitting_multiple_reviews_creates_more_than_one(self):
        user = User.objects.create_user(username="review_twice", password="StrongPass123!")
        self.client.login(username="review_twice", password="StrongPass123!")

        self.client.post(
            reverse("submit_event_review", args=[self.active_event.pk]),
            {"comment": "Primera opinion"},
        )
        self.client.post(
            reverse("submit_event_review", args=[self.active_event.pk]),
            {"comment": "Segunda opinion distinta"},
        )

        reviews = Review.objects.filter(event=self.active_event, user=user).order_by("created_at")
        self.assertEqual(reviews.count(), 2)
        self.assertEqual(reviews.first().comment, "Primera opinion")
        self.assertEqual(reviews.last().comment, "Segunda opinion distinta")

    def test_rejects_same_consecutive_review_comment(self):
        user = User.objects.create_user(username="review_spam", password="StrongPass123!")
        self.client.login(username="review_spam", password="StrongPass123!")

        self.client.post(
            reverse("submit_event_review", args=[self.active_event.pk]),
            {"comment": "Mismo texto"},
        )
        response = self.client.post(
            reverse("submit_event_review", args=[self.active_event.pk]),
            {"comment": "Mismo texto"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No publiques el mismo comentario de forma consecutiva.")
        self.assertEqual(Review.objects.filter(event=self.active_event, user=user).count(), 1)

    def test_limits_reviews_per_user_and_event_to_five(self):
        user = User.objects.create_user(username="review_limit", password="StrongPass123!")
        self.client.login(username="review_limit", password="StrongPass123!")

        comments = [
            "Comentario 1",
            "Comentario 2",
            "Comentario 3",
            "Comentario 4",
            "Comentario 5",
        ]
        for comment in comments:
            self.client.post(
                reverse("submit_event_review", args=[self.active_event.pk]),
                {"comment": comment},
            )

        blocked = self.client.post(
            reverse("submit_event_review", args=[self.active_event.pk]),
            {"comment": "Comentario 6"},
            follow=True,
        )

        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "Alcanzaste el límite de 5 opiniones para esta obra.")
        self.assertEqual(Review.objects.filter(event=self.active_event, user=user).count(), 5)

    def test_admin_can_delete_event_review(self):
        reviewer = User.objects.create_user(username="review_delete_user", password="StrongPass123!")
        admin = User.objects.create_user(
            username="review_delete_admin",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        review = Review.objects.create(
            event=self.active_event,
            user=reviewer,
            comment="Comentario a eliminar",
        )

        self.client.login(username="review_delete_admin", password="StrongPass123!")
        response = self.client.post(
            reverse("delete_event_review", args=[self.active_event.pk, review.pk]),
        )

        self.assertRedirects(
            response,
            f"{reverse('event_detail', args=[self.active_event.pk])}#opiniones",
            fetch_redirect_response=False,
        )
        self.assertFalse(Review.objects.filter(pk=review.pk).exists())

    def test_authenticated_user_can_reply_to_review(self):
        author = User.objects.create_user(username="reply_author", password="StrongPass123!")
        replier = User.objects.create_user(username="reply_user", password="StrongPass123!")
        parent = Review.objects.create(
            event=self.active_event,
            user=author,
            comment="Comentario principal",
        )
        self.client.login(username="reply_user", password="StrongPass123!")

        response = self.client.post(
            reverse("submit_event_review", args=[self.active_event.pk]),
            {"comment": "Esta es una respuesta", "parent_id": str(parent.pk)},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Review.objects.filter(
                event=self.active_event,
                user=replier,
                parent=parent,
                comment="Esta es una respuesta",
            ).exists()
        )

    def test_rejects_review_with_blocked_language(self):
        user = User.objects.create_user(username="review_clean", password="StrongPass123!")
        self.client.login(username="review_clean", password="StrongPass123!")

        response = self.client.post(
            reverse("submit_event_review", args=[self.active_event.pk]),
            {"comment": "Ese evento es una m13rda"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "lenguaje no permitido")
        self.assertFalse(
            Review.objects.filter(
                event=self.active_event,
                user=user,
                comment__icontains="m13rda",
            ).exists()
        )

    def test_rejects_review_with_blocked_language_variants(self):
        user = User.objects.create_user(username="review_clean_2", password="StrongPass123!")
        self.client.login(username="review_clean_2", password="StrongPass123!")

        blocked_samples = [
            "Ese man es hpta",
            "Qué hijo de puta",
            "Ese mk no respeta",
        ]
        for text in blocked_samples:
            response = self.client.post(
                reverse("submit_event_review", args=[self.active_event.pk]),
                {"comment": text},
                follow=True,
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "lenguaje no permitido")

        self.assertEqual(Review.objects.filter(event=self.active_event, user=user).count(), 0)

    def test_user_can_delete_own_event_review(self):
        reviewer = User.objects.create_user(username="review_owner_delete", password="StrongPass123!")
        review = Review.objects.create(
            event=self.active_event,
            user=reviewer,
            comment="Comentario propio",
        )
        self.client.login(username="review_owner_delete", password="StrongPass123!")

        response = self.client.post(
            reverse("delete_event_review", args=[self.active_event.pk, review.pk]),
        )

        self.assertRedirects(
            response,
            f"{reverse('event_detail', args=[self.active_event.pk])}#opiniones",
            fetch_redirect_response=False,
        )
        self.assertFalse(Review.objects.filter(pk=review.pk).exists())

    def test_user_cannot_delete_other_user_review(self):
        reviewer = User.objects.create_user(username="review_author_blocked", password="StrongPass123!")
        other_user = User.objects.create_user(username="review_other_blocked", password="StrongPass123!")
        review = Review.objects.create(
            event=self.active_event,
            user=reviewer,
            comment="Comentario de otro usuario",
        )
        self.client.login(username="review_other_blocked", password="StrongPass123!")

        response = self.client.post(
            reverse("delete_event_review", args=[self.active_event.pk, review.pk]),
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Review.objects.filter(pk=review.pk).exists())

    def test_admin_can_create_review_and_is_labeled_as_admin(self):
        admin = User.objects.create_user(
            username="review_admin_user",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        Profile.objects.create(user=admin, display_name="Cristian")
        self.client.login(username="review_admin_user", password="StrongPass123!")

        post_response = self.client.post(
            reverse("submit_event_review", args=[self.active_event.pk]),
            {"comment": "Comentario del admin"},
            follow=True,
        )

        self.assertEqual(post_response.status_code, 200)
        self.assertTrue(
            Review.objects.filter(
                event=self.active_event,
                user=admin,
                comment="Comentario del admin",
            ).exists()
        )
        detail_response = self.client.get(reverse("event_detail", args=[self.active_event.pk]))
        self.assertContains(detail_response, "Cristian")
        self.assertContains(detail_response, "Admin")

    def test_admin_sees_review_textarea_without_clicking_save(self):
        admin = User.objects.create_user(
            username="admin_form_visible",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username="admin_form_visible", password="StrongPass123!")
        response = self.client.get(reverse("event_detail", args=[self.active_event.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="comment"')

    def test_authenticated_user_can_like_review(self):
        author = User.objects.create_user(username="review_author_like", password="StrongPass123!")
        reactor = User.objects.create_user(username="review_reactor_like", password="StrongPass123!")
        review = Review.objects.create(
            event=self.active_event,
            user=author,
            comment="Comentario para reaccionar",
        )
        self.client.login(username="review_reactor_like", password="StrongPass123!")
        response = self.client.post(
            reverse("react_to_review", args=[self.active_event.pk, review.pk, "LIKE"]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ReviewReaction.objects.filter(
                review=review,
                user=reactor,
                reaction=ReviewReaction.Reaction.LIKE,
            ).exists()
        )

    def test_user_can_switch_like_to_dislike(self):
        author = User.objects.create_user(username="review_author_switch", password="StrongPass123!")
        reactor = User.objects.create_user(username="review_reactor_switch", password="StrongPass123!")
        review = Review.objects.create(
            event=self.active_event,
            user=author,
            comment="Comentario para cambiar reaccion",
        )
        self.client.login(username="review_reactor_switch", password="StrongPass123!")
        self.client.post(reverse("react_to_review", args=[self.active_event.pk, review.pk, "LIKE"]))
        self.client.post(reverse("react_to_review", args=[self.active_event.pk, review.pk, "DISLIKE"]))

        reaction = ReviewReaction.objects.get(review=review, user=reactor)
        self.assertEqual(reaction.reaction, ReviewReaction.Reaction.DISLIKE)

    def test_authenticated_user_can_report_review(self):
        author = User.objects.create_user(username="review_author_report", password="StrongPass123!")
        reporter = User.objects.create_user(username="review_reporter", password="StrongPass123!")
        admin = User.objects.create_user(
            username="review_admin_report_notification",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        review = Review.objects.create(
            event=self.active_event,
            user=author,
            comment="Comentario reportable",
        )
        self.client.login(username="review_reporter", password="StrongPass123!")
        response = self.client.post(
            reverse("report_review", args=[self.active_event.pk, review.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reporte enviado correctamente")
        self.assertTrue(
            ReviewReport.objects.filter(
                review=review,
                reporter=reporter,
                status=ReviewReport.Status.PENDING,
            ).exists()
        )
        admin_notification = Notification.objects.filter(
            user=admin,
            title="Nuevo reporte de comentario",
        ).first()
        self.assertIsNotNone(admin_notification)
        self.assertEqual(admin_notification.link_url, reverse("review_reports_list"))

    def test_user_cannot_report_own_review(self):
        author = User.objects.create_user(username="review_author_self_report", password="StrongPass123!")
        review = Review.objects.create(
            event=self.active_event,
            user=author,
            comment="Comentario propio",
        )
        self.client.login(username="review_author_self_report", password="StrongPass123!")
        response = self.client.post(
            reverse("report_review", args=[self.active_event.pk, review.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No puedes reportar tu propio comentario")
        self.assertFalse(ReviewReport.objects.filter(review=review, reporter=author).exists())

    def test_admin_can_view_review_reports(self):
        author = User.objects.create_user(username="review_author_list_report", password="StrongPass123!")
        reporter = User.objects.create_user(username="review_reporter_list_report", password="StrongPass123!")
        admin = User.objects.create_user(
            username="review_admin_list_report",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        review = Review.objects.create(
            event=self.active_event,
            user=author,
            comment="Comentario reportado para lista",
        )
        ReviewReport.objects.create(review=review, reporter=reporter)

        self.client.login(username="review_admin_list_report", password="StrongPass123!")
        response = self.client.get(reverse("review_reports_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reportes de")
        self.assertContains(response, "comentarios")
        self.assertContains(response, "Comentario reportado para lista")
        self.assertContains(
            response,
            f"{reverse('event_detail', args=[self.active_event.pk])}#review-{review.pk}",
        )

    def test_admin_can_omit_review_report(self):
        author = User.objects.create_user(username="review_author_omit_report", password="StrongPass123!")
        reporter = User.objects.create_user(username="review_reporter_omit_report", password="StrongPass123!")
        admin = User.objects.create_user(
            username="review_admin_omit_report",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        review = Review.objects.create(
            event=self.active_event,
            user=author,
            comment="Comentario para omitir",
        )
        report = ReviewReport.objects.create(review=review, reporter=reporter)

        self.client.login(username="review_admin_omit_report", password="StrongPass123!")
        response = self.client.post(reverse("omit_review_report", args=[report.pk]))

        self.assertRedirects(response, reverse("review_reports_list"))
        report.refresh_from_db()
        self.assertEqual(report.status, ReviewReport.Status.OMITTED)

    def test_admin_can_delete_reported_review(self):
        author = User.objects.create_user(username="review_author_delete_report", password="StrongPass123!")
        reporter = User.objects.create_user(username="review_reporter_delete_report", password="StrongPass123!")
        admin = User.objects.create_user(
            username="review_admin_delete_report",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        review = Review.objects.create(
            event=self.active_event,
            user=author,
            comment="Comentario para eliminar por reporte",
        )
        report = ReviewReport.objects.create(review=review, reporter=reporter)

        self.client.login(username="review_admin_delete_report", password="StrongPass123!")
        response = self.client.post(reverse("delete_reported_review", args=[report.pk]))

        self.assertRedirects(response, reverse("review_reports_list"))
        self.assertFalse(Review.objects.filter(pk=review.pk).exists())


class CartViewsTests(TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.user = User.objects.create_user(username="cart_customer", password="StrongPass123!")
        self.event = Event.objects.create(
            title="Evento carrito",
            datetime=now - timedelta(hours=1),
            end_datetime=now + timedelta(days=1),
            status=Event.Status.ACTIVE,
            unit_price_usd=Decimal("20.00"),
            ticket_limit=50,
        )
        self.other_event = Event.objects.create(
            title="Otro evento carrito",
            datetime=now - timedelta(hours=1),
            end_datetime=now + timedelta(days=2),
            status=Event.Status.ACTIVE,
            unit_price_usd=Decimal("18.00"),
            ticket_limit=40,
        )
        self.general_type = self.event.ensure_general_ticket_type()
        self.other_event.ensure_general_ticket_type()
        self.product = Product.objects.create(
            event=self.event,
            name="Gorra Carnaval",
            description="Merch oficial",
            price_usd=Decimal("12.00"),
            is_active=True,
            has_variants=False,
        )
        self.product_variant = ProductVariant.objects.create(
            product=self.product,
            name="Unidad",
            stock_total=10,
            is_active=True,
        )
        self.variant_product = Product.objects.create(
            event=self.event,
            name="Camiseta Carnaval",
            description="Edicion limitada",
            price_usd=Decimal("25.00"),
            is_active=True,
            has_variants=True,
        )
        self.variant_product_red = ProductVariant.objects.create(
            product=self.variant_product,
            name="M / Roja",
            stock_total=8,
            is_active=True,
        )
        self.variant_product_black = ProductVariant.objects.create(
            product=self.variant_product,
            name="L / Negra",
            stock_total=4,
            is_active=True,
        )

    def test_user_can_add_ticket_to_cart(self):
        self.client.login(username="cart_customer", password="StrongPass123!")

        response = self.client.post(
            reverse("add_ticket_to_cart", args=[self.event.pk]),
            {"ticket_type": EventTicketType.Code.GENERAL, "quantity": "3", "next": reverse("event_detail", args=[self.event.pk])},
        )

        self.assertRedirects(response, reverse("event_detail", args=[self.event.pk]))
        cart = Cart.objects.get(user=self.user, status=Cart.Status.ACTIVE)
        item = cart.items.get(ticket_type=self.general_type)
        self.assertEqual(item.quantity, 3)

    def test_user_can_add_ticket_to_cart_before_event_start_when_active(self):
        self.event.datetime = datetime.now(timezone.utc) + timedelta(days=2)
        self.event.end_datetime = self.event.datetime + timedelta(hours=4)
        self.event.status = Event.Status.ACTIVE
        self.event.save(update_fields=["datetime", "end_datetime", "status"])
        self.client.login(username="cart_customer", password="StrongPass123!")

        response = self.client.post(
            reverse("add_ticket_to_cart", args=[self.event.pk]),
            {"ticket_type": EventTicketType.Code.GENERAL, "quantity": "1", "next": reverse("event_detail", args=[self.event.pk])},
        )

        self.assertRedirects(response, reverse("event_detail", args=[self.event.pk]))
        cart = Cart.objects.get(user=self.user, status=Cart.Status.ACTIVE)
        self.assertTrue(cart.items.filter(ticket_type=self.general_type).exists())

    def test_event_detail_shows_cart_added_modal_after_adding_ticket(self):
        self.client.login(username="cart_customer", password="StrongPass123!")

        response = self.client.post(
            reverse("add_ticket_to_cart", args=[self.event.pk]),
            {"ticket_type": EventTicketType.Code.GENERAL, "quantity": "1", "next": reverse("event_detail", args=[self.event.pk])},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="cart-added-modal"', html=False)
        self.assertContains(response, "Boleta agregada")
        self.assertContains(response, "Continuar comprando")
        self.assertContains(response, reverse("cart_detail"))

    def test_event_detail_shows_active_products(self):
        self.client.login(username="cart_customer", password="StrongPass123!")

        response = self.client.get(reverse("event_detail", args=[self.event.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Productos del evento")
        self.assertContains(response, "Gorra Carnaval")

    def test_user_can_add_product_to_cart(self):
        self.client.login(username="cart_customer", password="StrongPass123!")

        response = self.client.post(
            reverse("add_product_to_cart", args=[self.event.pk]),
            {"product_variant": str(self.product_variant.pk), "quantity": "2", "next": reverse("event_detail", args=[self.event.pk])},
        )

        self.assertRedirects(response, reverse("event_detail", args=[self.event.pk]))
        cart = Cart.objects.get(user=self.user, status=Cart.Status.ACTIVE)
        item = cart.items.get(product_variant=self.product_variant)
        self.assertEqual(item.quantity, 2)

    def test_user_can_add_product_variant_to_cart(self):
        self.client.login(username="cart_customer", password="StrongPass123!")

        response = self.client.post(
            reverse("add_product_to_cart", args=[self.event.pk]),
            {"product_variant": str(self.variant_product_red.pk), "quantity": "3", "next": reverse("event_detail", args=[self.event.pk])},
        )

        self.assertRedirects(response, reverse("event_detail", args=[self.event.pk]))
        cart = Cart.objects.get(user=self.user, status=Cart.Status.ACTIVE)
        item = cart.items.get(product_variant=self.variant_product_red)
        self.assertEqual(item.quantity, 3)
        self.assertEqual(item.item_name, "Evento carrito - Camiseta Carnaval")

    def test_add_product_to_cart_rejects_insufficient_stock(self):
        self.client.login(username="cart_customer", password="StrongPass123!")

        response = self.client.post(
            reverse("add_product_to_cart", args=[self.event.pk]),
            {"product_variant": str(self.product_variant.pk), "quantity": "50"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No hay suficiente stock disponible")
        self.assertFalse(CartItem.objects.filter(product_variant=self.product_variant).exists())

    def test_logged_user_sees_cart_link_in_top_navigation(self):
        self.client.login(username="cart_customer", password="StrongPass123!")
        self.client.post(
            reverse("add_ticket_to_cart", args=[self.event.pk]),
            {"ticket_type": EventTicketType.Code.GENERAL, "quantity": "3"},
        )

        response = self.client.get(reverse("event_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("cart_detail"))
        self.assertContains(response, "Carrito (3)")

    def test_user_can_update_and_clear_cart(self):
        self.client.login(username="cart_customer", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "1"})
        cart = Cart.objects.get(user=self.user, status=Cart.Status.ACTIVE)
        item = cart.items.get(ticket_type=self.general_type)

        update_response = self.client.post(reverse("update_cart_item", args=[item.pk]), {"quantity": "4"})
        item.refresh_from_db()
        clear_response = self.client.post(reverse("clear_cart"))

        self.assertRedirects(update_response, reverse("cart_detail"))
        self.assertRedirects(clear_response, reverse("cart_detail"))
        self.assertEqual(item.quantity, 4)
        cart.refresh_from_db()
        self.assertEqual(cart.items.count(), 0)
        self.assertIsNone(cart.event)

    def test_user_can_update_product_quantity_in_cart(self):
        self.client.login(username="cart_customer", password="StrongPass123!")
        self.client.post(
            reverse("add_product_to_cart", args=[self.event.pk]),
            {"product_variant": str(self.product_variant.pk), "quantity": "2"},
        )
        cart = Cart.objects.get(user=self.user, status=Cart.Status.ACTIVE)
        item = cart.items.get(product_variant=self.product_variant)

        response = self.client.post(reverse("update_cart_item", args=[item.pk]), {"quantity": "4"})

        self.assertRedirects(response, reverse("cart_detail"))
        item.refresh_from_db()
        self.assertEqual(item.quantity, 4)

    def test_update_cart_item_rejects_invalid_quantity(self):
        self.client.login(username="cart_customer", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "2"})
        cart = Cart.objects.get(user=self.user, status=Cart.Status.ACTIVE)
        item = cart.items.get(ticket_type=self.general_type)

        response = self.client.post(
            reverse("update_cart_item", args=[item.pk]),
            {"quantity": "abc"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La cantidad seleccionada no es valida.")
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)

    def test_ticket_cart_shows_ticket_copy(self):
        self.client.login(username="cart_customer", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "2"})

        response = self.client.get(reverse("cart_detail"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tus boletas QR se generaran al confirmar la compra.")

    def test_cart_shows_product_copy(self):
        self.client.login(username="cart_customer", password="StrongPass123!")
        self.client.post(
            reverse("add_product_to_cart", args=[self.event.pk]),
            {"product_variant": str(self.product_variant.pk), "quantity": "2"},
        )

        response = self.client.get(reverse("cart_detail"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Producto")
        self.assertContains(response, "codigo de reclamacion")

    def test_cart_separates_tickets_and_products(self):
        self.client.login(username="cart_customer", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "1"})
        self.client.post(
            reverse("add_product_to_cart", args=[self.event.pk]),
            {"product_variant": str(self.variant_product_red.pk), "quantity": "1"},
        )

        response = self.client.get(reverse("cart_detail"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Boletas en tu carrito")
        self.assertContains(response, "Productos en tu carrito")


class CheckoutFlowTests(TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.user = User.objects.create_user(username="checkout_user", password="StrongPass123!")
        self.event = Event.objects.create(
            title="Evento checkout",
            datetime=now - timedelta(hours=1),
            end_datetime=now + timedelta(days=1),
            status=Event.Status.ACTIVE,
            unit_price_usd=Decimal("20.00"),
            ticket_limit=50,
        )
        self.general_type = self.event.ensure_general_ticket_type()
        self.product = Product.objects.create(
            event=self.event,
            name="Camiseta Carnaval",
            description="Merch oficial",
            price_usd=Decimal("25.00"),
            is_active=True,
            has_variants=True,
        )
        self.product_variant = ProductVariant.objects.create(
            product=self.product,
            name="M / Roja",
            stock_total=10,
            is_active=True,
        )
    def test_checkout_page_shows_cart_summary(self):
        self.client.login(username="checkout_user", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "2"})

        response = self.client.get(reverse("checkout_cart"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Resumen de")
        self.assertContains(response, "Evento checkout - General")

    def test_checkout_page_shows_payment_loading_overlay(self):
        self.client.login(username="checkout_user", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "1"})

        response = self.client.get(reverse("checkout_cart"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="payment-loading-overlay"', html=False)
        self.assertContains(response, "Redirigiendo a Stripe")
        self.assertContains(response, "BoletasQR_20260603.png")

    def test_checkout_shows_ticket_copy(self):
        self.client.login(username="checkout_user", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "2"})

        response = self.client.get(reverse("checkout_cart"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Al darle a pagar te enviaremos directo a Stripe.")

    def test_checkout_page_shows_products_in_summary(self):
        self.client.login(username="checkout_user", password="StrongPass123!")
        self.client.post(
            reverse("add_product_to_cart", args=[self.event.pk]),
            {"product_variant": str(self.product_variant.pk), "quantity": "2"},
        )

        response = self.client.get(reverse("checkout_cart"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Producto")
        self.assertContains(response, "Camiseta Carnaval")

    @patch("core.views.create_checkout_session")
    def test_checkout_creates_pending_order_and_redirects_directly_to_stripe(self, mocked_create_checkout_session):
        class FakeSession:
            id = "cs_test_checkout_direct"
            url = "https://checkout.stripe.test/session/direct"

        mocked_create_checkout_session.return_value = FakeSession()
        self.client.login(username="checkout_user", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "2"})

        response = self.client.post(reverse("checkout_cart"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, FakeSession.url)
        order = Order.objects.get(user=self.user, event=self.event)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.tickets.count(), 0)
        self.assertEqual(order.stripe_checkout_session_id, "cs_test_checkout_direct")
        cart = Cart.objects.get(user=self.user, status=Cart.Status.ACTIVE)
        self.assertEqual(cart.items.count(), 1)

    @patch("core.views.create_checkout_session")
    def test_checkout_creates_product_order_item_without_redemption(self, mocked_create_checkout_session):
        class FakeSession:
            id = "cs_test_product_direct"
            url = "https://checkout.stripe.test/session/product"

        mocked_create_checkout_session.return_value = FakeSession()
        self.client.login(username="checkout_user", password="StrongPass123!")
        self.client.post(
            reverse("add_product_to_cart", args=[self.event.pk]),
            {"product_variant": str(self.product_variant.pk), "quantity": "2"},
        )

        response = self.client.post(reverse("checkout_cart"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, FakeSession.url)
        order = Order.objects.get(user=self.user, event=self.event)
        product_item = order.items.get(item_type=OrderItem.ItemType.PRODUCT)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(product_item.quantity, 2)
        self.assertEqual(product_item.variant_name, "M / Roja")
        self.assertFalse(ProductRedemption.objects.filter(order=order, user=self.user, event=self.event).exists())

    @patch("core.views.create_checkout_session")
    def test_checkout_creates_mixed_pending_order_without_fulfillment(self, mocked_create_checkout_session):
        class FakeSession:
            id = "cs_test_mixed_direct"
            url = "https://checkout.stripe.test/session/mixed"

        mocked_create_checkout_session.return_value = FakeSession()
        self.client.login(username="checkout_user", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "1"})
        self.client.post(
            reverse("add_product_to_cart", args=[self.event.pk]),
            {"product_variant": str(self.product_variant.pk), "quantity": "2"},
        )

        response = self.client.post(reverse("checkout_cart"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, FakeSession.url)
        order = Order.objects.get(user=self.user, event=self.event)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.items.filter(item_type=OrderItem.ItemType.TICKET).count(), 1)
        self.assertEqual(order.items.filter(item_type=OrderItem.ItemType.PRODUCT).count(), 1)
        self.assertEqual(order.tickets.count(), 0)
        self.assertFalse(ProductRedemption.objects.filter(order=order, user=self.user, event=self.event).exists())

    @patch("core.views.create_checkout_session")
    def test_checkout_reuses_existing_pending_order_instead_of_creating_duplicate(self, mocked_create_checkout_session):
        class FakeSession:
            id = "cs_test_reuse_direct"
            url = "https://checkout.stripe.test/session/reuse"

        mocked_create_checkout_session.return_value = FakeSession()
        self.client.login(username="checkout_user", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "1"})

        first_response = self.client.post(reverse("checkout_cart"))
        self.assertEqual(first_response.status_code, 302)
        first_order = Order.objects.get(user=self.user, event=self.event, status=Order.Status.PENDING)
        self.assertEqual(Order.objects.filter(user=self.user, event=self.event, status=Order.Status.PENDING).count(), 1)

        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "2"})
        second_response = self.client.post(reverse("checkout_cart"))

        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(Order.objects.filter(user=self.user, event=self.event, status=Order.Status.PENDING).count(), 1)
        first_order.refresh_from_db()
        self.assertEqual(first_order.quantity, 3)
        self.assertEqual(first_order.items.count(), 1)

    def test_order_supports_payment_metadata_fields_for_future_gateway(self):
        order = Order.objects.create(
            user=self.user,
            event=self.event,
            status=Order.Status.PENDING,
            payment_provider="stripe",
            payment_status_detail="checkout_session_created",
            stripe_checkout_session_id="cs_test_123",
            stripe_payment_intent_id="pi_test_123",
        )

        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.payment_provider, "stripe")
        self.assertEqual(order.payment_status_detail, "checkout_session_created")
        self.assertEqual(order.stripe_checkout_session_id, "cs_test_123")
        self.assertEqual(order.stripe_payment_intent_id, "pi_test_123")
        self.assertFalse(order.is_payment_final)

    @patch("core.views.create_checkout_session")
    def test_start_stripe_checkout_creates_session_and_saves_session_id(self, mocked_create_checkout_session):
        self.client.login(username="checkout_user", password="StrongPass123!")
        order = Order.objects.create(
            user=self.user,
            event=self.event,
            status=Order.Status.PENDING,
            total_usd=Decimal("20.00"),
            quantity=1,
        )
        OrderItem.objects.create(
            order=order,
            event=self.event,
            item_type=OrderItem.ItemType.TICKET,
            ticket_type=self.general_type,
            item_name=f"{self.event.title} - {self.general_type.name}",
            unit_price_usd=self.general_type.price_usd,
            quantity=1,
            total_usd=self.general_type.price_usd,
        )

        class FakeSession:
            id = "cs_test_mocked"
            url = "https://checkout.stripe.test/session/mock"

        mocked_create_checkout_session.return_value = FakeSession()

        response = self.client.post(reverse("start_stripe_checkout", args=[order.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, FakeSession.url)
        order.refresh_from_db()
        self.assertEqual(order.payment_provider, "stripe")
        self.assertEqual(order.payment_status_detail, "checkout_session_created")
        self.assertEqual(order.stripe_checkout_session_id, "cs_test_mocked")

    def test_stripe_checkout_success_page_shows_confirmed_copy_for_paid_order(self):
        order = Order.objects.create(
            user=self.user,
            event=self.event,
            status=Order.Status.PAID,
            total_usd=Decimal("20.00"),
        )
        self.client.login(username="checkout_user", password="StrongPass123!")

        response = self.client.get(reverse("stripe_checkout_success") + f"?order_id={order.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "confirmado")
        self.assertContains(response, "ya generamos tus boletas QR")
        self.assertContains(response, "Te llevaremos a Mis compras y QR")
        self.assertContains(response, 'id="success-redirect-countdown"', html=False)
        self.assertEqual(response.context["redirect_target"], reverse("my_tickets"))

    def test_stripe_checkout_success_page_shows_product_button_when_order_has_products(self):
        order = Order.objects.create(
            user=self.user,
            event=self.event,
            status=Order.Status.PAID,
            total_usd=Decimal("45.00"),
        )
        OrderItem.objects.create(
            order=order,
            event=self.event,
            item_type=OrderItem.ItemType.PRODUCT,
            product_variant=self.product_variant,
            item_name=self.product.name,
            variant_name=self.product_variant.name,
            unit_price_usd=self.product.price_usd,
            quantity=1,
            total_usd=self.product.price_usd,
        )
        self.client.login(username="checkout_user", password="StrongPass123!")

        response = self.client.get(reverse("stripe_checkout_success") + f"?order_id={order.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ver productos y codigos")
        self.assertContains(response, f"{reverse('my_tickets')}?event_id={self.event.pk}")
        self.assertEqual(
            response.context["redirect_target"],
            f"{reverse('my_tickets')}?event_id={self.event.pk}",
        )

    def test_stripe_checkout_cancel_page_shows_pending_order(self):
        order = Order.objects.create(
            user=self.user,
            event=self.event,
            status=Order.Status.PENDING,
            total_usd=Decimal("20.00"),
        )
        self.client.login(username="checkout_user", password="StrongPass123!")

        response = self.client.get(reverse("stripe_checkout_cancel") + f"?order_id={order.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pago")
        self.assertContains(response, "cancelado")
        self.assertContains(response, f"#{order.pk}")

    @patch("core.views.create_checkout_session")
    @patch("core.views.construct_webhook_event")
    def test_stripe_webhook_marks_pending_order_paid_and_fulfills_once(self, mocked_construct_webhook_event, mocked_create_checkout_session):
        class FakeSession:
            id = "cs_test_mocked"
            url = "https://checkout.stripe.test/session/webhook"

        mocked_create_checkout_session.return_value = FakeSession()
        self.client.login(username="checkout_user", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "1"})
        self.client.post(reverse("checkout_cart"))
        order = Order.objects.get(user=self.user, event=self.event, status=Order.Status.PENDING)
        mocked_construct_webhook_event.return_value = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_mocked",
                    "payment_intent": "pi_test_mocked",
                    "metadata": {"order_id": str(order.pk)},
                }
            },
        }

        response = self.client.post(
            reverse("stripe_webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig_test_mocked",
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(order.payment_status_detail, "paid")
        self.assertEqual(order.stripe_payment_intent_id, "pi_test_mocked")
        self.assertIsNotNone(order.payment_confirmed_at)
        self.assertEqual(order.tickets.count(), 1)
        self.assertEqual(
            Cart.objects.filter(user=self.user, event=self.event, status=Cart.Status.ACTIVE).count(),
            0,
        )
        self.assertEqual(
            Cart.objects.filter(user=self.user, event=self.event, status=Cart.Status.CONVERTED).count(),
            1,
        )

        second_response = self.client.post(
            reverse("stripe_webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig_test_mocked",
        )

        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(order.tickets.count(), 1)
        self.assertEqual(Ticket.objects.filter(order=order).count(), 1)

    @patch("core.views.construct_webhook_event")
    def test_stripe_webhook_ignores_non_completed_events(self, mocked_construct_webhook_event):
        mocked_construct_webhook_event.return_value = {
            "type": "payment_intent.created",
            "data": {"object": {}},
        }

        response = self.client.post(
            reverse("stripe_webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig_test_mocked",
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {"received": True, "ignored": "payment_intent.created"},
        )

    def test_checkout_rejects_ticket_when_event_becomes_inactive(self):
        self.client.login(username="checkout_user", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "2"})
        self.event.status = Event.Status.INACTIVE
        self.event.save(update_fields=["status"])

        response = self.client.post(reverse("checkout_cart"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ya no esta disponible")
        self.assertEqual(Order.objects.filter(user=self.user, event=self.event).count(), 0)

    def test_checkout_rejects_ticket_when_event_finishes_before_payment(self):
        self.client.login(username="checkout_user", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "1"})
        self.event.end_datetime = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.event.save(update_fields=["end_datetime"])

        response = self.client.post(reverse("checkout_cart"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ya finalizo")
        self.assertEqual(Order.objects.filter(user=self.user, event=self.event).count(), 0)

class AdminValidationTests(TestCase):
    def setUp(self):
        self.event = Event.objects.create(
            title="Evento Validacion",
            datetime=datetime(2026, 5, 10, 20, 0, tzinfo=timezone.utc),
            status=Event.Status.ACTIVE,
        )
        self.customer = User.objects.create_user(
            username="cliente_val", password="StrongPass123!"
        )
        self.admin = User.objects.create_user(
            username="admin_val",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        self.order = Order.objects.create(
            user=self.customer, event=self.event, status=Order.Status.PAID
        )
        self.ticket = Ticket.objects.create(
            order=self.order, event=self.event, status=Ticket.Status.UNUSED
        )
        self.ticket.token_ref = generate_ticket_token(self.ticket)
        self.ticket.save(update_fields=["token_ref"])

    def test_validate_token_requires_staff_user(self):
        user = User.objects.create_user(username="plain_user", password="StrongPass123!")
        self.client.login(username="plain_user", password="StrongPass123!")
        response = self.client.get(reverse("validate_token"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_validator_role_user_can_access_validate_view(self):
        validator = User.objects.create_user(
            username="validator_user",
            password="StrongPass123!",
        )
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        validator.groups.add(group)

        self.client.login(username="validator_user", password="StrongPass123!")
        validate_response = self.client.get(reverse("validate_token"))

        self.assertEqual(validate_response.status_code, 200)

    def test_validator_home_shows_only_profile_and_validate(self):
        validator = User.objects.create_user(
            username="validator_home_user",
            password="StrongPass123!",
        )
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        validator.groups.add(group)

        self.client.login(username="validator_home_user", password="StrongPass123!")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mi perfil")
        self.assertContains(response, "Validar boleta de rifa")
        self.assertContains(response, "Gestionar participaciones de usuarios")
        self.assertNotContains(response, "Ver obras")

    def test_validator_post_valid_token_shows_popup_correcto_and_uses_ticket(self):
        validator = User.objects.create_user(
            username="validator_post_ok",
            password="StrongPass123!",
        )
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        validator.groups.add(group)

        self.client.login(username="validator_post_ok", password="StrongPass123!")
        response = self.client.post(
            reverse("validate_token"),
            {"token": self.ticket.token_ref},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Informacion de la boleta")
        self.assertContains(response, "Aceptar")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.USED)

    def test_validator_post_invalid_token_shows_popup_fallo(self):
        validator = User.objects.create_user(
            username="validator_post_fail",
            password="StrongPass123!",
        )
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        validator.groups.add(group)

        self.client.login(username="validator_post_fail", password="StrongPass123!")
        response = self.client.post(
            reverse("validate_token"),
            {"token": "token.invalido"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Informacion de la boleta")
        self.assertContains(response, "Aceptar")

    def test_validator_reused_token_shows_ya_fue_usado(self):
        validator = User.objects.create_user(
            username="validator_reuse_msg",
            password="StrongPass123!",
        )
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        validator.groups.add(group)

        self.client.login(username="validator_reuse_msg", password="StrongPass123!")
        self.client.post(reverse("validate_token"), {"token": self.ticket.token_ref}, follow=True)
        reuse = self.client.post(
            reverse("validate_token"),
            {"token": self.ticket.token_ref},
        )

        self.assertEqual(reuse.status_code, 200)
        self.assertContains(reuse, "Informacion de la boleta")
        self.assertContains(reuse, "Aceptar")

    def test_validate_token_marks_ticket_as_used(self):
        self.client.login(username="admin_val", password="StrongPass123!")
        response = self.client.post(
            reverse("validate_token"),
            {"token": self.ticket.token_ref},
        )

        self.ticket.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.ticket.status, Ticket.Status.USED)
        self.assertContains(response, "Acceso aprobado")
        self.assertTrue(
            ValidationLog.objects.filter(
                ticket=self.ticket, outcome=ValidationLog.Outcome.ACCEPTED
            ).exists()
        )

    def test_validate_token_rejects_tampered_signature(self):
        self.client.login(username="admin_val", password="StrongPass123!")
        payload, signature = self.ticket.token_ref.split(".", 1)
        replacement = "A" if signature[0] != "A" else "B"
        tampered = f"{payload}.{replacement}{signature[1:]}"

        response = self.client.post(reverse("validate_token"), {"token": tampered})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Firma HMAC invalida")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.UNUSED)
        self.assertTrue(
            ValidationLog.objects.filter(outcome=ValidationLog.Outcome.REJECTED).exists()
        )

    def test_validate_token_rejects_reuse(self):
        self.client.login(username="admin_val", password="StrongPass123!")
        self.client.post(reverse("validate_token"), {"token": self.ticket.token_ref})
        response = self.client.post(
            reverse("validate_token"),
            {"token": self.ticket.token_ref},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Esta entrada ya fue validada antes")

    def test_validate_token_rejects_ticket_when_event_finished(self):
        self.event.end_datetime = datetime(2026, 2, 1, 20, 0, tzinfo=timezone.utc)
        self.event.save(update_fields=["end_datetime"])
        self.client.login(username="admin_val", password="StrongPass123!")

        response = self.client.post(
            reverse("validate_token"),
            {"token": self.ticket.token_ref},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "evento ya finalizo")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.UNUSED)
        self.assertTrue(
            ValidationLog.objects.filter(
                ticket=self.ticket,
                outcome=ValidationLog.Outcome.REJECTED,
                detail__icontains="evento ya finalizo",
            ).exists()
        )

    def test_validate_product_redemption_requires_permission(self):
        user = User.objects.create_user(username="plain_product_validator", password="StrongPass123!")
        self.client.login(username="plain_product_validator", password="StrongPass123!")
        response = self.client.get(reverse("validate_product_redemption"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_validator_can_deliver_product_redemption(self):
        validator = User.objects.create_user(
            username="validator_product_ok",
            password="StrongPass123!",
        )
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        validator.groups.add(group)
        product = Product.objects.create(
            event=self.event,
            name="Gorra Carnaval",
            price_usd=Decimal("12.00"),
            is_active=True,
            has_variants=False,
        )
        variant = ProductVariant.objects.create(
            product=product,
            name="Unidad",
            stock_total=20,
            is_active=True,
        )
        OrderItem.objects.create(
            order=self.order,
            event=self.event,
            item_type=OrderItem.ItemType.PRODUCT,
            product_variant=variant,
            item_name="Gorra Carnaval",
            variant_name="",
            unit_price_usd=Decimal("12.00"),
            quantity=2,
            total_usd=Decimal("24.00"),
        )
        redemption = ProductRedemption.objects.create(
            order=self.order,
            user=self.customer,
            event=self.event,
        )

        self.client.login(username="validator_product_ok", password="StrongPass123!")
        response = self.client.post(
            reverse("validate_product_redemption"),
            {"action": "deliver", "code": redemption.code},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Entrega registrada correctamente.")
        redemption.refresh_from_db()
        self.assertEqual(redemption.status, ProductRedemption.Status.DELIVERED)
        self.assertEqual(redemption.delivered_by, validator)

    def test_validator_can_lookup_product_redemption_without_prod_prefix(self):
        validator = User.objects.create_user(
            username="validator_product_short_code",
            password="StrongPass123!",
        )
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        validator.groups.add(group)
        product = Product.objects.create(
            event=self.event,
            name="Camiseta Carnaval",
            price_usd=Decimal("25.00"),
            is_active=True,
            has_variants=False,
        )
        variant = ProductVariant.objects.create(
            product=product,
            name="Unidad",
            stock_total=20,
            is_active=True,
        )
        OrderItem.objects.create(
            order=self.order,
            event=self.event,
            item_type=OrderItem.ItemType.PRODUCT,
            product_variant=variant,
            item_name="Camiseta Carnaval",
            variant_name="",
            unit_price_usd=Decimal("25.00"),
            quantity=1,
            total_usd=Decimal("25.00"),
        )
        redemption = ProductRedemption.objects.create(
            order=self.order,
            user=self.customer,
            event=self.event,
            code="PROD-2DB0E7",
        )

        self.client.login(username="validator_product_short_code", password="StrongPass123!")
        response = self.client.post(
            reverse("validate_product_redemption"),
            {"action": "lookup", "code": "2DB0E7"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Codigo valido.")
        self.assertContains(response, redemption.code)

    def test_validate_product_redemption_blocks_double_delivery(self):
        product = Product.objects.create(
            event=self.event,
            name="Gorra Carnaval",
            price_usd=Decimal("12.00"),
            is_active=True,
            has_variants=False,
        )
        variant = ProductVariant.objects.create(
            product=product,
            name="Unidad",
            stock_total=20,
            is_active=True,
        )
        OrderItem.objects.create(
            order=self.order,
            event=self.event,
            item_type=OrderItem.ItemType.PRODUCT,
            product_variant=variant,
            item_name="Gorra Carnaval",
            variant_name="",
            unit_price_usd=Decimal("12.00"),
            quantity=1,
            total_usd=Decimal("12.00"),
        )
        redemption = ProductRedemption.objects.create(
            order=self.order,
            user=self.customer,
            event=self.event,
            status=ProductRedemption.Status.DELIVERED,
            delivered_by=self.admin,
            delivered_at=datetime.now(timezone.utc),
        )

        self.client.login(username="admin_val", password="StrongPass123!")
        response = self.client.post(
            reverse("validate_product_redemption"),
            {"action": "deliver", "code": redemption.code},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No repitas la entrega")


class AdminEventCreationTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="staff_creator",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        self.customer = User.objects.create_user(
            username="customer_no_staff",
            password="StrongPass123!",
        )

    def test_create_event_requires_staff_user(self):
        self.client.login(username="customer_no_staff", password="StrongPass123!")
        response = self.client.get(reverse("create_event"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_create_event_creates_active_event_for_staff(self):
        self.client.login(username="staff_creator", password="StrongPass123!")
        response = self.client.post(
            reverse("create_event"),
            {
                "title": "Evento Nuevo Admin",
                "description": "Descripcion admin",
                "location": "Teatro Central",
                "organizer": "Productora A",
                "category": "Concierto",
                "general_price_usd": "1.00",
                "general_ticket_limit": "100",
                "vip_price_usd": "",
                "vip_ticket_limit": "",
                "age_rating": Event.AgeRating.PLUS_18,
                "datetime": "2026-08-20T19:30",
                "end_datetime": "2026-08-20T22:30",
            },
        )

        created_event = Event.objects.get(title="Evento Nuevo Admin")
        self.assertRedirects(response, reverse("event_detail", args=[created_event.pk]))
        self.assertEqual(created_event.status, Event.Status.ACTIVE)
        self.assertEqual(created_event.created_by, self.staff)
        general_type = EventTicketType.objects.get(event=created_event, code=EventTicketType.Code.GENERAL)
        self.assertEqual(general_type.price_usd, Decimal("1.00"))
        self.assertEqual(general_type.stock_total, 100)
        self.assertFalse(
            EventTicketType.objects.filter(
                event=created_event,
                code=EventTicketType.Code.VIP,
                is_active=True,
            ).exists()
        )

    def test_create_event_can_define_vip_ticket_type(self):
        self.client.login(username="staff_creator", password="StrongPass123!")
        response = self.client.post(
            reverse("create_event"),
            {
                "title": "Evento VIP Admin",
                "description": "Descripcion VIP",
                "location": "Salon Norte",
                "general_price_usd": "2.00",
                "general_ticket_limit": "80",
                "vip_price_usd": "5.00",
                "vip_ticket_limit": "20",
                "age_rating": Event.AgeRating.ALL,
                "datetime": "2026-09-20T19:30",
                "end_datetime": "2026-09-20T23:00",
            },
        )

        created_event = Event.objects.get(title="Evento VIP Admin")
        self.assertRedirects(response, reverse("event_detail", args=[created_event.pk]))
        general_type = EventTicketType.objects.get(event=created_event, code=EventTicketType.Code.GENERAL)
        vip_type = EventTicketType.objects.get(event=created_event, code=EventTicketType.Code.VIP)
        self.assertEqual(general_type.price_usd, Decimal("2.00"))
        self.assertEqual(general_type.stock_total, 80)
        self.assertEqual(vip_type.price_usd, Decimal("5.00"))
        self.assertEqual(vip_type.stock_total, 20)
        self.assertTrue(vip_type.is_active)

    def test_create_event_can_create_products_and_variants(self):
        self.client.login(username="staff_creator", password="StrongPass123!")
        products_payload = json.dumps(
            [
                {
                    "name": "Gorra Carnaval",
                    "description": "Edicion oficial",
                    "price_usd": "12.00",
                    "is_active": True,
                    "has_variants": False,
                    "simple_stock": "40",
                    "variants": [],
                },
                {
                    "name": "Camiseta Carnaval",
                    "description": "Coleccion 2026",
                    "price_usd": "25.00",
                    "is_active": True,
                    "has_variants": True,
                    "simple_stock": "",
                    "variants": [
                        {"name": "S / Roja", "stock_total": "8", "is_active": True},
                        {"name": "M / Roja", "stock_total": "12", "is_active": True},
                    ],
                },
            ]
        )
        response = self.client.post(
            reverse("create_event"),
            {
                "title": "Evento con productos",
                "description": "Descripcion",
                "location": "Malecon",
                "general_price_usd": "2.00",
                "general_ticket_limit": "80",
                "vip_price_usd": "",
                "vip_ticket_limit": "",
                "products_enabled": "on",
                "products_payload": products_payload,
                "age_rating": Event.AgeRating.ALL,
                "datetime": "2026-10-01T19:00",
                "end_datetime": "2026-10-01T23:00",
            },
        )

        created_event = Event.objects.get(title="Evento con productos")
        self.assertRedirects(response, reverse("event_detail", args=[created_event.pk]))
        self.assertEqual(created_event.products.count(), 2)
        cap = Product.objects.get(event=created_event, name="Gorra Carnaval")
        shirt = Product.objects.get(event=created_event, name="Camiseta Carnaval")
        self.assertFalse(cap.has_variants)
        self.assertEqual(cap.variants.get(name="Unidad").stock_total, 40)
        self.assertTrue(shirt.has_variants)
        self.assertEqual(shirt.variants.count(), 2)

    def test_create_event_can_upload_product_image(self):
        self.client.login(username="staff_creator", password="StrongPass123!")
        image_input_key = "product_image_new_1"
        products_payload = json.dumps(
            [
                {
                    "name": "Gorra Carnaval",
                    "description": "Edicion oficial",
                    "price_usd": "12.00",
                    "is_active": True,
                    "has_variants": False,
                    "image_input_key": image_input_key,
                    "image_url": "",
                    "simple_stock": "40",
                    "variants": [],
                }
            ]
        )
        response = self.client.post(
            reverse("create_event"),
            {
                "title": "Evento con imagen de producto",
                "description": "Descripcion",
                "location": "Malecon",
                "general_price_usd": "2.00",
                "general_ticket_limit": "80",
                "vip_price_usd": "",
                "vip_ticket_limit": "",
                "products_enabled": "on",
                "products_payload": products_payload,
                "age_rating": Event.AgeRating.ALL,
                "datetime": "2026-10-01T19:00",
                "end_datetime": "2026-10-01T23:00",
                image_input_key: SimpleUploadedFile(
                    "gorra.jpg",
                    b"fake-image-content",
                    content_type="image/jpeg",
                ),
            },
        )

        created_event = Event.objects.get(title="Evento con imagen de producto")
        self.assertRedirects(response, reverse("event_detail", args=[created_event.pk]))
        product = Product.objects.get(event=created_event, name="Gorra Carnaval")
        self.assertTrue(product.image.name.endswith("gorra.jpg"))

    def test_create_event_rejects_duplicate_variant_names_per_product(self):
        self.client.login(username="staff_creator", password="StrongPass123!")
        products_payload = json.dumps(
            [
                {
                    "name": "Camiseta Carnaval",
                    "description": "Coleccion 2026",
                    "price_usd": "25.00",
                    "is_active": True,
                    "has_variants": True,
                    "simple_stock": "",
                    "variants": [
                        {"name": "M / Roja", "stock_total": "8", "is_active": True},
                        {"name": "m / roja", "stock_total": "12", "is_active": True},
                    ],
                }
            ]
        )

        response = self.client.post(
            reverse("create_event"),
            {
                "title": "Evento con variantes repetidas",
                "description": "Descripcion",
                "location": "Malecon",
                "general_price_usd": "2.00",
                "general_ticket_limit": "80",
                "vip_price_usd": "",
                "vip_ticket_limit": "",
                "products_enabled": "on",
                "products_payload": products_payload,
                "age_rating": Event.AgeRating.ALL,
                "datetime": "2026-10-01T19:00",
                "end_datetime": "2026-10-01T23:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.filter(title="Evento con variantes repetidas").exists())
        self.assertFormError(
            response.context["form"],
            "products_payload",
            'El producto "Camiseta Carnaval" no puede tener variantes repetidas: "m / roja".',
        )

    def test_create_active_event_notifies_normal_users(self):
        subscriber = User.objects.create_user(
            username="notify_new_work_user",
            password="StrongPass123!",
        )
        self.client.login(username="staff_creator", password="StrongPass123!")
        response = self.client.post(
            reverse("create_event"),
            {
                "title": "Obra para notificar",
                "description": "Descripcion",
                "location": "Galeria",
                "general_price_usd": "1.00",
                "general_ticket_limit": "100",
                "age_rating": Event.AgeRating.ALL,
                "datetime": "2026-10-20T19:30",
                "end_datetime": "2026-10-20T22:30",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Notification.objects.filter(
                user=subscriber,
                title="Nuevo evento disponible",
            ).exists()
        )


class AdminEventManagementTests(TestCase):
    def setUp(self):
        self.event = Event.objects.create(
            title="Evento Gestion",
            datetime=datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc),
            end_datetime=datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc),
            status=Event.Status.ACTIVE,
        )
        self.staff = User.objects.create_user(
            username="manage_staff",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        self.customer = User.objects.create_user(
            username="manage_customer",
            password="StrongPass123!",
        )

    def test_staff_event_detail_shows_edit_and_delete_not_buy(self):
        self.client.login(username="manage_staff", password="StrongPass123!")
        response = self.client.get(reverse("event_detail", args=[self.event.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Editar evento")
        self.assertContains(response, "Eliminar evento")
        self.assertContains(response, reverse("update_event", args=[self.event.pk]))
        self.assertContains(response, reverse("delete_event", args=[self.event.pk]))
        self.assertNotContains(response, "Guardar cambios")
        self.assertNotContains(response, "Comprar")
        self.assertNotContains(response, "Iniciar sesi")
        self.assertNotContains(response, "Registrarse")

    def test_staff_can_delete_event_and_related_records(self):
        self.client.login(username="manage_staff", password="StrongPass123!")
        ticket_type = EventTicketType.objects.create(
            event=self.event,
            code=EventTicketType.Code.GENERAL,
            name="General",
            price_usd=Decimal("2.00"),
            stock_total=50,
            is_active=True,
            display_order=1,
            number_prefix="G",
        )
        order = Order.objects.create(
            user=self.customer,
            event=self.event,
            ticket_type=ticket_type,
            status=Order.Status.PAID,
            unit_price_usd=Decimal("2.00"),
            quantity=1,
            total_usd=Decimal("2.00"),
        )
        ticket = Ticket.objects.create(
            order=order,
            event=self.event,
            ticket_type=ticket_type,
            status=Ticket.Status.UNUSED,
            raffle_number=1,
            token_ref="delete.event.ticket",
        )
        ValidationLog.objects.create(
            ticket=ticket,
            admin=self.staff,
            outcome=ValidationLog.Outcome.ACCEPTED,
            detail="Validacion inicial",
        )
        product = Product.objects.create(
            event=self.event,
            name="Camiseta",
            description="Edicion especial",
            price_usd=Decimal("20.00"),
            is_active=True,
            has_variants=True,
        )
        variant = ProductVariant.objects.create(
            product=product,
            name="M / Roja",
            stock_total=10,
            is_active=True,
        )
        OrderItem.objects.create(
            order=order,
            event=self.event,
            item_type=OrderItem.ItemType.PRODUCT,
            product_variant=variant,
            item_name="Camiseta",
            variant_name="M / Roja",
            unit_price_usd=Decimal("20.00"),
            quantity=1,
            total_usd=Decimal("20.00"),
        )
        ProductRedemption.objects.create(
            order=order,
            user=self.customer,
            event=self.event,
        )
        EventImage.objects.create(
            event=self.event,
            image=SimpleUploadedFile(
                "delete-event.jpg",
                b"fake-image-content",
                content_type="image/jpeg",
            ),
        )

        response = self.client.post(reverse("delete_event", args=[self.event.pk]), follow=True)

        self.assertRedirects(response, reverse("event_list"))
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())
        self.assertFalse(EventTicketType.objects.filter(pk=ticket_type.pk).exists())
        self.assertFalse(Ticket.objects.filter(pk=ticket.pk).exists())
        self.assertFalse(ValidationLog.objects.filter(ticket=ticket).exists())
        self.assertFalse(Product.objects.filter(pk=product.pk).exists())
        self.assertFalse(ProductVariant.objects.filter(pk=variant.pk).exists())
        self.assertFalse(Order.objects.filter(pk=order.pk).exists())
        self.assertFalse(ProductRedemption.objects.filter(order=order).exists())
        self.assertFalse(EventImage.objects.filter(event=self.event).exists())

    def test_non_staff_cannot_delete_event(self):
        self.client.login(username="manage_customer", password="StrongPass123!")

        response = self.client.post(reverse("delete_event", args=[self.event.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_staff_can_create_moment_block_with_image_and_video(self):
        self.client.login(username="manage_staff", password="StrongPass123!")

        response = self.client.post(
            reverse("manage_moments"),
            {
                "title": "Recuerdos del carnaval",
                "description": "Bloque visual de prueba",
                "focal_point_x_new_1": "72",
                "focal_point_y_new_1": "28",
                "media_files": [
                    SimpleUploadedFile(
                        "momento.jpg",
                        b"fake-image-content",
                        content_type="image/jpeg",
                    ),
                    SimpleUploadedFile(
                        "momento.mp4",
                        b"fake-video-content",
                        content_type="video/mp4",
                    ),
                ],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(MomentBlock.objects.filter(title="Recuerdos del carnaval").exists())
        block = MomentBlock.objects.get(title="Recuerdos del carnaval")
        self.assertEqual(block.media_items.count(), 2)
        image_media = block.media_items.get(media_type=MomentMedia.MediaType.IMAGE)
        self.assertEqual(image_media.focal_point_x, 72.0)
        self.assertEqual(image_media.focal_point_y, 28.0)
        self.assertTrue(block.media_items.filter(media_type=MomentMedia.MediaType.VIDEO).exists())

    def test_manage_moments_rejects_more_than_six_media_files(self):
        self.client.login(username="manage_staff", password="StrongPass123!")

        media_files = [
            SimpleUploadedFile(
                f"momento-{index}.jpg",
                b"fake-image-content",
                content_type="image/jpeg",
            )
            for index in range(1, 8)
        ]

        response = self.client.post(
            reverse("manage_moments"),
            {
                "title": "Bloque excedido",
                "description": "Demasiados archivos",
                "media_files": media_files,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solo puedes cargar hasta 6 archivos por bloque")
        self.assertFalse(MomentBlock.objects.filter(title="Bloque excedido").exists())

    def test_staff_can_update_moment_block_and_delete_selected_media(self):
        self.client.login(username="manage_staff", password="StrongPass123!")
        block = MomentBlock.objects.create(
            title="Bloque original",
            description="Descripcion original",
            is_active=True,
            display_order=1,
        )
        existing_media = MomentMedia.objects.create(
            block=block,
            media_type=MomentMedia.MediaType.IMAGE,
            file=SimpleUploadedFile(
                "original.jpg",
                b"fake-image-content",
                content_type="image/jpeg",
            ),
            display_order=1,
        )
        second_media = MomentMedia.objects.create(
            block=block,
            media_type=MomentMedia.MediaType.IMAGE,
            file=SimpleUploadedFile(
                "second.jpg",
                b"fake-image-content-2",
                content_type="image/jpeg",
            ),
            display_order=2,
        )

        response = self.client.post(
            reverse("update_moment_block", args=[block.pk]),
            {
                "title": "Bloque editado",
                "description": "Descripcion nueva",
                "delete_media_ids": [str(existing_media.pk)],
                f"media_order_{second_media.pk}": "1",
                f"focal_point_x_{second_media.pk}": "18",
                f"focal_point_y_{second_media.pk}": "82",
                "focal_point_x_new_1": "61",
                "focal_point_y_new_1": "39",
                "media_files": [
                    SimpleUploadedFile(
                        "nuevo.mp4",
                        b"fake-video-content",
                        content_type="video/mp4",
                    )
                ],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        block.refresh_from_db()
        self.assertEqual(block.title, "Bloque editado")
        self.assertEqual(block.description, "Descripcion nueva")
        self.assertTrue(block.is_active)
        self.assertFalse(MomentMedia.objects.filter(pk=existing_media.pk).exists())
        self.assertEqual(block.media_items.count(), 2)
        second_media.refresh_from_db()
        self.assertEqual(second_media.display_order, 1)
        self.assertEqual(second_media.focal_point_x, 18.0)
        self.assertEqual(second_media.focal_point_y, 82.0)
        self.assertTrue(block.media_items.filter(media_type=MomentMedia.MediaType.VIDEO).exists())

    def test_update_moment_block_rejects_more_than_six_total_media_files(self):
        self.client.login(username="manage_staff", password="StrongPass123!")
        block = MomentBlock.objects.create(
            title="Bloque con limite",
            description="Descripcion original",
            is_active=True,
            display_order=1,
        )
        for index in range(1, 6):
            MomentMedia.objects.create(
                block=block,
                media_type=MomentMedia.MediaType.IMAGE,
                file=SimpleUploadedFile(
                    f"existing-{index}.jpg",
                    b"fake-image-content",
                    content_type="image/jpeg",
                ),
                display_order=index,
            )

        response = self.client.post(
            reverse("update_moment_block", args=[block.pk]),
            {
                "title": "Bloque con limite",
                "description": "Descripcion original",
                "media_files": [
                    SimpleUploadedFile("nuevo-1.jpg", b"fake-image-content", content_type="image/jpeg"),
                    SimpleUploadedFile("nuevo-2.mp4", b"fake-video-content", content_type="video/mp4"),
                ],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solo puedes guardar hasta 6 archivos por bloque")
        block.refresh_from_db()
        self.assertEqual(block.media_items.count(), 5)

    def test_manage_moments_shows_newest_block_first(self):
        self.client.login(username="manage_staff", password="StrongPass123!")
        older = MomentBlock.objects.create(title="Bloque viejo", description="A", is_active=True)
        newer = MomentBlock.objects.create(title="Bloque nuevo", description="B", is_active=True)

        response = self.client.get(reverse("manage_moments"))

        self.assertEqual(response.status_code, 200)
        blocks = list(response.context["moment_blocks"])
        self.assertEqual(blocks[0].pk, newer.pk)
        self.assertEqual(blocks[1].pk, older.pk)

    def test_staff_can_delete_moment_block(self):
        self.client.login(username="manage_staff", password="StrongPass123!")
        block = MomentBlock.objects.create(
            title="Bloque para eliminar",
            description="Descripcion",
            is_active=True,
            display_order=1,
        )
        MomentMedia.objects.create(
            block=block,
            media_type=MomentMedia.MediaType.IMAGE,
            file=SimpleUploadedFile(
                "eliminar.jpg",
                b"fake-image-content",
                content_type="image/jpeg",
            ),
            display_order=1,
        )

        response = self.client.post(reverse("delete_moment_block", args=[block.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MomentBlock.objects.filter(pk=block.pk).exists())
        self.assertFalse(MomentMedia.objects.filter(block=block).exists())

    def test_staff_can_update_site_social_links(self):
        self.client.login(username="manage_staff", password="StrongPass123!")

        response = self.client.post(
            reverse("manage_site_settings"),
            {
                "whatsapp_url": "https://wa.me/456",
                "instagram_url": "https://instagram.com/carnaval",
                "facebook_url": "",
                "tiktok_url": "https://www.tiktok.com/@carnaval",
                "x_url": "https://x.com/carnaval",
                "telegram_url": "",
                "home_video_url": "https://youtu.be/JrozMeSCjcc",
                "footer_primary_text": "Nuevo texto principal",
                "footer_tagline": "Nueva frase",
                "footer_copyright_text": "2026 Demo. Derechos reservados.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        settings_obj = SiteSettings.objects.get(pk=1)
        self.assertEqual(settings_obj.whatsapp_url, "https://wa.me/456")
        self.assertEqual(settings_obj.instagram_url, "https://instagram.com/carnaval")
        self.assertEqual(settings_obj.tiktok_url, "https://www.tiktok.com/@carnaval")
        self.assertEqual(settings_obj.x_url, "https://x.com/carnaval")
        self.assertEqual(settings_obj.home_video_url, "https://youtu.be/JrozMeSCjcc")
        self.assertEqual(settings_obj.footer_primary_text, "Nuevo texto principal")
        self.assertEqual(settings_obj.footer_tagline, "Nueva frase")
        self.assertEqual(settings_obj.footer_copyright_text, "2026 Demo. Derechos reservados.")

    def test_staff_navigation_control_shows_link_to_site_settings(self):
        self.client.login(username="manage_staff", password="StrongPass123!")

        response = self.client.get(reverse("event_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("manage_site_settings"))
        self.assertContains(response, "Configurar sitio y redes")

    def test_manage_site_settings_requires_staff_user(self):
        self.client.login(username="manage_customer", password="StrongPass123!")

        response = self.client.get(reverse("manage_site_settings"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_event_detail_disables_html_cache(self):
        response = self.client.get(reverse("event_detail", args=[self.event.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-cache", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("Cookie", response["Vary"])

    def test_staff_can_update_event(self):
        self.client.login(username="manage_staff", password="StrongPass123!")
        response = self.client.post(
            reverse("update_event", args=[self.event.pk]),
            {
                "title": "Evento Gestion Editado",
                "description": "Descripcion editada",
                "location": "Arena Sur",
                "organizer": "Productora C",
                "category": "Festival",
                "general_price_usd": "3.50",
                "general_ticket_limit": "100",
                "vip_price_usd": "8.50",
                "vip_ticket_limit": "25",
                "age_rating": Event.AgeRating.PLUS_18,
                "datetime": "2026-11-05T21:15",
                "end_datetime": "2026-11-05T23:45",
            },
        )

        self.assertRedirects(response, reverse("event_detail", args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Evento Gestion Editado")
        self.assertEqual(self.event.unit_price_usd, Decimal("3.50"))
        self.assertEqual(self.event.status, Event.Status.ACTIVE)
        self.assertEqual(self.event.end_datetime, datetime(2026, 11, 6, 4, 45, tzinfo=timezone.utc))
        general_type = EventTicketType.objects.get(event=self.event, code=EventTicketType.Code.GENERAL)
        vip_type = EventTicketType.objects.get(event=self.event, code=EventTicketType.Code.VIP)
        self.assertEqual(general_type.price_usd, Decimal("3.50"))
        self.assertEqual(general_type.stock_total, 100)
        self.assertEqual(vip_type.price_usd, Decimal("8.50"))
        self.assertEqual(vip_type.stock_total, 25)
        self.assertTrue(vip_type.is_active)

    def test_staff_can_update_event_products(self):
        existing_product = Product.objects.create(
            event=self.event,
            name="Producto anterior",
            description="Viejo",
            price_usd=Decimal("10.00"),
            is_active=True,
            has_variants=False,
        )
        existing_variant = ProductVariant.objects.create(
            product=existing_product,
            name="Unidad",
            stock_total=6,
            is_active=True,
        )
        self.client.login(username="manage_staff", password="StrongPass123!")
        products_payload = json.dumps(
            [
                {
                    "id": existing_product.pk,
                    "name": "Producto anterior editado",
                    "description": "Nuevo copy",
                    "price_usd": "15.00",
                    "is_active": False,
                    "has_variants": True,
                    "simple_stock": "",
                    "variants": [
                        {
                            "id": existing_variant.pk,
                            "name": "L / Negra",
                            "stock_total": "4",
                            "is_active": True,
                        },
                        {
                            "name": "M / Blanca",
                            "stock_total": "9",
                            "is_active": True,
                        },
                    ],
                }
            ]
        )
        response = self.client.post(
            reverse("update_event", args=[self.event.pk]),
            {
                "title": "Evento Gestion",
                "description": "",
                "location": "",
                "organizer": "",
                "category": "",
                "general_price_usd": "1.00",
                "general_ticket_limit": "100",
                "vip_price_usd": "",
                "vip_ticket_limit": "",
                "products_enabled": "on",
                "products_payload": products_payload,
                "age_rating": Event.AgeRating.ALL,
                "datetime": "2026-11-01T20:00",
                "end_datetime": "2026-11-01T23:00",
            },
        )

        self.assertRedirects(response, reverse("event_detail", args=[self.event.pk]))
        existing_product.refresh_from_db()
        self.assertEqual(existing_product.name, "Producto anterior editado")
        self.assertEqual(existing_product.price_usd, Decimal("15.00"))
        self.assertTrue(existing_product.has_variants)
        self.assertEqual(existing_product.variants.count(), 2)
        self.assertTrue(existing_product.variants.filter(name="L / Negra").exists())
        self.assertTrue(existing_product.variants.filter(name="M / Blanca").exists())

    def test_staff_can_update_event_with_existing_simple_product_without_duplicate_unidad_variant(self):
        existing_product = Product.objects.create(
            event=self.event,
            name="Camisa",
            description="Base",
            price_usd=Decimal("12.00"),
            is_active=True,
            has_variants=False,
        )
        existing_variant = ProductVariant.objects.create(
            product=existing_product,
            name="Unidad",
            stock_total=6,
            is_active=True,
        )
        self.client.login(username="manage_staff", password="StrongPass123!")
        products_payload = json.dumps(
            [
                {
                    "id": existing_product.pk,
                    "name": "Camisa editada",
                    "description": "Nueva descripcion",
                    "price_usd": "14.00",
                    "is_active": True,
                    "has_variants": False,
                    "simple_stock": "9",
                    "simple_variant_id": existing_variant.pk,
                    "variants": [],
                }
            ]
        )

        response = self.client.post(
            reverse("update_event", args=[self.event.pk]),
            {
                "title": "Evento Gestion",
                "description": "",
                "location": "",
                "organizer": "",
                "category": "",
                "general_price_usd": "1.00",
                "general_ticket_limit": "100",
                "vip_price_usd": "",
                "vip_ticket_limit": "",
                "products_enabled": "on",
                "products_payload": products_payload,
                "age_rating": Event.AgeRating.ALL,
                "datetime": "2026-11-01T20:00",
                "end_datetime": "2026-11-01T23:00",
            },
        )

        self.assertRedirects(response, reverse("event_detail", args=[self.event.pk]))
        existing_product.refresh_from_db()
        self.assertEqual(existing_product.name, "Camisa editada")
        self.assertFalse(existing_product.has_variants)
        self.assertEqual(existing_product.variants.count(), 1)
        unidad = existing_product.variants.get()
        self.assertEqual(unidad.pk, existing_variant.pk)
        self.assertEqual(unidad.name, "Unidad")
        self.assertEqual(unidad.stock_total, 9)

    def test_staff_event_detail_shows_summary_by_ticket_type(self):
        vip_type = EventTicketType.objects.create(
            event=self.event,
            code=EventTicketType.Code.VIP,
            name="VIP",
            price_usd=Decimal("7.00"),
            stock_total=30,
            is_active=True,
            display_order=2,
            number_prefix="VIP",
        )
        buyer = User.objects.create_user(username="event_summary_buyer", password="StrongPass123!")
        general_order = Order.objects.create(
            user=buyer,
            event=self.event,
            status=Order.Status.PAID,
            unit_price_usd=Decimal("1.00"),
            quantity=1,
            total_usd=Decimal("1.00"),
        )
        Ticket.objects.create(
            order=general_order,
            event=self.event,
            status=Ticket.Status.UNUSED,
            raffle_number=1,
            token_ref="summary.general",
        )
        vip_order = Order.objects.create(
            user=buyer,
            event=self.event,
            ticket_type=vip_type,
            status=Order.Status.PAID,
            unit_price_usd=Decimal("7.00"),
            quantity=1,
            total_usd=Decimal("7.00"),
        )
        Ticket.objects.create(
            order=vip_order,
            event=self.event,
            ticket_type=vip_type,
            status=Ticket.Status.UNUSED,
            raffle_number=1,
            token_ref="summary.vip",
        )

        self.client.login(username="manage_staff", password="StrongPass123!")
        response = self.client.get(reverse("event_detail", args=[self.event.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Total de entradas QR para la venta:")
        self.assertContains(response, "Boletas emitidas:")
        self.assertContains(response, "Boletas disponibles:")
        self.assertContains(response, "Boleta General")
        self.assertContains(response, "Boleta VIP")
        self.assertContains(response, "USD 7,00")

    def test_staff_cannot_reduce_ticket_limit_below_emitted_tickets(self):
        buyer = User.objects.create_user(username="event_limit_buyer", password="StrongPass123!")
        order = Order.objects.create(user=buyer, event=self.event, status=Order.Status.PAID)
        Ticket.objects.create(
            order=order,
            event=self.event,
            status=Ticket.Status.UNUSED,
            raffle_number=1,
            token_ref="limit.ticket.1",
        )
        Ticket.objects.create(
            order=order,
            event=self.event,
            status=Ticket.Status.UNUSED,
            raffle_number=2,
            token_ref="limit.ticket.2",
        )

        self.client.login(username="manage_staff", password="StrongPass123!")
        response = self.client.post(
            reverse("update_event", args=[self.event.pk]),
            {
                "title": "Evento Gestion",
                "description": "",
                "location": "",
                "organizer": "",
                "category": "",
                "general_price_usd": "1.00",
                "general_ticket_limit": "1",
                "vip_price_usd": "",
                "vip_ticket_limit": "",
                "age_rating": Event.AgeRating.ALL,
                "datetime": "2026-11-01T20:00",
                "end_datetime": "2026-11-01T23:00",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No puedes reducir el limite por debajo de las entradas ya emitidas.")
        self.event.refresh_from_db()
        self.assertEqual(self.event.ticket_limit, 100)

    def test_staff_can_upload_up_to_max_event_images(self):
        self.client.login(username="manage_staff", password="StrongPass123!")
        files = [
            SimpleUploadedFile(f"img{i}.jpg", b"fake-image-content", content_type="image/jpeg")
            for i in range(1, 7)
        ]

        response = self.client.post(
            reverse("add_event_images", args=[self.event.pk]),
            {"images": files},
        )

        self.assertRedirects(response, reverse("event_detail", args=[self.event.pk]))
        self.assertEqual(EventImage.objects.filter(event=self.event).count(), 3)

    def test_staff_can_delete_event_image(self):
        self.client.login(username="manage_staff", password="StrongPass123!")
        image = EventImage.objects.create(
            event=self.event,
            image=SimpleUploadedFile(
                "to-delete.jpg",
                b"fake-image-content",
                content_type="image/jpeg",
            ),
        )

        response = self.client.post(
            reverse("delete_event_image", args=[self.event.pk, image.pk]),
        )

        self.assertRedirects(response, reverse("event_detail", args=[self.event.pk]))
        self.assertFalse(EventImage.objects.filter(pk=image.pk).exists())

    def test_creator_non_staff_can_manage_own_event_images(self):
        creator = User.objects.create_user(
            username="event_creator_owner",
            password="StrongPass123!",
        )
        creator_event = Event.objects.create(
            title="Evento del Creador",
            datetime=datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc),
            status=Event.Status.ACTIVE,
            created_by=creator,
        )
        self.client.login(username="event_creator_owner", password="StrongPass123!")
        upload = self.client.post(
            reverse("add_event_images", args=[creator_event.pk]),
            {
                "images": [
                    SimpleUploadedFile(
                        "creator.jpg",
                        b"fake-image-content",
                        content_type="image/jpeg",
                    )
                ]
            },
        )
        self.assertRedirects(upload, reverse("event_detail", args=[creator_event.pk]))
        image = EventImage.objects.get(event=creator_event)

        delete = self.client.post(
            reverse("delete_event_image", args=[creator_event.pk, image.pk]),
        )
        self.assertRedirects(delete, reverse("event_detail", args=[creator_event.pk]))
        self.assertFalse(EventImage.objects.filter(pk=image.pk).exists())

    def test_non_creator_non_admin_cannot_manage_event_images(self):
        creator = User.objects.create_user(
            username="event_creator_blocked",
            password="StrongPass123!",
        )
        outsider = User.objects.create_user(
            username="event_outsider",
            password="StrongPass123!",
        )
        creator_event = Event.objects.create(
            title="Evento Privado de Imagenes",
            datetime=datetime(2026, 11, 6, 20, 0, tzinfo=timezone.utc),
            status=Event.Status.ACTIVE,
            created_by=creator,
        )
        existing = EventImage.objects.create(
            event=creator_event,
            image=SimpleUploadedFile(
                "existing.jpg",
                b"fake-image-content",
                content_type="image/jpeg",
            ),
        )
        self.client.login(username="event_outsider", password="StrongPass123!")
        upload = self.client.post(
            reverse("add_event_images", args=[creator_event.pk]),
            {
                "images": [
                    SimpleUploadedFile(
                        "blocked.jpg",
                        b"fake-image-content",
                        content_type="image/jpeg",
                    )
                ]
            },
        )
        delete = self.client.post(
            reverse("delete_event_image", args=[creator_event.pk, existing.pk]),
        )

        self.assertEqual(upload.status_code, 403)
        self.assertEqual(delete.status_code, 403)

    def test_non_staff_cannot_update_event(self):
        self.client.login(username="manage_customer", password="StrongPass123!")
        update_response = self.client.post(
            reverse("update_event", args=[self.event.pk]),
            {
                "title": "No permitido",
                "description": "No permitido",
                "location": "No permitido",
                "organizer": "No permitido",
                "category": "No permitido",
                "general_price_usd": "1.00",
                "general_ticket_limit": "100",
                "vip_price_usd": "",
                "vip_ticket_limit": "",
                "age_rating": Event.AgeRating.ALL,
                "datetime": "2026-11-05T21:15",
                "end_datetime": "2026-11-05T23:00",
            },
        )

        self.assertEqual(update_response.status_code, 302)
        self.assertIn(reverse("login"), update_response.url)


class AdminUserManagementTests(TestCase):
    def setUp(self):
        self.root_admin = User.objects.create_user(
            username="admin",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        self.admin = User.objects.create_user(
            username="users_admin",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        self.customer = User.objects.create_user(
            username="users_customer",
            password="StrongPass123!",
            email="customer@example.com",
        )
        self.other_staff = User.objects.create_user(
            username="users_other_staff",
            password="StrongPass123!",
            is_staff=True,
        )

    def test_user_list_requires_staff_user(self):
        plain = User.objects.create_user(username="plain_user_list", password="StrongPass123!")
        self.client.login(username="plain_user_list", password="StrongPass123!")
        response = self.client.get(reverse("user_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_create_user_requires_staff_user(self):
        plain = User.objects.create_user(username="plain_create_user", password="StrongPass123!")
        self.client.login(username="plain_create_user", password="StrongPass123!")
        response = self.client.get(reverse("create_user"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_staff_can_view_registered_users(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Usuarios")
        self.assertContains(response, "registrados")
        self.assertContains(response, "users_admin")
        self.assertContains(response, "users_customer")
        self.assertContains(response, "users_other_staff")

    def test_staff_home_shows_link_to_user_management(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("user_list"))
        self.assertContains(response, "Gestionar usuarios")

    def test_staff_navigation_control_shows_link_to_user_management(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.get(reverse("event_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CONTROL")
        self.assertContains(response, reverse("user_list"))
        self.assertContains(response, "Gestionar usuarios")

    def test_root_admin_sees_permission_management_actions(self):
        self.client.login(username="admin", password="StrongPass123!")
        response = self.client.get(reverse("user_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hacer admin")
        self.assertContains(response, "Dar rol validador")
        self.assertContains(response, "Bloquear")

    def test_non_root_admin_does_not_see_permission_management_actions(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Hacer admin")
        self.assertNotContains(response, "Dar rol validador")
        self.assertNotContains(response, "Bloquear")

    def test_staff_can_create_user_from_user_management(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.post(
            reverse("create_user"),
            {
                "username": "created_from_admin",
                "email": "created_from_admin@example.com",
                "display_name": "Creado desde admin",
                "contact_number": "3001234569",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Usuario creado correctamente")
        self.assertTrue(
            User.objects.filter(
                username="created_from_admin",
                email="created_from_admin@example.com",
            ).exists()
        )

    def test_staff_cannot_create_user_without_email(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.post(
            reverse("create_user"),
            {
                "username": "created_without_email",
                "document_number": "100000102",
                "contact_number": "3001234570",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Este campo es obligatorio")
        self.assertFalse(User.objects.filter(username="created_without_email").exists())

    def test_staff_can_delete_another_user(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.post(reverse("delete_user", args=[self.customer.pk]))

        self.assertRedirects(response, reverse("user_list"))
        self.assertFalse(User.objects.filter(pk=self.customer.pk).exists())

    def test_staff_cannot_delete_own_account(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.post(reverse("delete_user", args=[self.admin.pk]))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_non_root_admin_cannot_delete_other_admin(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.post(reverse("delete_user", args=[self.other_staff.pk]))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=self.other_staff.pk).exists())

    def test_root_admin_can_delete_other_admin(self):
        self.client.login(username="admin", password="StrongPass123!")
        response = self.client.post(reverse("delete_user", args=[self.other_staff.pk]))

        self.assertRedirects(response, reverse("user_list"))
        self.assertFalse(User.objects.filter(pk=self.other_staff.pk).exists())

    def test_root_admin_can_promote_non_admin_user(self):
        self.client.login(username="admin", password="StrongPass123!")
        response = self.client.post(reverse("promote_user_to_admin", args=[self.customer.pk]))

        self.assertRedirects(response, reverse("user_list"))
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_staff)
        self.assertTrue(self.customer.is_superuser)

    def test_non_root_admin_cannot_promote_non_admin_user(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.post(reverse("promote_user_to_admin", args=[self.customer.pk]))

        self.assertEqual(response.status_code, 400)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_staff)
        self.assertFalse(self.customer.is_superuser)

    def test_non_staff_cannot_promote_user(self):
        plain = User.objects.create_user(username="plain_promoter", password="StrongPass123!")
        self.client.login(username="plain_promoter", password="StrongPass123!")
        response = self.client.post(reverse("promote_user_to_admin", args=[self.customer.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_staff)
        self.assertFalse(self.customer.is_superuser)

    def test_non_root_admin_cannot_demote_admin_user(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.post(reverse("demote_admin_user", args=[self.other_staff.pk]))

        self.assertEqual(response.status_code, 400)
        self.other_staff.refresh_from_db()
        self.assertTrue(self.other_staff.is_staff)

    def test_root_admin_can_demote_admin_user(self):
        self.client.login(username="admin", password="StrongPass123!")
        response = self.client.post(reverse("demote_admin_user", args=[self.other_staff.pk]))

        self.assertRedirects(response, reverse("user_list"))
        self.other_staff.refresh_from_db()
        self.assertFalse(self.other_staff.is_staff)
        self.assertFalse(self.other_staff.is_superuser)

    def test_root_admin_can_grant_validator_role(self):
        self.client.login(username="admin", password="StrongPass123!")
        response = self.client.post(reverse("grant_validator_role", args=[self.customer.pk]))

        self.assertRedirects(response, reverse("user_list"))
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.groups.filter(name="Validador").exists())

    def test_non_root_admin_cannot_grant_validator_role(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.post(reverse("grant_validator_role", args=[self.customer.pk]))

        self.assertEqual(response.status_code, 400)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.groups.filter(name="Validador").exists())

    def test_root_admin_can_revoke_validator_role(self):
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        self.customer.groups.add(group)

        self.client.login(username="admin", password="StrongPass123!")
        response = self.client.post(reverse("revoke_validator_role", args=[self.customer.pk]))

        self.assertRedirects(response, reverse("user_list"))
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.groups.filter(name="Validador").exists())

    def test_root_admin_can_block_user_account(self):
        self.client.login(username="admin", password="StrongPass123!")
        response = self.client.post(reverse("block_user_account", args=[self.customer.pk]))

        self.assertRedirects(response, reverse("user_list"))
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_active)

    def test_non_root_admin_cannot_block_user_account(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.post(reverse("block_user_account", args=[self.customer.pk]))

        self.assertEqual(response.status_code, 400)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_active)

    def test_root_admin_can_unblock_user_account(self):
        self.customer.is_active = False
        self.customer.save(update_fields=["is_active"])
        self.client.login(username="admin", password="StrongPass123!")
        response = self.client.post(reverse("unblock_user_account", args=[self.customer.pk]))

        self.assertRedirects(response, reverse("user_list"))
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_active)

    def test_user_list_can_filter_only_blocked_accounts(self):
        self.customer.is_active = False
        self.customer.save(update_fields=["is_active"])

        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_list"), {"account": "blocked"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "users_customer")
        self.assertNotContains(response, "users_admin")


class AdminTicketManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="tickets_admin",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        self.customer = User.objects.create_user(
            username="tickets_customer",
            password="StrongPass123!",
        )
        self.event = Event.objects.create(
            title="Evento Tickets Admin",
            datetime=datetime(2026, 12, 1, 20, 0, tzinfo=timezone.utc),
            status=Event.Status.ACTIVE,
        )
        self.order = Order.objects.create(
            user=self.customer,
            event=self.event,
            status=Order.Status.PAID,
        )
        self.ticket = Ticket.objects.create(
            order=self.order,
            event=self.event,
            status=Ticket.Status.UNUSED,
            token_ref="sample.token",
            raffle_number=12,
        )
        self.other_customer = User.objects.create_user(
            username="tickets_other_customer",
            password="StrongPass123!",
        )
        self.other_order = Order.objects.create(
            user=self.other_customer,
            event=self.event,
            status=Order.Status.PAID,
        )
        self.other_ticket = Ticket.objects.create(
            order=self.other_order,
            event=self.event,
            status=Ticket.Status.UNUSED,
            token_ref="other.token",
            raffle_number=34,
        )
        self.second_event = Event.objects.create(
            title="Evento Secundario",
            datetime=datetime(2026, 12, 2, 21, 0, tzinfo=timezone.utc),
            status=Event.Status.ACTIVE,
        )
        self.second_order = Order.objects.create(
            user=self.customer,
            event=self.second_event,
            status=Order.Status.PAID,
        )
        self.second_ticket = Ticket.objects.create(
            order=self.second_order,
            event=self.second_event,
            status=Ticket.Status.UNUSED,
            token_ref="second.token",
            raffle_number=56,
        )
        self.product = Product.objects.create(
            event=self.event,
            name="Gorra Admin",
            price_usd=Decimal("12.00"),
            is_active=True,
            has_variants=False,
        )
        self.product_variant = ProductVariant.objects.create(
            product=self.product,
            name="Unidad",
            stock_total=20,
            is_active=True,
        )
        OrderItem.objects.create(
            order=self.order,
            event=self.event,
            item_type=OrderItem.ItemType.PRODUCT,
            product_variant=self.product_variant,
            item_name="Gorra Admin",
            variant_name="",
            unit_price_usd=Decimal("12.00"),
            quantity=2,
            total_usd=Decimal("24.00"),
        )
        self.product_redemption = ProductRedemption.objects.create(
            order=self.order,
            user=self.customer,
            event=self.event,
        )

    def test_user_tickets_requires_validation_permission(self):
        plain = User.objects.create_user(
            username="tickets_plain",
            password="StrongPass123!",
        )
        self.client.login(username="tickets_plain", password="StrongPass123!")
        response = self.client.get(reverse("user_tickets"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_user_products_requires_validation_permission(self):
        plain = User.objects.create_user(
            username="products_plain",
            password="StrongPass123!",
        )
        self.client.login(username="products_plain", password="StrongPass123!")
        response = self.client.get(reverse("user_products"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_validator_can_view_user_tickets(self):
        validator = User.objects.create_user(
            username="tickets_validator",
            password="StrongPass123!",
        )
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        validator.groups.add(group)

        self.client.login(username="tickets_validator", password="StrongPass123!")
        response = self.client.get(reverse("user_tickets"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Participaciones compradas")
        self.assertContains(response, "por usuarios")
        self.assertNotContains(response, "Eliminar participacion")

    def test_validator_can_view_user_products(self):
        validator = User.objects.create_user(
            username="products_validator",
            password="StrongPass123!",
        )
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        validator.groups.add(group)

        self.client.login(username="products_validator", password="StrongPass123!")
        response = self.client.get(reverse("user_products"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Productos comprados")
        self.assertContains(response, self.product_redemption.code)
        self.assertContains(response, "Gorra Admin")
    def test_validator_can_filter_user_tickets_by_username(self):
        validator = User.objects.create_user(
            username="tickets_validator_filter_user",
            password="StrongPass123!",
        )
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        validator.groups.add(group)

        self.client.login(username="tickets_validator_filter_user", password="StrongPass123!")
        response = self.client.get(reverse("user_tickets"), {"username": "tickets_customer"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tickets_customer")
        self.assertNotContains(response, "tickets_other_customer")

    def test_staff_can_view_user_tickets(self):
        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_tickets"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Participaciones compradas")
        self.assertContains(response, "por usuarios")
        self.assertContains(response, "tickets_customer")
        self.assertContains(response, "Evento Tickets Admin")
        self.assertContains(response, self.ticket.raffle_number_display)

    def test_staff_can_view_ticket_type_summary_cards(self):
        vip_type = EventTicketType.objects.create(
            event=self.event,
            code=EventTicketType.Code.VIP,
            name="VIP",
            price_usd=Decimal("8.00"),
            stock_total=20,
            is_active=True,
            display_order=2,
            number_prefix="VIP",
        )
        vip_order = Order.objects.create(
            user=self.customer,
            event=self.event,
            ticket_type=vip_type,
            status=Order.Status.PAID,
            unit_price_usd=Decimal("8.00"),
            quantity=1,
            total_usd=Decimal("8.00"),
        )
        OrderItem.objects.create(
            order=vip_order,
            event=self.event,
            item_type=OrderItem.ItemType.TICKET,
            ticket_type=vip_type,
            item_name=f"{self.event.title} - VIP",
            unit_price_usd=Decimal("8.00"),
            quantity=1,
            total_usd=Decimal("8.00"),
        )
        Ticket.objects.create(
            order=vip_order,
            event=self.event,
            ticket_type=vip_type,
            status=Ticket.Status.UNUSED,
            token_ref="vip.token",
            raffle_number=1,
        )

        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_tickets"), {"event_id": str(self.event.pk)})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Resumen General")
        self.assertContains(response, "Resumen VIP")
        self.assertContains(response, "USD 8,00")

    def test_staff_ticket_type_summary_uses_order_item_totals(self):
        general_type = self.event.ensure_general_ticket_type()
        mixed_order = Order.objects.create(
            user=self.customer,
            event=self.event,
            status=Order.Status.PAID,
            unit_price_usd=Decimal("11.33"),
            quantity=3,
            total_usd=Decimal("34.00"),
        )
        OrderItem.objects.create(
            order=mixed_order,
            event=self.event,
            item_type=OrderItem.ItemType.TICKET,
            ticket_type=general_type,
            item_name=f"{self.event.title} - General",
            unit_price_usd=Decimal("5.00"),
            quantity=2,
            total_usd=Decimal("10.00"),
        )
        Ticket.objects.create(
            order=mixed_order,
            event=self.event,
            ticket_type=general_type,
            status=Ticket.Status.UNUSED,
            token_ref="summary.mixed.1",
            raffle_number=50,
        )
        Ticket.objects.create(
            order=mixed_order,
            event=self.event,
            ticket_type=general_type,
            status=Ticket.Status.UNUSED,
            token_ref="summary.mixed.2",
            raffle_number=51,
        )

        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_tickets"), {"event_id": str(self.event.pk)})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "USD 10,00")

    def test_staff_can_view_tickets_filtered_by_user(self):
        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_tickets_by_user", args=[self.customer.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Usuario seleccionado:")
        self.assertContains(response, "tickets_customer")
        self.assertContains(response, self.ticket.raffle_number_display)
        self.assertNotContains(response, f">{self.other_ticket.raffle_number_display}<")

    def test_staff_can_filter_user_tickets_by_event(self):
        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.get(
            reverse("user_tickets_by_user", args=[self.customer.pk]),
            {"event_id": str(self.second_event.pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.second_ticket.raffle_number_display)
        self.assertNotContains(response, f">{self.ticket.raffle_number_display}<")

    def test_staff_can_filter_global_tickets_by_event(self):
        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.get(
            reverse("user_tickets"),
            {"event_id": str(self.second_event.pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.second_ticket.raffle_number_display)
        self.assertNotContains(response, f">{self.ticket.raffle_number_display}<")

    def test_validator_can_view_user_ticket_qrs_page(self):
        validator = User.objects.create_user(
            username="tickets_qr_validator",
            password="StrongPass123!",
        )
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        validator.groups.add(group)

        self.client.login(username="tickets_qr_validator", password="StrongPass123!")
        response = self.client.get(reverse("user_ticket_qrs_by_user", args=[self.customer.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Codigos QR de")
        self.assertContains(response, "data:image/png;base64,")

    def test_user_ticket_qrs_requires_validation_permission(self):
        plain = User.objects.create_user(
            username="tickets_qr_plain",
            password="StrongPass123!",
        )
        self.client.login(username="tickets_qr_plain", password="StrongPass123!")
        response = self.client.get(reverse("user_ticket_qrs_by_user", args=[self.customer.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_user_list_shows_link_to_view_user_tickets(self):
        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("user_ticket_qrs_by_user", args=[self.customer.pk]),
        )
        self.assertContains(
            response,
            reverse("user_products_by_user", args=[self.customer.pk]),
        )

    def test_staff_can_view_products_filtered_by_user(self):
        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_products_by_user", args=[self.customer.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Usuario seleccionado:")
        self.assertContains(response, self.product_redemption.code)
        self.assertContains(response, "Gorra Admin")

    def test_user_list_filters_by_username_query(self):
        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_list"), {"q": "tickets_customer"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tickets_customer")
        self.assertNotContains(response, "tickets_other_customer")

    def test_user_list_filters_by_role_validator(self):
        validator = User.objects.create_user(
            username="tickets_validator_filter",
            password="StrongPass123!",
        )
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        validator.groups.add(group)

        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_list"), {"role": "validator"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tickets_validator_filter")
        self.assertNotContains(response, "tickets_customer")
        self.assertNotContains(response, "tickets_admin")


class CleanupExpiredTicketsCommandTests(TestCase):
    def test_cleanup_command_deletes_only_tickets_past_45_days_after_event_end(self):
        from django.utils import timezone as dj_timezone

        user = User.objects.create_user(username="cleanup_user", password="StrongPass123!")
        now = dj_timezone.now()

        ended_event = Event.objects.create(
            title="Evento finalizado hace mas de 45 dias",
            datetime=now - timedelta(days=50),
            end_datetime=now - timedelta(days=46),
            status=Event.Status.ACTIVE,
        )
        fresh_event = Event.objects.create(
            title="Evento finalizado hace pocos dias",
            datetime=now - timedelta(days=10),
            end_datetime=now - timedelta(days=1),
            status=Event.Status.ACTIVE,
        )

        old_order = Order.objects.create(user=user, event=ended_event, status=Order.Status.PAID)
        fresh_order = Order.objects.create(user=user, event=fresh_event, status=Order.Status.PAID)
        old_ticket = Ticket.objects.create(order=old_order, event=ended_event, status=Ticket.Status.UNUSED)
        fresh_ticket = Ticket.objects.create(
            order=fresh_order,
            event=fresh_event,
            status=Ticket.Status.UNUSED,
        )

        call_command("cleanup_expired_tickets")

        self.assertFalse(Ticket.objects.filter(pk=old_ticket.pk).exists())
        self.assertTrue(Ticket.objects.filter(pk=fresh_ticket.pk).exists())


class RaffleReminderCommandTests(TestCase):
    def test_send_raffle_reminders_creates_8_day_notification_once(self):
        from django.utils import timezone as dj_timezone

        user = User.objects.create_user(username="reminder_user", password="StrongPass123!")
        now = dj_timezone.now()
        event = Event.objects.create(
            title="Rifa recordatorio",
            datetime=now - timedelta(days=1),
            end_datetime=now + timedelta(days=8),
            status=Event.Status.ACTIVE,
        )
        order = Order.objects.create(user=user, event=event, status=Order.Status.PAID)
        Ticket.objects.create(order=order, event=event, status=Ticket.Status.UNUSED)

        call_command("send_raffle_reminders")
        call_command("send_raffle_reminders")

        notifications = Notification.objects.filter(
            user=user,
            title="Faltan 8 dias para el sorteo",
            link_url=reverse("event_detail", args=[event.pk]) + "?reminder=8d",
        )
        self.assertEqual(notifications.count(), 1)


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class ProfileCustomizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="profile_user",
            email="profile_user@example.com",
            password="StrongPass123!",
        )
        self.admin = User.objects.create_user(
            username="profile_admin",
            email="profile_admin@example.com",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )

    def test_profile_requires_authentication(self):
        response = self.client.get(reverse("edit_profile"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_user_can_update_profile_fields_and_photo(self):
        self.client.login(username="profile_user", password="StrongPass123!")
        file = SimpleUploadedFile(
            "avatar.gif",
            b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
            content_type="image/gif",
        )

        response = self.client.post(
            reverse("edit_profile"),
            {
                "email": "profile_user_updated@example.com",
                "document_number": "100000201",
                "contact_number": "3001234501",
                "display_name": "Nombre Visible",
                "bio": "Bio corta",
                "photo": file,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse("home"))
        self.assertContains(response, "Cambios guardados.")
        profile = Profile.objects.get(user=self.user)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "profile_user_updated@example.com")
        self.assertEqual(profile.display_name, "Nombre Visible")
        self.assertEqual(profile.bio, "Bio corta")
        self.assertTrue(bool(profile.photo))

    def test_admin_can_customize_own_profile(self):
        self.client.login(username="profile_admin", password="StrongPass123!")
        response = self.client.post(
            reverse("edit_profile"),
            {
                "email": "profile_admin_updated@example.com",
                "document_number": "100000202",
                "contact_number": "3001234502",
                "display_name": "Admin Visible",
                "bio": "Admin bio",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse("home"))
        self.assertContains(response, "Cambios guardados.")
        profile = Profile.objects.get(user=self.admin)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.email, "profile_admin_updated@example.com")
        self.assertEqual(profile.display_name, "Admin Visible")

    def test_user_can_delete_profile_photo_when_saving_changes(self):
        self.client.login(username="profile_user", password="StrongPass123!")
        file = SimpleUploadedFile(
            "avatar.gif",
            b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
            content_type="image/gif",
        )
        self.client.post(
            reverse("edit_profile"),
            {
                "email": "profile_user_photo@example.com",
                "document_number": "100000203",
                "contact_number": "3001234503",
                "display_name": "Con Foto",
                "bio": "Bio",
                "photo": file,
            },
            follow=True,
        )

        response = self.client.post(
            reverse("edit_profile"),
            {
                "email": "profile_user_photo@example.com",
                "document_number": "100000203",
                "contact_number": "3001234503",
                "display_name": "Con Foto",
                "bio": "Bio",
                "photo-clear": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        profile = Profile.objects.get(user=self.user)
        self.assertFalse(bool(profile.photo))

    def test_profile_shows_registered_email_field(self):
        self.client.login(username="profile_user", password="StrongPass123!")
        response = self.client.get(reverse("edit_profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correo electrónico")
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'name="contact_number"')

    def test_profile_rejects_non_numeric_document_or_contact(self):
        self.client.login(username="profile_user", password="StrongPass123!")
        response = self.client.post(
            reverse("edit_profile"),
            {
                "email": "profile_user@example.com",
                "document_number": "DOC-ABC",
                "contact_number": "300-ABCD",
                "display_name": "Nombre",
                "bio": "Bio",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "solo puede contener dígitos")


class ConcurrencyValidationTests(TransactionTestCase):
    def setUp(self):
        self.event = Event.objects.create(
            title="Evento Concurrente",
            datetime=datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc),
            status=Event.Status.ACTIVE,
        )
        self.customer = User.objects.create_user(
            username="cliente_conc", password="StrongPass123!"
        )
        self.order = Order.objects.create(
            user=self.customer, event=self.event, status=Order.Status.PAID
        )
        self.ticket = Ticket.objects.create(
            order=self.order, event=self.event, status=Ticket.Status.UNUSED
        )

    def _attempt_consume(self):
        close_old_connections()
        try:
            return consume_ticket_atomic(self.ticket.pk)
        finally:
            close_old_connections()

    def test_concurrent_attempts_only_consume_once(self):
        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(lambda _: self._attempt_consume(), range(20)))

        self.ticket.refresh_from_db()
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.ticket.status, Ticket.Status.USED)


class QuickValidationScenariosTests(TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.event = Event.objects.create(
            title="Evento Demo Rapida",
            datetime=now - timedelta(hours=1),
            end_datetime=now + timedelta(days=1),
            status=Event.Status.ACTIVE,
        )
        self.customer = User.objects.create_user(
            username="quick_customer",
            password="StrongPass123!",
        )
        self.admin = User.objects.create_user(
            username="quick_admin",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )

    def _buy_ticket(self):
        self.client.login(username="quick_customer", password="StrongPass123!")
        self.client.post(reverse("add_ticket_to_cart", args=[self.event.pk]), {"quantity": "1"})
        response = self.client.post(reverse("checkout_cart"))
        self.assertEqual(response.status_code, 200)
        ticket = Ticket.objects.get(order__user=self.customer, event=self.event)
        self.client.logout()
        return ticket

    def test_quick_happy_path_purchase_then_validation_sets_used(self):
        ticket = self._buy_ticket()

        self.client.login(username="quick_admin", password="StrongPass123!")
        response = self.client.post(
            reverse("validate_token"),
            {"token": ticket.token_ref},
        )

        ticket.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ticket.status, Ticket.Status.USED)
        self.assertTrue(
            ValidationLog.objects.filter(
                ticket=ticket,
                outcome=ValidationLog.Outcome.ACCEPTED,
            ).exists()
        )

    def test_quick_reuse_is_blocked(self):
        ticket = self._buy_ticket()
        self.client.login(username="quick_admin", password="StrongPass123!")
        self.client.post(reverse("validate_token"), {"token": ticket.token_ref})
        response = self.client.post(reverse("validate_token"), {"token": ticket.token_ref})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Esta entrada ya fue validada antes")
        self.assertGreaterEqual(
            ValidationLog.objects.filter(
                ticket=ticket, outcome=ValidationLog.Outcome.REJECTED
            ).count(),
            1,
        )

    def test_quick_tampered_token_is_rejected(self):
        ticket = self._buy_ticket()
        payload, signature = ticket.token_ref.split(".", 1)
        replacement = "A" if signature[0] != "A" else "B"
        tampered_token = f"{payload}.{replacement}{signature[1:]}"

        self.client.login(username="quick_admin", password="StrongPass123!")
        response = self.client.post(
            reverse("validate_token"),
            {"token": tampered_token},
        )

        ticket.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Firma HMAC invalida")
        self.assertEqual(ticket.status, Ticket.Status.UNUSED)


class CommerceFoundationModelsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="commerce_user", password="StrongPass123!")
        self.event = Event.objects.create(
            title="Carnaval Shop",
            datetime=datetime.now(timezone.utc) - timedelta(hours=2),
            end_datetime=datetime.now(timezone.utc) + timedelta(hours=4),
            status=Event.Status.ACTIVE,
            unit_price_usd=Decimal("15.00"),
            ticket_limit=100,
        )
        self.general_type = self.event.ensure_general_ticket_type()
    def test_cart_total_uses_ticket_items(self):
        cart = Cart.objects.create(user=self.user, event=self.event)
        vip_type = EventTicketType.objects.create(
            event=self.event,
            code=EventTicketType.Code.VIP,
            name="VIP",
            price_usd=Decimal("15.00"),
            stock_total=20,
            is_active=True,
            display_order=2,
            number_prefix="V",
        )
        CartItem.objects.create(
            cart=cart,
            item_type=CartItem.ItemType.TICKET,
            ticket_type=self.general_type,
            quantity=2,
            unit_price_usd=Decimal("15.00"),
        )
        CartItem.objects.create(
            cart=cart,
            item_type=CartItem.ItemType.TICKET,
            ticket_type=vip_type,
            quantity=3,
            unit_price_usd=Decimal("15.00"),
        )

        self.assertEqual(cart.items.count(), 2)
        self.assertEqual(cart.total_usd, Decimal("75.00"))

    def test_user_can_only_have_one_active_cart(self):
        Cart.objects.create(user=self.user, event=self.event, status=Cart.Status.ACTIVE)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Cart.objects.create(user=self.user, event=self.event, status=Cart.Status.ACTIVE)

    def test_product_variant_remaining_stock_uses_paid_order_items(self):
        product = Product.objects.create(
            event=self.event,
            name="Camiseta Carnaval",
            price_usd=Decimal("25.00"),
            has_variants=True,
        )
        variant = ProductVariant.objects.create(
            product=product,
            name="M / Roja",
            stock_total=10,
        )
        order = Order.objects.create(
            user=self.user,
            event=self.event,
            ticket_type=self.general_type,
            unit_price_usd=Decimal("15.00"),
            quantity=1,
            total_usd=Decimal("15.00"),
            status=Order.Status.PAID,
        )
        OrderItem.objects.create(
            order=order,
            event=self.event,
            item_type=OrderItem.ItemType.PRODUCT,
            product_variant=variant,
            item_name="Camiseta Carnaval",
            variant_name="M / Roja",
            unit_price_usd=Decimal("25.00"),
            quantity=3,
            total_usd=Decimal("75.00"),
        )

        self.assertEqual(variant.sold_quantity, 3)
        self.assertEqual(variant.remaining_stock, 7)

    def test_cart_item_product_requires_product_variant(self):
        product = Product.objects.create(
            event=self.event,
            name="Gorra Carnaval",
            price_usd=Decimal("12.00"),
            has_variants=False,
        )
        variant = ProductVariant.objects.create(
            product=product,
            name="Unidad",
            stock_total=50,
        )
        cart = Cart.objects.create(user=self.user, event=self.event)

        CartItem.objects.create(
            cart=cart,
            item_type=CartItem.ItemType.PRODUCT,
            product_variant=variant,
            quantity=2,
            unit_price_usd=Decimal("12.00"),
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CartItem.objects.create(
                    cart=cart,
                    item_type=CartItem.ItemType.PRODUCT,
                    quantity=1,
                    unit_price_usd=Decimal("12.00"),
                )

    def test_product_redemption_generates_code(self):
        order = Order.objects.create(
            user=self.user,
            event=self.event,
            ticket_type=self.general_type,
            unit_price_usd=Decimal("15.00"),
            quantity=1,
            total_usd=Decimal("15.00"),
            status=Order.Status.PAID,
        )

        redemption = ProductRedemption.objects.create(
            order=order,
            user=self.user,
            event=self.event,
        )

        self.assertTrue(redemption.code.startswith("PROD-"))
        self.assertEqual(redemption.status, ProductRedemption.Status.PENDING)
