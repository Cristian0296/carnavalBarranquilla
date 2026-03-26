from decimal import Decimal
from pathlib import Path
import re


from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.core import signing
from django.core.files.images import get_image_dimensions
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Count, Max, Min, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.http import (
    FileResponse,
    Http404,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    HttpResponse,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.views.generic import DetailView, ListView, TemplateView

from .forms import (
    EmailOrUsernameAuthenticationForm,
    EmailRequiredUserCreationForm,
    EventCreateForm,
    RulesContentForm,
    EventUpdateForm,
    ProfileForm,
    ReviewForm,
    UserEmailUpdateForm,
)
from .models import (
    Event,
    EventImage,
    EventTicketType,
    Notification,
    Order,
    Profile,
    Review,
    ReviewReport,
    ReviewReaction,
    Ticket,
    ValidationLog,
)
from .moderation import contains_blocked_language
from .services import (
    build_qr_data_uri,
    build_qr_jpg_bytes,
    consume_ticket_atomic,
    generate_ticket_token,
    verify_ticket_token,
)

MAX_EVENT_IMAGES = 3
RECOMMENDED_IMAGE_RATIO = 16 / 9
RECOMMENDED_IMAGE_RATIO_TOLERANCE = 0.05
RULES_FILENAME = "JDL_Trocas_Official_Rules.md"


def _is_recommended_image_ratio(uploaded_file):
    width, height = get_image_dimensions(uploaded_file)
    if not width or not height:
        # Si no se puede leer el tamaño, mostramos igualmente la recomendación.
        return False
    ratio = width / height
    return abs(ratio - RECOMMENDED_IMAGE_RATIO) <= RECOMMENDED_IMAGE_RATIO_TOLERANCE


def _is_root_admin(user):
    return bool(user.is_authenticated and user.is_staff and user.username == "admin")


def _can_validate_tickets(user):
    return bool(user.is_authenticated and user.has_perm("core.can_validate_tickets"))


def _validator_group_names():
    return ["Validador", "Validator"]


def _ensure_validator_group():
    primary_name = _validator_group_names()[0]
    group, _ = Group.objects.get_or_create(name=primary_name)
    permission = Permission.objects.filter(
        content_type__app_label="core",
        codename="can_validate_tickets",
    ).first()
    if permission and not group.permissions.filter(pk=permission.pk).exists():
        group.permissions.add(permission)
    for alias in _validator_group_names()[1:]:
        legacy_group = Group.objects.filter(name=alias).first()
        if not legacy_group:
            continue
        if permission and not legacy_group.permissions.filter(pk=permission.pk).exists():
            legacy_group.permissions.add(permission)
    return group


def _can_manage_event_images(user, event):
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    return event.created_by_id == user.id


def _display_name_for_user(user):
    try:
        profile_name = (user.profile.display_name or "").strip()
    except Exception:
        profile_name = ""
    return profile_name or user.username


def _ticket_validation_info(ticket):
    buyer = ticket.order.user
    raffle_status = "Sorteo realizado" if ticket.event.has_finished() else "En espera del sorteo"
    ticket_type_name = (
        ticket.ticket_type.name
        if getattr(ticket, "ticket_type_id", None) and ticket.ticket_type
        else "General"
    )
    ticket_type_code = (
        ticket.ticket_type.code
        if getattr(ticket, "ticket_type_id", None) and ticket.ticket_type
        else EventTicketType.Code.GENERAL
    )
    return {
        "title": ticket.event.title,
        "buyer_name": _display_name_for_user(buyer),
        "start_at": ticket.event.datetime,
        "draw_at": ticket.event.end_datetime or ticket.event.datetime,
        "ticket_number": ticket.raffle_number_display or str(ticket.ticket_uuid),
        "ticket_type_name": ticket_type_name,
        "ticket_type_code": ticket_type_code,
        "raffle_status": raffle_status,
    }


def _create_notification_if_missing(user, title, body, link_url=""):
    exists = Notification.objects.filter(
        user=user,
        title=title,
        body=body,
        link_url=link_url,
    ).exists()
    if not exists:
        Notification.objects.create(
            user=user,
            title=title,
            body=body,
            link_url=link_url,
        )


TRANSFER_TICKET_SALT = "ticket-transfer-preview"


def _build_my_tickets_context(user, selected_event_id=None, transfer_state=None):
    user_tickets = Ticket.objects.select_related("event", "order").filter(order__user=user)

    event_cards_qs = (
        Event.objects.filter(tickets__order__user=user)
        .prefetch_related("images")
        .annotate(
            participation_count=Count(
                "tickets",
                filter=Q(tickets__order__user=user),
                distinct=True,
            ),
            last_purchase_at=Max(
                "orders__created_at",
                filter=Q(orders__user=user),
            ),
        )
        .order_by("-last_purchase_at", "title")
    )
    event_cards = []
    event_ids = set()
    for event in event_cards_qs:
        cover = event.images.first()
        event_ids.add(event.id)
        event_cards.append(
            {
                "event": event,
                "cover_url": cover.image.url if cover else "",
                "participation_count": event.participation_count,
                "last_purchase_at": event.last_purchase_at,
                "is_selected": selected_event_id == event.id,
            }
        )

    if selected_event_id not in event_ids:
        selected_event_id = None

    event_cards = [
        {
            **card,
            "is_selected": selected_event_id == card["event"].id,
        }
        for card in event_cards
    ]

    selected_event = None
    if selected_event_id:
        selected_event = next(
            (card["event"] for card in event_cards if card["event"].id == selected_event_id),
            None,
        )
        user_tickets = user_tickets.filter(event_id=selected_event_id)
    else:
        user_tickets = user_tickets.none()

    user_tickets = user_tickets.order_by("-issued_at")
    participation_summary = [
        {"event_id": card["event"].id, "event__title": card["event"].title, "total": card["participation_count"]}
        for card in event_cards
    ]
    tickets_with_qr = []
    for ticket in user_tickets:
        is_expired = ticket.event.has_finished()
        qr_data_uri = ""
        if ticket.token_ref and not is_expired:
            qr_data_uri = build_qr_data_uri(ticket.token_ref)
        tickets_with_qr.append(
            {"ticket": ticket, "qr_data_uri": qr_data_uri, "is_expired": is_expired}
        )

    return {
        "tickets_with_qr": tickets_with_qr,
        "event_cards": event_cards,
        "selected_event": selected_event,
        "selected_event_id": selected_event_id,
        "participation_summary": participation_summary,
        "transfer_state": transfer_state,
    }


def _active_ticket_type_cards(event):
    general_type = event.ensure_general_ticket_type()
    ticket_types = list(event.ticket_types.filter(is_active=True).order_by("display_order", "id"))
    if not any(ticket_type.pk == general_type.pk for ticket_type in ticket_types):
        ticket_types.insert(0, general_type)

    cards = []
    for ticket_type in ticket_types:
        sold_count = ticket_type.tickets.count()
        remaining_count = max(ticket_type.stock_total - sold_count, 0)
        cards.append(
            {
                "ticket_type": ticket_type,
                "sold_count": sold_count,
                "remaining_count": remaining_count,
                "max_purchase_quantity": min(20, remaining_count),
                "is_vip": ticket_type.code == EventTicketType.Code.VIP,
            }
        )
    return cards


def _build_ticket_type_summary(tickets_queryset):
    rows = (
        tickets_queryset.values("ticket_type__code", "ticket_type__name")
        .annotate(
            sold_count=Count("id"),
            revenue_total=Sum("order__unit_price_usd"),
        )
        .order_by("ticket_type__code")
    )
    summary = []
    for row in rows:
        code = row["ticket_type__code"] or EventTicketType.Code.GENERAL
        name = row["ticket_type__name"] or "General"
        summary.append(
            {
                "code": code,
                "name": name,
                "sold_count": row["sold_count"] or 0,
                "revenue_total": row["revenue_total"] or Decimal("0.00"),
                "is_vip": code == EventTicketType.Code.VIP,
            }
        )
    return summary


def _ticket_type_label(ticket):
    if getattr(ticket, "ticket_type_id", None) and ticket.ticket_type:
        return ticket.ticket_type.name
    return "General"


def _preview_ticket_transfer(owner, ticket, target_email, selected_event_id):
    normalized_email = (target_email or "").strip().lower()
    ticket_type_label = _ticket_type_label(ticket)
    transfer_state = {
        "mode": "form",
        "ticket_id": ticket.pk,
        "ticket_number": ticket.raffle_number_display,
        "ticket_type_name": ticket_type_label,
        "target_email": normalized_email,
        "selected_event_id": selected_event_id,
    }
    if ticket.status != Ticket.Status.UNUSED or ticket.event.has_finished():
        transfer_state["error"] = "Solo puedes transferir boletas disponibles."
        return transfer_state
    if not normalized_email:
        transfer_state["error"] = "Usuario invalido."
        return transfer_state
    recipient = User.objects.filter(email__iexact=normalized_email, is_active=True).first()
    if not recipient:
        transfer_state["error"] = "Usuario invalido."
        return transfer_state
    if recipient.pk == owner.pk:
        transfer_state["error"] = "No puedes transferirte una boleta a ti mismo."
        return transfer_state

    transfer_state.update(
        {
            "mode": "confirm",
            "recipient_id": recipient.pk,
            "recipient_name": _display_name_for_user(recipient),
            "recipient_email": recipient.email,
            "ticket_type_name": ticket_type_label,
            "transfer_token": signing.dumps(
                {
                    "ticket_id": ticket.pk,
                    "owner_id": owner.pk,
                    "recipient_id": recipient.pk,
                },
                salt=TRANSFER_TICKET_SALT,
            ),
        }
    )
    return transfer_state


def _confirm_ticket_transfer(owner, ticket_id, transfer_token):
    try:
        payload = signing.loads(transfer_token, salt=TRANSFER_TICKET_SALT, max_age=1800)
    except signing.BadSignature:
        return False, "La confirmacion de transferencia no es valida."

    if (
        payload.get("ticket_id") != ticket_id
        or payload.get("owner_id") != owner.pk
        or not payload.get("recipient_id")
    ):
        return False, "La confirmacion de transferencia no es valida."

    with transaction.atomic():
        ticket = (
            Ticket.objects.select_related("event", "order")
            .select_for_update()
            .filter(pk=ticket_id, order__user=owner)
            .first()
        )
        if not ticket:
            return False, "No pudimos encontrar la boleta para transferir."
        if ticket.status != Ticket.Status.UNUSED or ticket.event.has_finished():
            return False, "Solo puedes transferir boletas disponibles."

        recipient = User.objects.filter(pk=payload["recipient_id"], is_active=True).first()
        if not recipient:
            return False, "Usuario invalido."
        if recipient.pk == owner.pk:
            return False, "No puedes transferirte una boleta a ti mismo."

        original_order = ticket.order
        recipient_order = Order.objects.create(
            user=recipient,
            event=ticket.event,
            ticket_type=original_order.ticket_type or ticket.ticket_type,
            unit_price_usd=original_order.unit_price_usd,
            quantity=1,
            total_usd=original_order.unit_price_usd,
            status=Order.Status.PAID,
        )
        ticket.order = recipient_order
        ticket.save(update_fields=["order"])

        remaining_ticket_count = original_order.tickets.count()
        if remaining_ticket_count == 0:
            original_order.delete()
        else:
            original_order.quantity = remaining_ticket_count
            original_order.total_usd = (
                original_order.unit_price_usd * Decimal(remaining_ticket_count)
            ).quantize(Decimal("0.01"))
            original_order.save(update_fields=["quantity", "total_usd"])

    link = f'{reverse("my_tickets")}?event_id={ticket.event_id}'
    transferred_ticket_type = _ticket_type_label(ticket)
    _create_notification_if_missing(
        recipient,
        "Boleta recibida",
        f'Recibiste la boleta {transferred_ticket_type} #{ticket.raffle_number_display} del evento "{ticket.event.title}".',
        link,
    )
    return True, f'La boleta {transferred_ticket_type} #{ticket.raffle_number_display} fue transferida a {_display_name_for_user(recipient)}.'


def _notify_new_event_available(event):
    link = reverse("event_detail", args=[event.pk])
    users = User.objects.filter(is_staff=False, is_active=True)
    title = "Nuevo evento disponible"
    for user in users:
        body = f'Se publicó un nuevo evento: "{event.title}".'
        _create_notification_if_missing(user, title, body, link)


def _active_and_not_finished_filter():
    now = timezone.now()
    return (
        Q(status=Event.Status.ACTIVE)
        & Q(datetime__lte=now)
        & (Q(end_datetime__gt=now) | Q(end_datetime__isnull=True))
    )


class HomeView(TemplateView):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        display_name = ""
        home_role = ""
        home_photo_url = ""
        user = self.request.user
        if user.is_authenticated:
            try:
                profile_name = (user.profile.display_name or "").strip()
            except Exception:
                profile_name = ""
            try:
                home_photo_url = user.profile.photo.url if user.profile.photo else ""
            except Exception:
                home_photo_url = ""
            display_name = profile_name or user.username
            if user.is_staff:
                home_role = "Administrator"
            elif user.has_perm("core.can_validate_tickets"):
                home_role = "Validator"
            else:
                home_role = "User"
        context["home_display_name"] = display_name
        context["home_role"] = home_role
        context["home_photo_url"] = home_photo_url
        return context


class CustomerLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = EmailOrUsernameAuthenticationForm


class EventListView(ListView):
    model = Event
    template_name = "events/event_list.html"
    context_object_name = "events"

    def get_queryset(self):
        for event in Event.objects.filter(_active_and_not_finished_filter()).only("id", "unit_price_usd", "ticket_limit"):
            event.ensure_general_ticket_type()
        return (
            Event.objects.filter(_active_and_not_finished_filter())
            .prefetch_related("images")
            .annotate(
                active_ticket_types_count=Count(
                    "ticket_types",
                    filter=Q(ticket_types__is_active=True),
                    distinct=True,
                ),
                vip_ticket_types_count=Count(
                    "ticket_types",
                    filter=Q(
                        ticket_types__is_active=True,
                        ticket_types__code=EventTicketType.Code.VIP,
                    ),
                    distinct=True,
                ),
                min_ticket_price=Coalesce(
                    Min("ticket_types__price_usd", filter=Q(ticket_types__is_active=True)),
                    "unit_price_usd",
                ),
            )
            .order_by("-created_at", "-id")
        )


class EventDetailView(DetailView):
    model = Event
    template_name = "events/event_detail.html"
    context_object_name = "event"

    def get_queryset(self):
        if self.request.user.is_staff:
            return Event.objects.all()
        if self.request.user.is_authenticated:
            return Event.objects.filter(
                (
                    Q(status=Event.Status.ACTIVE)
                    & (Q(end_datetime__gt=timezone.now()) | Q(end_datetime__isnull=True))
                )
                | Q(created_by=self.request.user)
            )
        return Event.objects.filter(
            Q(status=Event.Status.ACTIVE)
            & (Q(end_datetime__gt=timezone.now()) | Q(end_datetime__isnull=True))
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ticket_type_cards = _active_ticket_type_cards(self.object)
        total_ticket_stock = sum(card["ticket_type"].stock_total for card in ticket_type_cards)
        sold_tickets_count = sum(card["sold_count"] for card in ticket_type_cards)
        remaining_tickets_count = sum(card["remaining_count"] for card in ticket_type_cards)
        replies_qs = Review.objects.select_related("user").order_by("created_at")
        reviews = list(
            self.object.reviews.filter(parent__isnull=True)
            .select_related("user")
            .prefetch_related(Prefetch("replies", queryset=replies_qs))
            .order_by("-updated_at")
        )
        context["reviews"] = reviews
        context["event_images"] = self.object.images.order_by("created_at")
        context["event_image_urls"] = [img.image.url for img in context["event_images"]]
        context["can_manage_event_images"] = _can_manage_event_images(self.request.user, self.object)
        context["event_has_finished"] = self.object.has_finished()
        context["ticket_type_cards"] = ticket_type_cards
        context["admin_ticket_type_summary"] = ticket_type_cards
        context["total_ticket_stock"] = total_ticket_stock
        context["sold_tickets_count"] = sold_tickets_count
        context["remaining_tickets_count"] = remaining_tickets_count
        context["max_purchase_quantity"] = min(20, remaining_tickets_count)
        context["raffle_start_display"] = str(1).zfill(self.object.raffle_number_width)
        context["reviewed"] = self.request.GET.get("reviewed") == "1"
        context["report_sent"] = self.request.GET.get("report_sent") == "1"
        review_ids = []
        for review in reviews:
            review_ids.append(review.id)
            for reply in review.replies.all():
                review_ids.append(reply.id)
        reaction_counts = {review_id: {"like": 0, "dislike": 0} for review_id in review_ids}
        if review_ids:
            rows = (
                ReviewReaction.objects.filter(review_id__in=review_ids)
                .values("review_id", "reaction")
                .annotate(total=Count("id"))
            )
            for row in rows:
                key = "like" if row["reaction"] == ReviewReaction.Reaction.LIKE else "dislike"
                reaction_counts[row["review_id"]][key] = row["total"]
        context["reaction_counts"] = reaction_counts
        user_reactions = {}
        if self.request.user.is_authenticated and review_ids:
            user_rows = ReviewReaction.objects.filter(
                review_id__in=review_ids,
                user=self.request.user,
            ).values("review_id", "reaction")
            user_reactions = {row["review_id"]: row["reaction"] for row in user_rows}
        context["user_reactions"] = user_reactions
        for review in reviews:
            counts = reaction_counts.get(review.id, {"like": 0, "dislike": 0})
            review.like_count = counts["like"]
            review.dislike_count = counts["dislike"]
            review.user_reaction = user_reactions.get(review.id, "")
            for reply in review.replies.all():
                reply_counts = reaction_counts.get(reply.id, {"like": 0, "dislike": 0})
                reply.like_count = reply_counts["like"]
                reply.dislike_count = reply_counts["dislike"]
                reply.user_reaction = user_reactions.get(reply.id, "")
        if self.request.user.is_authenticated:
            context["review_form"] = ReviewForm()
        if self.request.user.is_staff:
            context["event_form"] = EventUpdateForm(instance=self.object)
        return context


def signup(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        form = EmailRequiredUserCreationForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("login")
    else:
        form = EmailRequiredUserCreationForm()

    return render(request, "registration/signup.html", {"form": form})


def _inline_markdown(text):
    escaped = escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def _markdown_body_to_html(markdown_text):
    lines = (markdown_text or "").splitlines()
    html_parts = []
    in_list = False

    for raw in lines:
        stripped = raw.strip()

        if not stripped:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            continue

        if stripped.startswith("- "):
            if not in_list:
                html_parts.append('<ul class="mt-3 list-disc space-y-2 pl-6 text-slate-700">')
                in_list = True
            html_parts.append(f"<li>{_inline_markdown(stripped[2:])}</li>")
            continue

        if in_list:
            html_parts.append("</ul>")
            in_list = False
        html_parts.append(
            f'<p class="mt-3 leading-7 text-slate-700">{_inline_markdown(stripped)}</p>'
        )

    if in_list:
        html_parts.append("</ul>")

    return mark_safe("\n".join(html_parts))


def _parse_rules_markdown(markdown_text):
    lines = (markdown_text or "").splitlines()
    document_title = "Reglas oficiales"
    intro_lines = []
    sections = []
    current = None

    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("# "):
            document_title = stripped[2:].strip() or document_title
            continue
        if stripped.startswith("## "):
            if current is not None:
                sections.append(current)
            current = {"title": stripped[3:].strip(), "lines": []}
            continue
        if stripped.startswith("---"):
            continue
        if current is None:
            intro_lines.append(raw.rstrip())
        else:
            current["lines"].append(raw.rstrip())

    if current is not None:
        sections.append(current)

    parsed_sections = []
    for section in sections:
        body = "\n".join(section["lines"]).strip()
        parsed_sections.append(
            {
                "title": section["title"],
                "body_markdown": body,
                "body_html": _markdown_body_to_html(body),
            }
        )

    intro_markdown = "\n".join(intro_lines).strip()
    return {
        "document_title": document_title,
        "intro_markdown": intro_markdown,
        "intro_html": _markdown_body_to_html(intro_markdown),
        "sections": parsed_sections,
    }


def _build_rules_markdown(document_title, intro_markdown, sections):
    parts = [f"# {document_title}", ""]
    if intro_markdown:
        parts.append(intro_markdown.strip())
        parts.append("")

    for index, section in enumerate(sections):
        parts.append(f"## {section['title']}")
        parts.append("")
        body = (section.get("body_markdown") or "").strip()
        if body:
            parts.append(body)
            parts.append("")
        if index < len(sections) - 1:
            parts.append("------------------------------------------------------------------------")
            parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def rules_page(request):
    rules_path = Path(settings.BASE_DIR) / RULES_FILENAME
    try:
        rules_content = rules_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        rules_content = "No se encontró el archivo de reglas."

    parsed = _parse_rules_markdown(rules_content)
    sections = parsed["sections"]
    edit_idx_raw = (request.GET.get("edit") or request.POST.get("edit_index") or "").strip()
    editing_index = int(edit_idx_raw) if edit_idx_raw.isdigit() else None

    if request.method == "POST":
        if not (request.user.is_authenticated and request.user.is_staff):
            return HttpResponseForbidden("Not authorized.")
        if editing_index is None or editing_index < 0 or editing_index >= len(sections):
            messages.error(request, "Sección de reglas inválida.")
            return redirect("rules_page")

        action = (request.POST.get("action") or "save").strip().lower()
        if action == "delete":
            deleted_title = sections[editing_index]["title"]
            del sections[editing_index]
            updated_markdown = _build_rules_markdown(
                parsed["document_title"], parsed["intro_markdown"], sections
            )
            try:
                rules_path.write_text(updated_markdown, encoding="utf-8")
                messages.success(request, f'Section "{deleted_title}" deleted.')
            except OSError:
                messages.error(request, "Could not save rules file.")
            return redirect("rules_page")

        updated_title = (request.POST.get("section_title") or "").strip()
        updated_body = (request.POST.get("section_content") or "").strip()
        if not updated_title:
            messages.error(request, "Section title is required.")
            return redirect(f"{reverse('rules_page')}?edit={editing_index}")

        sections[editing_index]["title"] = updated_title
        sections[editing_index]["body_markdown"] = updated_body
        updated_markdown = _build_rules_markdown(
            parsed["document_title"], parsed["intro_markdown"], sections
        )
        try:
            rules_path.write_text(updated_markdown, encoding="utf-8")
            messages.success(request, "Sección de reglas actualizada.")
        except OSError:
            messages.error(request, "Could not save rules file.")
        return redirect("rules_page")

    return render(
        request,
        "rules.html",
        {
            "rules_title": parsed["document_title"],
            "rules_filename": RULES_FILENAME,
            "rules_intro_html": parsed["intro_html"],
            "rules_sections": sections,
            "editing_index": editing_index,
        },
    )


def about_page(request):
    return render(request, "about.html")


@login_required
@user_passes_test(lambda user: user.is_staff)
def edit_rules(request):
    rules_path = Path(settings.BASE_DIR) / RULES_FILENAME
    current_content = ""
    if rules_path.exists():
        try:
            current_content = rules_path.read_text(encoding="utf-8")
        except OSError:
            current_content = ""

    form = RulesContentForm(request.POST or None, initial={"content": current_content})
    if request.method == "POST" and form.is_valid():
        try:
            rules_path.write_text(form.cleaned_data["content"], encoding="utf-8")
            messages.success(request, "Reglas actualizadas correctamente.")
            return redirect("edit_rules")
        except OSError:
            messages.error(request, "Could not save rules file.")

    return render(
        request,
        "admin_tools/edit_rules.html",
        {
            "form": form,
            "rules_filename": RULES_FILENAME,
        },
    )


@login_required
def purchase_event(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    event = get_object_or_404(Event, pk=pk, status=Event.Status.ACTIVE)
    if not event.has_started():
        return HttpResponseBadRequest("Este evento aun no ha comenzado.")
    if event.has_finished():
        return HttpResponseBadRequest("Este evento ya finalizo y no acepta compras.")
    ticket_type_code = (request.POST.get("ticket_type") or EventTicketType.Code.GENERAL).strip().lower()
    quantity_raw = request.POST.get("quantity", "1")
    try:
        quantity = int(quantity_raw)
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Cantidad inválida.")
    if quantity < 1 or quantity > 20:
        return HttpResponseBadRequest("Cantidad debe estar entre 1 y 20.")
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            with transaction.atomic():
                event = Event.objects.select_for_update().get(pk=event.pk)
                ticket_type = event.ticket_types.filter(code=ticket_type_code, is_active=True).first()
                if not ticket_type:
                    if ticket_type_code == EventTicketType.Code.GENERAL:
                        ticket_type = event.ensure_general_ticket_type()
                    else:
                        return HttpResponseBadRequest("El tipo de boleta seleccionado no esta disponible.")
                used_numbers = set(
                    Ticket.objects.filter(ticket_type=ticket_type, raffle_number__isnull=False).values_list(
                        "raffle_number",
                        flat=True,
                    )
                )
                total_numbers = ticket_type.stock_total
                remaining_tickets_count = max(total_numbers - len(used_numbers), 0)
                if remaining_tickets_count <= 0:
                    return HttpResponseBadRequest("No hay entradas QR disponibles para este evento.")
                if quantity > remaining_tickets_count:
                    return HttpResponseBadRequest(
                        f"Solo quedan {remaining_tickets_count} entradas QR disponibles."
                    )
                available_numbers = [number for number in range(1, total_numbers + 1) if number not in used_numbers]
                assigned_numbers = available_numbers[:quantity]

                unit_price_usd = ticket_type.price_usd
                total_usd = (unit_price_usd * Decimal(quantity)).quantize(Decimal("0.01"))
                order = Order.objects.create(
                    user=request.user,
                    event=event,
                    ticket_type=ticket_type,
                    unit_price_usd=unit_price_usd,
                    quantity=quantity,
                    total_usd=total_usd,
                    status=Order.Status.PAID,
                )
                tickets_with_qr = []
                for raffle_number in assigned_numbers:
                    ticket = Ticket.objects.create(
                        order=order,
                        event=event,
                        ticket_type=ticket_type,
                        status=Ticket.Status.UNUSED,
                        raffle_number=raffle_number,
                    )
                    token = generate_ticket_token(ticket)
                    ticket.token_ref = token
                    ticket.save(update_fields=["token_ref"])
                    tickets_with_qr.append(
                        {"ticket": ticket, "qr_data_uri": build_qr_data_uri(ticket.token_ref)}
                    )
            break
        except (OperationalError, IntegrityError):
            if attempt == max_attempts - 1:
                return HttpResponse(
                    "No fue posible procesar la compra por alta concurrencia. Intenta de nuevo.",
                    status=503,
                )
            continue

    return render(
        request,
        "tickets/purchase_success.html",
        {
            "event": event,
            "order": order,
            "ticket_type": ticket_type,
            "quantity": quantity,
            "unit_price_usd": unit_price_usd,
            "total_usd": total_usd,
            "tickets_with_qr": tickets_with_qr,
        },
    )


@login_required
def my_tickets(request):
    selected_event_id_raw = (request.GET.get("event_id") or request.POST.get("selected_event_id") or "").strip()
    selected_event_id = int(selected_event_id_raw) if selected_event_id_raw.isdigit() else None
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        ticket_id_raw = (request.POST.get("ticket_id") or "").strip()
        ticket_id = int(ticket_id_raw) if ticket_id_raw.isdigit() else None
        ticket = None
        if ticket_id:
            ticket = (
                Ticket.objects.select_related("event", "order__user")
                .filter(pk=ticket_id, order__user=request.user)
                .first()
            )

        if not ticket:
            messages.error(request, "No pudimos encontrar la boleta seleccionada.")
            redirect_url = reverse("my_tickets")
            if selected_event_id:
                redirect_url = f"{redirect_url}?event_id={selected_event_id}"
            return redirect(redirect_url)

        if action == "preview":
            transfer_state = _preview_ticket_transfer(
                request.user,
                ticket,
                request.POST.get("target_email", ""),
                selected_event_id,
            )
            return render(
                request,
                "tickets/my_tickets.html",
                _build_my_tickets_context(
                    request.user,
                    selected_event_id=selected_event_id,
                    transfer_state=transfer_state,
                ),
            )

        if action == "confirm":
            ok, message = _confirm_ticket_transfer(
                request.user,
                ticket.pk,
                request.POST.get("transfer_token", ""),
            )
            if ok:
                messages.success(request, message)
            else:
                messages.error(request, message)
            redirect_url = reverse("my_tickets")
            if selected_event_id:
                redirect_url = f"{redirect_url}?event_id={selected_event_id}"
            return redirect(redirect_url)

        messages.error(request, "Accion de transferencia invalida.")
        redirect_url = reverse("my_tickets")
        if selected_event_id:
            redirect_url = f"{redirect_url}?event_id={selected_event_id}"
        return redirect(redirect_url)

    return render(
        request,
        "tickets/my_tickets.html",
        _build_my_tickets_context(request.user, selected_event_id=selected_event_id),
    )


@login_required
def download_ticket_qr_jpg(request, ticket_id):
    ticket = Ticket.objects.select_related("order__user").filter(pk=ticket_id).first()
    if not ticket:
        raise Http404("You do not have access to this ticket.")
    can_access = (
        ticket.order.user_id == request.user.id
        or request.user.is_staff
        or _can_validate_tickets(request.user)
    )
    if not can_access:
        raise Http404("You do not have access to this ticket.")
    if not ticket.token_ref:
        raise Http404("This ticket has no token to generate QR.")

    qr_bytes = build_qr_jpg_bytes(ticket.token_ref)
    filename = f"qr_{ticket.ticket_uuid}.jpg"
    response = HttpResponse(qr_bytes, content_type="image/jpeg")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@user_passes_test(_can_validate_tickets)
def validate_ticket_token(request):
    context = {
        "message": "",
        "result": "",
        "ticket": None,
        "popup_title": "",
        "popup_message": "",
        "validation_info": None,
    }

    def render_validator_popup(message_text, ticket=None, result="error"):
        popup_context = {
            "message": message_text,
            "result": result,
            "ticket": ticket,
            "popup_title": "Informacion de la boleta",
            "popup_message": message_text,
            "validation_info": _ticket_validation_info(ticket) if ticket else None,
        }
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse(
                {
                    "message": popup_context["message"],
                    "result": popup_context["result"],
                    "popup_title": popup_context["popup_title"],
                    "popup_message": popup_context["popup_message"],
                    "validation_info": popup_context["validation_info"],
                }
            )
        return render(request, "admin_tools/validate_token.html", popup_context)

    if request.method == "POST":
        token = request.POST.get("token", "").strip()
        is_valid, payload, message = verify_ticket_token(token)
        if not is_valid:
            ValidationLog.objects.create(
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=message,
            )
            return render_validator_popup(message)

        ticket = Ticket.objects.select_related("event", "order__user").filter(
            ticket_uuid=payload["ticket_uuid"]
        ).first()
        if not ticket:
            detail = "La boleta no existe."
            ValidationLog.objects.create(
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

        if ticket.event_id != payload["event_id"]:
            detail = "El token no coincide con el evento de la boleta."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

        if int(ticket.issued_at.timestamp()) != int(payload["issued_at"]):
            detail = "El token no coincide con la fecha de emision de la boleta."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

        if ticket.status == Ticket.Status.VOID:
            detail = "La boleta fue anulada y no puede validarse."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

        if ticket.status == Ticket.Status.USED:
            detail = "La boleta ya fue utilizada."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

        if ticket.event.has_finished():
            detail = "La boleta vencio porque el evento ya finalizo."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

        if consume_ticket_atomic(ticket.pk):
            ticket.refresh_from_db()
            detail = "Validacion exitosa. La boleta fue marcada como usada."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.ACCEPTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket, result="ok")
        else:
            detail = "No se pudo validar la boleta. Puede haber un uso concurrente."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

    return render(request, "admin_tools/validate_token.html", context)


@login_required
def notifications_list(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    notifications = Notification.objects.filter(user=request.user).order_by("-created_at", "-id")
    return render(request, "accounts/notifications.html", {"notifications": notifications})


@login_required
def delete_notification(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.delete()
    return redirect("notifications_list")


@login_required
def notifications_unread_api(request):
    unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
    latest = (
        Notification.objects.filter(user=request.user)
        .values("id", "title", "body", "is_read", "link_url")
        .order_by("-created_at", "-id")
        .first()
    )
    return JsonResponse(
        {
            "unread_count": unread_count,
            "latest": latest or {},
        }
    )


@login_required
def report_review(request, event_pk, review_pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    review = get_object_or_404(Review, pk=review_pk, event_id=event_pk)
    if review.user_id == request.user.pk:
        messages.error(request, "No puedes reportar tu propio comentario.")
        return redirect(f"{reverse('event_detail', args=[event_pk])}#opiniones")

    report, created = ReviewReport.objects.get_or_create(
        review=review,
        reporter=request.user,
        defaults={"status": ReviewReport.Status.PENDING},
    )
    should_notify_admins = created
    if not created and report.status == ReviewReport.Status.OMITTED:
        report.status = ReviewReport.Status.PENDING
        report.handled_by = None
        report.handled_at = None
        report.save(update_fields=["status", "handled_by", "handled_at"])
        should_notify_admins = True
    if should_notify_admins:
        reporter_name = _display_name_for_user(request.user)
        reports_link = reverse("review_reports_list")
        admins = User.objects.filter(is_staff=True)
        notifications = [
            Notification(
                user=admin,
                title="Nuevo reporte de comentario",
                body=f'{reporter_name} reportó un comentario en el evento "{review.event.title}".',
                link_url=reports_link,
            )
            for admin in admins
        ]
        if notifications:
            Notification.objects.bulk_create(notifications)
    return redirect(f"{reverse('event_detail', args=[event_pk])}?report_sent=1#opiniones")


@login_required
@user_passes_test(lambda user: user.is_staff)
def review_reports_list(request):
    reports = (
        ReviewReport.objects.select_related("review__event", "review__user", "reporter")
        .filter(status=ReviewReport.Status.PENDING)
        .order_by("-created_at", "-id")
    )
    return render(request, "admin_tools/review_reports.html", {"reports": reports})


@login_required
@user_passes_test(lambda user: user.is_staff)
def omit_review_report(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    report = get_object_or_404(ReviewReport, pk=pk, status=ReviewReport.Status.PENDING)
    report.status = ReviewReport.Status.OMITTED
    report.handled_by = request.user
    report.handled_at = timezone.now()
    report.save(update_fields=["status", "handled_by", "handled_at"])
    messages.success(request, "Reporte omitido.")
    return redirect("review_reports_list")


@login_required
@user_passes_test(lambda user: user.is_staff)
def delete_reported_review(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    report = get_object_or_404(ReviewReport, pk=pk, status=ReviewReport.Status.PENDING)
    review = report.review
    review.delete()
    messages.success(request, "Comentario eliminado.")
    return redirect("review_reports_list")


@login_required
@user_passes_test(lambda user: user.is_staff)
def create_event(request):
    form = EventCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        event = form.save(commit=False)
        event.created_by = request.user
        event.save()
        form._save_ticket_types(event)

        files = request.FILES.getlist("images")
        selected_files = files[:MAX_EVENT_IMAGES]
        has_non_recommended_image = any(
            not _is_recommended_image_ratio(image_file) for image_file in selected_files
        )
        for image_file in selected_files:
            EventImage.objects.create(event=event, image=image_file)
        if len(files) > MAX_EVENT_IMAGES:
            messages.warning(
                request,
                f"Solo se guardaron {MAX_EVENT_IMAGES} imagenes, que es el maximo permitido por evento.",
            )
        if has_non_recommended_image:
            messages.warning(
                request,
                "El tamaño recomendado para las imagenes es formato 16:9 (por ejemplo 1600x900 o 1920x1080).",
            )
        if event.status == Event.Status.ACTIVE:
            _notify_new_event_available(event)

        return redirect("event_detail", pk=event.pk)

    return render(request, "admin_tools/create_event.html", {"form": form})


@login_required
@user_passes_test(lambda user: user.is_staff)
def update_event(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    event = get_object_or_404(Event, pk=pk)
    form = EventUpdateForm(request.POST, instance=event)
    if not form.is_valid():
        if any(field in form.errors for field in ["general_ticket_limit", "vip_ticket_limit", "enable_vip"]):
            messages.error(
                request,
                "No puedes reducir el limite por debajo de las entradas ya emitidas.",
            )
        else:
            messages.error(request, "No se pudieron guardar los cambios del evento.")
        return redirect("event_detail", pk=event.pk)

    form.save()

    delete_image_ids_raw = request.POST.getlist("delete_image_ids")
    if delete_image_ids_raw:
        delete_image_ids = [int(value) for value in delete_image_ids_raw if str(value).isdigit()]
        if delete_image_ids:
            images_to_delete = EventImage.objects.filter(event=event, id__in=delete_image_ids)
            deleted_count = 0
            for image in images_to_delete:
                image.image.delete(save=False)
                image.delete()
                deleted_count += 1
            if deleted_count:
                messages.success(request, f"Se eliminaron {deleted_count} imagen(es) marcadas.")

    files = request.FILES.getlist("images")
    if files:
        current_count = event.images.count()
        remaining = max(0, MAX_EVENT_IMAGES - current_count)
        if remaining == 0:
            messages.error(request, f"Este evento ya tiene el maximo de {MAX_EVENT_IMAGES} imagenes.")
        else:
            to_create = files[:remaining]
            has_non_recommended_image = any(
                not _is_recommended_image_ratio(image_file) for image_file in to_create
            )
            for image_file in to_create:
                EventImage.objects.create(event=event, image=image_file)
            if len(files) > remaining:
                messages.warning(
                    request,
                    f"Solo se agregaron {remaining} imagenes por el limite maximo de {MAX_EVENT_IMAGES} por evento.",
                )
            if has_non_recommended_image:
                messages.warning(
                    request,
                    "El tamaño recomendado para las imagenes es formato 16:9 (por ejemplo 1600x900 o 1920x1080).",
                )

    messages.success(request, "Los cambios se guardaron correctamente.")
    return redirect("event_detail", pk=event.pk)


@login_required
def add_event_images(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    event = get_object_or_404(Event, pk=pk)
    if not _can_manage_event_images(request.user, event):
        return HttpResponseForbidden("No tienes permisos para administrar las imagenes de este evento.")

    files = request.FILES.getlist("images")
    if not files:
        messages.error(request, "Selecciona al menos una imagen.")
        return redirect("event_detail", pk=event.pk)

    current_count = event.images.count()
    remaining = max(0, MAX_EVENT_IMAGES - current_count)
    if remaining == 0:
        messages.error(request, f"Este evento ya tiene el maximo de {MAX_EVENT_IMAGES} imagenes.")
        return redirect("event_detail", pk=event.pk)

    to_create = files[:remaining]
    has_non_recommended_image = any(
        not _is_recommended_image_ratio(image_file) for image_file in to_create
    )
    for image_file in to_create:
        EventImage.objects.create(event=event, image=image_file)

    if len(files) > remaining:
        messages.warning(
            request,
            f"Solo se agregaron {remaining} imagenes por el limite maximo de {MAX_EVENT_IMAGES} por evento.",
        )
    else:
        messages.success(request, "Las imagenes del evento se actualizaron correctamente.")
    if has_non_recommended_image:
        messages.warning(
            request,
            "El tamaño recomendado para las imagenes es formato 16:9 (por ejemplo 1600x900 o 1920x1080).",
        )
    return redirect("event_detail", pk=event.pk)


@login_required
def delete_event_image(request, event_pk, image_pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    image = get_object_or_404(EventImage, pk=image_pk, event_id=event_pk)
    if not _can_manage_event_images(request.user, image.event):
        return HttpResponseForbidden("No tienes permisos para administrar las imagenes de este evento.")

    image.image.delete(save=False)
    image.delete()
    messages.success(request, "La imagen fue eliminada del carrusel.")
    return redirect("event_detail", pk=event_pk)


@login_required
@user_passes_test(lambda user: user.is_staff)
def user_list(request):
    search_query = (request.GET.get("q") or "").strip()
    role_filter = (request.GET.get("role") or "").strip()
    account_filter = (request.GET.get("account") or "").strip()
    users = User.objects.order_by("date_joined", "id")
    if search_query:
        users = users.filter(username__icontains=search_query)
    if role_filter == "admin":
        users = users.filter(is_staff=True)
    elif role_filter == "validator":
        users = users.filter(is_staff=False, groups__name__in=_validator_group_names())
    elif role_filter == "user":
        users = users.filter(is_staff=False).exclude(groups__name__in=_validator_group_names())
    if account_filter == "blocked":
        users = users.filter(is_active=False)
    elif account_filter == "active":
        users = users.filter(is_active=True)
    users = users.distinct()
    validator_user_ids = set(
        User.objects.filter(groups__name__in=_validator_group_names()).values_list("id", flat=True)
    )
    return render(
        request,
        "admin_tools/user_list.html",
        {
            "users": users,
            "is_root_admin": _is_root_admin(request.user),
            "validator_user_ids": validator_user_ids,
            "search_query": search_query,
            "role_filter": role_filter,
            "account_filter": account_filter,
        },
    )


@login_required
@user_passes_test(lambda user: user.is_staff)
def create_user(request):
    form = EmailRequiredUserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect(f"{reverse('create_user')}?created=1")
    created = request.GET.get("created") == "1"
    return render(request, "admin_tools/create_user.html", {"form": form, "created": created})


@login_required
@user_passes_test(lambda user: user.is_staff)
def delete_user(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.user.pk == pk:
        return HttpResponseBadRequest("No puedes eliminar tu propia cuenta.")

    user = get_object_or_404(User, pk=pk)
    if (user.is_staff or user.is_superuser) and not _is_root_admin(request.user):
        return HttpResponseBadRequest("Solo la cuenta admin puede eliminar cuentas administradoras.")
    user.delete()
    return redirect("user_list")


@login_required
@user_passes_test(lambda user: user.is_staff)
def promote_user_to_admin(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not _is_root_admin(request.user):
        return HttpResponseBadRequest("Solo la cuenta admin puede asignar permisos de administrador.")

    user = get_object_or_404(User, pk=pk)
    if not user.is_staff or not user.is_superuser:
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])
    return redirect("user_list")


@login_required
@user_passes_test(lambda user: user.is_staff)
def demote_admin_user(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not _is_root_admin(request.user):
        return HttpResponseBadRequest("Solo la cuenta admin puede revocar permisos de administrador.")
    if request.user.pk == pk:
        return HttpResponseBadRequest("No puedes revocar tus propios permisos de administrador.")

    user = get_object_or_404(User, pk=pk)
    user.is_staff = False
    user.is_superuser = False
    user.save(update_fields=["is_staff", "is_superuser"])
    return redirect("user_list")


@login_required
@user_passes_test(lambda user: user.is_staff)
def grant_validator_role(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not _is_root_admin(request.user):
        return HttpResponseBadRequest("Solo la cuenta admin puede asignar el rol validador.")

    user = get_object_or_404(User, pk=pk)
    if user.is_staff:
        return HttpResponseBadRequest("No puedes asignar el rol validador a una cuenta administradora.")
    validator_group = _ensure_validator_group()
    user.groups.add(validator_group)
    return redirect("user_list")


@login_required
@user_passes_test(lambda user: user.is_staff)
def revoke_validator_role(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not _is_root_admin(request.user):
        return HttpResponseBadRequest("Solo la cuenta admin puede quitar el rol validador.")

    user = get_object_or_404(User, pk=pk)
    validator_groups = Group.objects.filter(name__in=_validator_group_names())
    if validator_groups.exists():
        user.groups.remove(*validator_groups)
    return redirect("user_list")


@login_required
@user_passes_test(lambda user: user.is_staff)
def block_user_account(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not _is_root_admin(request.user):
        return HttpResponseBadRequest("Solo la cuenta admin puede bloquear cuentas.")
    if request.user.pk == pk:
        return HttpResponseBadRequest("No puedes bloquear tu propia cuenta.")

    user = get_object_or_404(User, pk=pk)
    if user.username == "admin":
        return HttpResponseBadRequest("No puedes bloquear la cuenta admin.")
    if not user.is_active:
        return redirect("user_list")
    user.is_active = False
    user.save(update_fields=["is_active"])
    return redirect("user_list")


@login_required
@user_passes_test(lambda user: user.is_staff)
def unblock_user_account(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not _is_root_admin(request.user):
        return HttpResponseBadRequest("Solo la cuenta admin puede desbloquear cuentas.")

    user = get_object_or_404(User, pk=pk)
    if user.is_active:
        return redirect("user_list")
    user.is_active = True
    user.save(update_fields=["is_active"])
    return redirect("user_list")


@login_required
@user_passes_test(_can_validate_tickets)
def user_tickets(request):
    event_id = request.GET.get("event_id", "").strip()
    username_query = request.GET.get("username", "").strip()
    tickets = Ticket.objects.select_related("order__user", "event", "ticket_type")
    if event_id.isdigit():
        tickets = tickets.filter(event_id=int(event_id))
    if username_query:
        tickets = tickets.filter(order__user__username__icontains=username_query)
    summary_cards = _build_ticket_type_summary(tickets)
    tickets = tickets.order_by("-issued_at", "-id")
    events = Event.objects.order_by("title")
    return render(
        request,
        "admin_tools/user_tickets.html",
        {
            "tickets": tickets,
            "selected_user": None,
            "events": events,
            "selected_event_id": event_id,
            "username_query": username_query,
            "current_url": request.get_full_path(),
            "summary_cards": summary_cards,
        },
    )


@login_required
@user_passes_test(_can_validate_tickets)
def user_tickets_by_user(request, user_id):
    selected_user = get_object_or_404(User, pk=user_id)
    event_id = request.GET.get("event_id", "").strip()
    username_query = ""
    tickets = Ticket.objects.select_related("order__user", "event", "ticket_type").filter(
        order__user_id=user_id
    )
    if event_id.isdigit():
        tickets = tickets.filter(event_id=int(event_id))
    summary_cards = _build_ticket_type_summary(tickets)
    tickets = tickets.order_by("-issued_at", "-id")
    events = Event.objects.order_by("title")
    return render(
        request,
        "admin_tools/user_tickets.html",
        {
            "tickets": tickets,
            "selected_user": selected_user,
            "events": events,
            "selected_event_id": event_id,
            "username_query": username_query,
            "current_url": request.get_full_path(),
            "summary_cards": summary_cards,
        },
    )


@login_required
@user_passes_test(_can_validate_tickets)
def user_ticket_qrs_by_user(request, user_id):
    selected_user = get_object_or_404(User, pk=user_id)
    tickets = (
        Ticket.objects.select_related("event", "order__user")
        .filter(order__user_id=user_id)
        .order_by("-issued_at", "-id")
    )
    tickets_with_qr = []
    for ticket in tickets:
        qr_data_uri = build_qr_data_uri(ticket.token_ref) if ticket.token_ref else ""
        tickets_with_qr.append(
            {
                "ticket": ticket,
                "qr_data_uri": qr_data_uri,
            }
        )
    next_url = request.GET.get("next", "").strip()
    if next_url and not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = ""

    return render(
        request,
        "admin_tools/user_ticket_qrs.html",
        {
            "selected_user": selected_user,
            "tickets_with_qr": tickets_with_qr,
            "return_url": next_url,
        },
    )


@login_required
def edit_profile(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    form = ProfileForm(request.POST or None, request.FILES or None, instance=profile)
    email_form = UserEmailUpdateForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid() and email_form.is_valid():
        form.save()
        request.user.email = email_form.cleaned_data["email"]
        request.user.save(update_fields=["email"])
        return redirect(f"{reverse('edit_profile')}?updated=1")

    updated = request.GET.get("updated") == "1"
    return render(
        request,
        "accounts/edit_profile.html",
        {"form": form, "email_form": email_form, "profile": profile, "updated": updated},
    )


@login_required
def submit_event_review(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    event = get_object_or_404(Event, pk=pk, status=Event.Status.ACTIVE)
    if not event.has_started():
        return HttpResponseBadRequest("Este evento aun no ha comenzado.")
    form = ReviewForm(request.POST)
    parent_id = request.POST.get("parent_id", "").strip()
    parent_review = None
    if parent_id:
        if not parent_id.isdigit():
            return HttpResponseBadRequest("Comentario padre inválido.")
        parent_review = Review.objects.filter(pk=int(parent_id), event=event).first()
        if not parent_review:
            return HttpResponseBadRequest("Comentario padre inválido.")
    if form.is_valid():
        raw_comment = form.cleaned_data["comment"].strip()
        user_reviews = Review.objects.filter(event=event, user=request.user).order_by("-created_at")
        if contains_blocked_language(raw_comment):
            review_error = (
                "Tu comentario contiene lenguaje no permitido. "
                "Por favor, edita el texto y vuelve a intentarlo."
            )
            replies_qs = Review.objects.select_related("user").order_by("created_at")
            reviews = (
                event.reviews.filter(parent__isnull=True)
                .select_related("user")
                .prefetch_related(Prefetch("replies", queryset=replies_qs))
                .order_by("-updated_at")
            )
            return render(
                request,
                "events/event_detail.html",
                {
                    "event": event,
                    "review_form": form,
                    "reviews": reviews,
                    "reviewed": False,
                    "review_error": review_error,
                },
                status=200,
            )
        if user_reviews.count() >= 5:
            review_error = "Alcanzaste el límite de 5 opiniones para esta obra."
            replies_qs = Review.objects.select_related("user").order_by("created_at")
            reviews = (
                event.reviews.filter(parent__isnull=True)
                .select_related("user")
                .prefetch_related(Prefetch("replies", queryset=replies_qs))
                .order_by("-updated_at")
            )
            return render(
                request,
                "events/event_detail.html",
                {
                    "event": event,
                    "review_form": form,
                    "reviews": reviews,
                    "reviewed": False,
                    "review_error": review_error,
                },
                status=200,
            )

        last_review = user_reviews.first()
        if last_review and last_review.comment.strip().casefold() == raw_comment.casefold():
            review_error = "No publiques el mismo comentario de forma consecutiva."
            replies_qs = Review.objects.select_related("user").order_by("created_at")
            reviews = (
                event.reviews.filter(parent__isnull=True)
                .select_related("user")
                .prefetch_related(Prefetch("replies", queryset=replies_qs))
                .order_by("-updated_at")
            )
            return render(
                request,
                "events/event_detail.html",
                {
                    "event": event,
                    "review_form": form,
                    "reviews": reviews,
                    "reviewed": False,
                    "review_error": review_error,
                },
                status=200,
            )

        created_review = Review.objects.create(
            event=event,
            user=request.user,
            comment=raw_comment,
            parent=parent_review,
        )
        if parent_review and parent_review.user_id != request.user.pk:
            owner = parent_review.user
            title = "Respondieron tu comentario"
            body = f'Recibiste una respuesta en el evento "{event.title}".'
            link = f"{reverse('event_detail', args=[event.pk])}#review-{created_review.pk}"
            _create_notification_if_missing(owner, title, body, link)
        return redirect(f"{reverse('event_detail', args=[event.pk])}?reviewed=1#opiniones")

    replies_qs = Review.objects.select_related("user").order_by("created_at")
    reviews = (
        event.reviews.filter(parent__isnull=True)
        .select_related("user")
        .prefetch_related(Prefetch("replies", queryset=replies_qs))
        .order_by("-updated_at")
    )
    return render(
        request,
        "events/event_detail.html",
        {
            "event": event,
            "review_form": form,
            "reviews": reviews,
            "reviewed": False,
            "review_error": "",
        },
        status=400,
    )


@login_required
def delete_event_review(request, event_pk, review_pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    review = get_object_or_404(Review, pk=review_pk, event_id=event_pk)
    if not (request.user.is_staff or request.user.pk == review.user_id):
        return HttpResponseForbidden("You do not have permission to delete this comment.")
    review.delete()
    return redirect(f"{reverse('event_detail', args=[event_pk])}#opiniones")


@login_required
def react_to_review(request, event_pk, review_pk, reaction):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    if reaction not in {ReviewReaction.Reaction.LIKE, ReviewReaction.Reaction.DISLIKE}:
        return HttpResponseBadRequest("Reacción inválida.")

    review = get_object_or_404(Review, pk=review_pk, event_id=event_pk)
    reaction_obj = ReviewReaction.objects.filter(review=review, user=request.user).first()
    if reaction_obj:
        if reaction_obj.reaction == reaction:
            reaction_obj.delete()
        else:
            reaction_obj.reaction = reaction
            reaction_obj.save(update_fields=["reaction"])
    else:
        ReviewReaction.objects.create(review=review, user=request.user, reaction=reaction)

    return redirect(f"{reverse('event_detail', args=[event_pk])}#opiniones")




