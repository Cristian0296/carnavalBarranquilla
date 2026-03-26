from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import tempfile

from django.contrib.auth.models import User
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from .models import (
    Event,
    EventImage,
    Notification,
    Order,
    Profile,
    Review,
    ReviewReport,
    ReviewReaction,
    Ticket,
    ValidationLog,
)
from .services import consume_ticket_atomic, generate_ticket_token


class AuthFlowTests(TestCase):
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
        )

        self.assertRedirects(response, reverse("login"))
        self.assertTrue(
            User.objects.filter(username="cliente1", email="cliente1@example.com").exists()
        )
        self.assertTrue(
            Profile.objects.filter(user__username="cliente1", display_name="Cliente Uno").exists()
        )

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

    def test_home_is_accessible(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

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
        self.assertFalse(User.objects.filter(username="cliente_nuevo").exists())

    def test_signup_rejects_duplicate_display_name(self):
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

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Este nombre para mostrar ya está registrado.")
        self.assertFalse(User.objects.filter(username="cliente_display_nuevo").exists())
        self.assertFalse(User.objects.filter(username="cliente_doc_invalido").exists())

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

    def test_event_list_shows_only_active_events(self):
        response = self.client.get(reverse("event_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Evento Activo")
        self.assertContains(response, "USD 2,50 por participacion")
        self.assertNotContains(response, "Evento Programado")
        self.assertNotContains(response, "Evento Activo Finalizado")
        self.assertNotContains(response, "Evento Inactivo")

    def test_event_detail_for_active_event(self):
        response = self.client.get(reverse("event_detail", args=[self.active_event.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Evento Activo")
        self.assertContains(response, "USD 2,50")

    def test_event_detail_displays_raffle_range_with_dynamic_width(self):
        self.active_event.ticket_limit = 100
        self.active_event.save(update_fields=["ticket_limit"])

        response = self.client.get(reverse("event_detail", args=[self.active_event.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "000 a 100")

    def test_event_detail_returns_404_for_inactive_event(self):
        response = self.client.get(reverse("event_detail", args=[self.inactive_event.pk]))
        self.assertEqual(response.status_code, 404)

    def test_event_detail_returns_404_for_scheduled_active_event(self):
        response = self.client.get(reverse("event_detail", args=[self.scheduled_active_event.pk]))
        self.assertEqual(response.status_code, 404)

    def test_purchase_requires_authentication(self):
        response = self.client.post(reverse("purchase_event", args=[self.active_event.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_purchase_rejects_scheduled_active_event_before_start(self):
        user = User.objects.create_user(username="buyer_scheduled", password="StrongPass123!")
        self.client.login(username="buyer_scheduled", password="StrongPass123!")
        response = self.client.post(reverse("purchase_event", args=[self.scheduled_active_event.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("aun no inicia", response.content.decode("utf-8"))

    def test_purchase_creates_paid_order_and_unused_ticket(self):
        user = User.objects.create_user(username="buyer1", password="StrongPass123!")
        self.client.login(username="buyer1", password="StrongPass123!")

        response = self.client.post(reverse("purchase_event", args=[self.active_event.pk]))
        self.assertEqual(response.status_code, 200)

        order = Order.objects.get(user=user, event=self.active_event)
        ticket = Ticket.objects.get(order=order)

        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(order.quantity, 1)
        self.assertEqual(order.unit_price_usd, Decimal("2.50"))
        self.assertEqual(order.total_usd, Decimal("2.50"))
        self.assertEqual(ticket.status, Ticket.Status.UNUSED)
        self.assertTrue(ticket.token_ref)
        self.assertEqual(len(ticket.token_ref.split(".")), 2)
        self.assertContains(response, str(ticket.ticket_uuid))
        self.assertContains(response, "Cantidad de participaciones: 1")
        self.assertContains(response, "Total pagado (USD): 2,50")

    def test_purchase_multiple_tickets_in_single_order(self):
        user = User.objects.create_user(username="buyer_multi", password="StrongPass123!")
        self.client.login(username="buyer_multi", password="StrongPass123!")

        response = self.client.post(
            reverse("purchase_event", args=[self.active_event.pk]),
            {"quantity": "3"},
        )

        self.assertEqual(response.status_code, 200)
        order = Order.objects.get(user=user, event=self.active_event)
        tickets = Ticket.objects.filter(order=order).order_by("issued_at")
        self.assertEqual(tickets.count(), 3)
        self.assertEqual(order.quantity, 3)
        self.assertEqual(order.unit_price_usd, Decimal("2.50"))
        self.assertEqual(order.total_usd, Decimal("7.50"))
        self.assertContains(response, "Cantidad de participaciones: 3")
        self.assertContains(response, "Total pagado (USD): 7,50")
        self.assertContains(response, "data:image/png;base64,")

    def test_purchase_rejects_when_exceeds_event_ticket_limit(self):
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
        response = self.client.post(
            reverse("purchase_event", args=[self.active_event.pk]),
            {"quantity": "2"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Solo quedan 1 participaciones disponibles", response.content.decode("utf-8"))

    def test_user_can_buy_incrementally_for_same_event(self):
        user = User.objects.create_user(username="buyer_incremental", password="StrongPass123!")
        self.client.login(username="buyer_incremental", password="StrongPass123!")

        first = self.client.post(
            reverse("purchase_event", args=[self.active_event.pk]),
            {"quantity": "1"},
        )
        second = self.client.post(
            reverse("purchase_event", args=[self.active_event.pk]),
            {"quantity": "4"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Order.objects.filter(user=user, event=self.active_event).count(), 2)
        self.assertEqual(
            Ticket.objects.filter(order__user=user, event=self.active_event).count(),
            5,
        )

    def test_purchase_assigns_unique_raffle_numbers_per_event(self):
        user = User.objects.create_user(username="buyer_random_numbers", password="StrongPass123!")
        self.active_event.ticket_limit = 10
        self.active_event.save(update_fields=["ticket_limit"])
        self.client.login(username="buyer_random_numbers", password="StrongPass123!")

        first = self.client.post(
            reverse("purchase_event", args=[self.active_event.pk]),
            {"quantity": "3"},
        )
        second = self.client.post(
            reverse("purchase_event", args=[self.active_event.pk]),
            {"quantity": "3"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        numbers = list(
            Ticket.objects.filter(event=self.active_event).values_list("raffle_number", flat=True)
        )
        self.assertEqual(len(numbers), 6)
        self.assertEqual(len(set(numbers)), 6)
        self.assertTrue(all(number is not None and 0 <= number <= 10 for number in numbers))

    def test_purchase_assigns_raffle_numbers_in_ascending_available_order(self):
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

        response = self.client.post(
            reverse("purchase_event", args=[self.active_event.pk]),
            {"quantity": "3"},
        )

        self.assertEqual(response.status_code, 200)
        purchased_numbers = list(
            Ticket.objects.filter(order__user=user, event=self.active_event)
            .exclude(order=existing_order)
            .order_by("raffle_number")
            .values_list("raffle_number", flat=True)
        )
        self.assertEqual(purchased_numbers, [2, 4, 5])

    def test_purchase_rejects_invalid_quantity(self):
        User.objects.create_user(username="buyer_bad_qty", password="StrongPass123!")
        self.client.login(username="buyer_bad_qty", password="StrongPass123!")

        response = self.client.post(
            reverse("purchase_event", args=[self.active_event.pk]),
            {"quantity": "0"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_purchase_rejects_finished_event(self):
        user = User.objects.create_user(username="buyer_finished_event", password="StrongPass123!")
        self.client.login(username="buyer_finished_event", password="StrongPass123!")

        response = self.client.post(reverse("purchase_event", args=[self.ended_active_event.pk]))

        self.assertEqual(response.status_code, 400)
        self.assertIn("ya finalizo", response.content.decode("utf-8"))
        self.assertEqual(Order.objects.count(), 0)

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
        self.assertContains(response, "Tu opinión fue guardada correctamente")
        self.assertTrue(
            Review.objects.filter(
                event=self.active_event,
                user=user,
                comment="Excelente organizacion",
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
        self.assertContains(response, "No publiques el mismo comentario consecutivamente")
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
        self.assertContains(blocked, "Has alcanzado el")
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
        self.assertContains(response, "Reporte enviado con éxito")
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
        self.assertContains(response, "Reportes de comentarios")
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
        self.assertContains(response, "exitosa")
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
        self.assertContains(response, "La boleta ya fue utilizada")

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
                "unit_price_usd": "1.00",
                "age_rating": Event.AgeRating.PLUS_12,
                "datetime": "2026-08-20T19:30",
                "status": Event.Status.ACTIVE,
            },
        )

        created_event = Event.objects.get(title="Evento Nuevo Admin")
        self.assertRedirects(response, reverse("event_detail", args=[created_event.pk]))
        self.assertEqual(created_event.status, Event.Status.ACTIVE)
        self.assertEqual(created_event.created_by, self.staff)

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
                "unit_price_usd": "1.00",
                "ticket_limit": "100",
                "age_rating": Event.AgeRating.ALL,
                "datetime": "2026-10-20T19:30",
                "status": Event.Status.ACTIVE,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Notification.objects.filter(
                user=subscriber,
                title="Nueva obra disponible",
            ).exists()
        )


class AdminEventManagementTests(TestCase):
    def setUp(self):
        self.event = Event.objects.create(
            title="Evento Gestion",
            datetime=datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc),
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
        self.assertContains(response, "Editar obra")
        self.assertContains(response, "Guardar cambios")
        self.assertContains(response, "Eliminar obra")
        self.assertNotContains(response, "Comprar")

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
                "unit_price_usd": "3.50",
                "age_rating": Event.AgeRating.PLUS_18,
                "datetime": "2026-11-05T21:15",
                "status": Event.Status.INACTIVE,
            },
        )

        self.assertRedirects(response, reverse("event_detail", args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Evento Gestion Editado")
        self.assertEqual(self.event.unit_price_usd, Decimal("3.50"))
        self.assertEqual(self.event.status, Event.Status.INACTIVE)

    def test_staff_can_delete_event(self):
        self.client.login(username="manage_staff", password="StrongPass123!")
        response = self.client.post(reverse("delete_event", args=[self.event.pk]))

        self.assertRedirects(response, reverse("event_list"))
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())

    def test_deleting_event_cascades_related_orders_tickets_reviews_and_logs(self):
        buyer = User.objects.create_user(username="event_cascade_buyer", password="StrongPass123!")
        reviewer = User.objects.create_user(
            username="event_cascade_reviewer", password="StrongPass123!"
        )
        order = Order.objects.create(user=buyer, event=self.event, status=Order.Status.PAID)
        ticket = Ticket.objects.create(
            order=order,
            event=self.event,
            status=Ticket.Status.UNUSED,
            token_ref="cascade.token",
        )
        review = Review.objects.create(
            event=self.event,
            user=reviewer,
            comment="Comentario de cascada",
        )
        log = ValidationLog.objects.create(
            ticket=ticket,
            admin=self.staff,
            outcome=ValidationLog.Outcome.ACCEPTED,
            detail="Log asociado al ticket",
        )

        self.client.login(username="manage_staff", password="StrongPass123!")
        response = self.client.post(reverse("delete_event", args=[self.event.pk]))

        self.assertRedirects(response, reverse("event_list"))
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())
        self.assertFalse(Order.objects.filter(pk=order.pk).exists())
        self.assertFalse(Ticket.objects.filter(pk=ticket.pk).exists())
        self.assertFalse(Review.objects.filter(pk=review.pk).exists())
        self.assertFalse(ValidationLog.objects.filter(pk=log.pk).exists())

    def test_staff_can_upload_only_one_event_image(self):
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
        self.assertEqual(EventImage.objects.filter(event=self.event).count(), 1)

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

    def test_non_staff_cannot_update_or_delete_event(self):
        self.client.login(username="manage_customer", password="StrongPass123!")
        update_response = self.client.post(
            reverse("update_event", args=[self.event.pk]),
            {
                "title": "No permitido",
                "description": "No permitido",
                "location": "No permitido",
                "organizer": "No permitido",
                "category": "No permitido",
                "age_rating": Event.AgeRating.ALL,
                "datetime": "2026-11-05T21:15",
                "status": Event.Status.ACTIVE,
            },
        )
        delete_response = self.client.post(reverse("delete_event", args=[self.event.pk]))

        self.assertEqual(update_response.status_code, 302)
        self.assertIn(reverse("login"), update_response.url)
        self.assertEqual(delete_response.status_code, 302)
        self.assertIn(reverse("login"), delete_response.url)


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
        self.assertContains(response, "Usuarios registrados")
        self.assertContains(response, "Crear usuario nuevo")
        self.assertContains(response, "users_admin")
        self.assertContains(response, "users_customer")
        self.assertContains(response, "users_other_staff")

    def test_staff_can_create_user_from_user_management(self):
        self.client.login(username="users_admin", password="StrongPass123!")
        response = self.client.post(
            reverse("create_user"),
            {
                "username": "created_from_admin",
                "email": "created_from_admin@example.com",
                "document_number": "100000101",
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

    def test_user_tickets_requires_validation_permission(self):
        plain = User.objects.create_user(
            username="tickets_plain",
            password="StrongPass123!",
        )
        self.client.login(username="tickets_plain", password="StrongPass123!")
        response = self.client.get(reverse("user_tickets"))
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
        self.assertContains(response, "Participaciones compradas por usuarios")
        self.assertNotContains(response, "Eliminar participación")

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

    def test_validator_cannot_delete_ticket(self):
        validator = User.objects.create_user(
            username="tickets_validator_no_delete",
            password="StrongPass123!",
        )
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_validate_tickets",
        )
        group, _ = Group.objects.get_or_create(name="Validador")
        group.permissions.add(permission)
        validator.groups.add(group)

        self.client.login(username="tickets_validator_no_delete", password="StrongPass123!")
        response = self.client.post(reverse("delete_ticket", args=[self.ticket.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
        self.assertTrue(Ticket.objects.filter(pk=self.ticket.pk).exists())

    def test_staff_can_view_user_tickets(self):
        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_tickets"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Participaciones compradas por usuarios")
        self.assertContains(response, "tickets_customer")
        self.assertContains(response, "Evento Tickets Admin")
        self.assertContains(response, self.ticket.raffle_number_display)

    def test_staff_can_delete_ticket(self):
        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.post(reverse("delete_ticket", args=[self.ticket.pk]))

        self.assertRedirects(response, reverse("user_tickets"))
        self.assertFalse(Ticket.objects.filter(pk=self.ticket.pk).exists())

    def test_staff_can_view_tickets_filtered_by_user(self):
        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_tickets_by_user", args=[self.customer.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mostrando participaciones de")
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
        self.assertContains(response, "QR de participaciones")
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

    def test_staff_can_delete_ticket_from_user_qr_page_and_return(self):
        self.client.login(username="tickets_admin", password="StrongPass123!")
        target = reverse("user_ticket_qrs_by_user", args=[self.customer.pk])
        response = self.client.post(
            reverse("delete_ticket", args=[self.ticket.pk]),
            {"next": target},
        )

        self.assertRedirects(response, target)
        self.assertFalse(Ticket.objects.filter(pk=self.ticket.pk).exists())

    def test_user_list_shows_link_to_view_user_tickets(self):
        self.client.login(username="tickets_admin", password="StrongPass123!")
        response = self.client.get(reverse("user_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("user_tickets_by_user", args=[self.customer.pk]),
        )

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
        self.assertContains(response, "Perfil actualizado correctamente")
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
        self.assertContains(response, "Correo electronico")
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'name="document_number"')
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
        self.assertContains(response, "solo puede contener digitos")


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
        self.event = Event.objects.create(
            title="Evento Demo Rapida",
            datetime=datetime(2026, 7, 1, 19, 0, tzinfo=timezone.utc),
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
        response = self.client.post(reverse("purchase_event", args=[self.event.pk]))
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
        self.assertContains(response, "La boleta ya fue utilizada")
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
