from decimal import Decimal
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse


from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.models import Group, Permission, User
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.core.files.images import get_image_dimensions
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.template.loader import render_to_string
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
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import DetailView, ListView, TemplateView

from .forms import (
    EmailOrUsernameAuthenticationForm,
    EmailVerificationResendForm,
    EmailRequiredUserCreationForm,
    EventCreateForm,
    MomentBlockForm,
    RulesContentForm,
    SiteSettingsForm,
    EventUpdateForm,
    ProfileForm,
    ReviewForm,
    UserEmailUpdateForm,
)
from .models import (
    Cart,
    CartItem,
    Event,
    EventImage,
    EventTicketType,
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
from .moderation import contains_blocked_language
from .services import (
    build_qr_data_uri,
    build_qr_jpg_bytes,
    consume_ticket_atomic,
    generate_ticket_token,
    verify_ticket_token,
)
from .stripe_service import (
    StripeConfigurationError,
    StripeWebhookError,
    construct_webhook_event,
    create_checkout_session,
)

MAX_EVENT_IMAGES = 3
MAX_MOMENT_MEDIA = 6
RECOMMENDED_IMAGE_RATIO = 16 / 9
RECOMMENDED_IMAGE_RATIO_TOLERANCE = 0.05
RULES_FILENAME = "JDL_Trocas_Official_Rules.md"
EMAIL_VERIFICATION_SALT = "core.email_verification"
EMAIL_VERIFICATION_MAX_AGE = 60 * 60 * 24
EMAIL_VERIFICATION_RESEND_LIMIT = 3
EMAIL_VERIFICATION_RESEND_WINDOW = 60 * 60


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


def _site_settings():
    return SiteSettings.get_solo()


def robots_txt(request):
    sitemap_url = request.build_absolute_uri(reverse("sitemap_xml"))
    content = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "Disallow: /admin/",
            "Disallow: /staff/",
            "Disallow: /cart/",
            "Disallow: /my-tickets/",
            "Disallow: /accounts/",
            f"Sitemap: {sitemap_url}",
            "",
        ]
    )
    return HttpResponse(content, content_type="text/plain")


def sitemap_xml(request):
    public_paths = [
        reverse("home"),
        reverse("about_page"),
        reverse("rules_page"),
        reverse("event_list"),
        reverse("moments_page"),
    ]
    urls = [
        {
            "loc": request.build_absolute_uri(path),
            "priority": "0.8" if path == reverse("home") else "0.6",
        }
        for path in public_paths
    ]
    for event in Event.objects.filter(status=Event.Status.ACTIVE).order_by("-datetime", "-id"):
        urls.append(
            {
                "loc": request.build_absolute_uri(reverse("event_detail", args=[event.pk])),
                "priority": "0.7",
            }
        )

    rows = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url in urls:
        rows.extend(
            [
                "  <url>",
                f"    <loc>{escape(url['loc'])}</loc>",
                f"    <priority>{url['priority']}</priority>",
                "  </url>",
            ]
        )
    rows.append("</urlset>")
    return HttpResponse("\n".join(rows), content_type="application/xml")


def _youtube_embed_url(raw_url):
    url = (raw_url or "").strip()
    if not url:
        return ""

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").strip("/")
    video_id = ""

    if "youtu.be" in host:
        video_id = path.split("/")[0]
    elif "youtube.com" in host:
        if path == "watch":
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        elif path.startswith("embed/"):
            video_id = path.split("/", 1)[1]
        elif path.startswith("shorts/"):
            video_id = path.split("/", 1)[1]

    video_id = (video_id or "").split("/")[0].split("?")[0].split("&")[0].strip()
    if not video_id:
        return ""
    return f"https://www.youtube.com/embed/{video_id}?rel=0"


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


def _user_email_is_verified(user):
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    try:
        return bool(user.profile.email_verified)
    except Exception:
        return True


def _event_purchase_block_reason(event):
    if event.end_datetime is None:
        return "Este evento no tiene una fecha de finalizacion definida y no admite compras todavia."
    if event.has_finished():
        return "Este evento ya finalizo."
    return ""


def _build_email_verification_token(user):
    return signing.dumps(
        {"user_id": user.pk, "email": (user.email or "").strip().lower()},
        salt=EMAIL_VERIFICATION_SALT,
    )


def _get_email_verification_user(token):
    try:
        payload = signing.loads(
            token,
            salt=EMAIL_VERIFICATION_SALT,
            max_age=EMAIL_VERIFICATION_MAX_AGE,
        )
    except signing.SignatureExpired:
        return None, "expired"
    except signing.BadSignature:
        return None, "invalid"

    user_id = payload.get("user_id")
    email = (payload.get("email") or "").strip().lower()
    if not user_id or not email:
        return None, "invalid"

    user = User.objects.filter(pk=user_id, email__iexact=email).first()
    if user is None:
        return None, "invalid"
    return user, ""


def _verification_email_context(request, user):
    token = _build_email_verification_token(user)
    confirmation_url = request.build_absolute_uri(
        f"{reverse('verify_email_confirm')}?token={token}"
    )
    return {
        "user": user,
        "display_name": _display_name_for_user(user),
        "domain": request.get_host(),
        "protocol": "https" if request.is_secure() else "http",
        "confirmation_url": confirmation_url,
    }


def _send_email_verification_message(request, user):
    context = _verification_email_context(request, user)
    subject = render_to_string("registration/email_verification_subject.txt", context).strip()
    body_text = render_to_string("registration/email_verification_email.txt", context)
    body_html = render_to_string("registration/email_verification_email.html", context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=body_text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    message.attach_alternative(body_html, "text/html")
    message.send()


def _email_verification_resend_cache_key(email):
    return f"email-verification-resends:{(email or '').strip().lower()}"


def _can_resend_email_verification(email):
    key = _email_verification_resend_cache_key(email)
    attempts = cache.get(key, 0)
    return attempts < EMAIL_VERIFICATION_RESEND_LIMIT


def _register_email_verification_resend(email):
    key = _email_verification_resend_cache_key(email)
    attempts = cache.get(key, 0) + 1
    cache.set(key, attempts, timeout=EMAIL_VERIFICATION_RESEND_WINDOW)
    return attempts


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
    user_redemptions = ProductRedemption.objects.select_related("event", "order").filter(user=user)

    event_cards_qs = (
        Event.objects.filter(Q(tickets__order__user=user) | Q(product_redemptions__user=user))
        .prefetch_related("images")
        .annotate(
            participation_count=Count(
                "tickets",
                filter=Q(tickets__order__user=user),
                distinct=True,
            ),
            product_purchase_count=Count(
                "product_redemptions",
                filter=Q(product_redemptions__user=user),
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
                "product_purchase_count": event.product_purchase_count,
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
        user_redemptions = user_redemptions.filter(event_id=selected_event_id)
    else:
        user_tickets = user_tickets.none()
        user_redemptions = user_redemptions.none()

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

    product_redemptions = []
    for redemption in user_redemptions.order_by("-created_at", "-id"):
        product_items = list(
            redemption.order.items.filter(item_type=OrderItem.ItemType.PRODUCT)
            .select_related("product_variant__product")
            .order_by("created_at", "id")
        )
        product_redemptions.append(
            {
                "redemption": redemption,
                "product_items": product_items,
            }
        )

    return {
        "tickets_with_qr": tickets_with_qr,
        "product_redemptions": product_redemptions,
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


def _active_product_cards(event):
    products = (
        event.products.filter(is_active=True)
        .prefetch_related("variants")
        .order_by("name", "id")
    )
    cards = []
    for product in products:
        active_variants = [variant for variant in product.variants.all() if variant.is_active]
        if product.has_variants:
            variants = []
            for variant in active_variants:
                variants.append(
                    {
                        "variant": variant,
                        "remaining_stock": variant.remaining_stock,
                        "is_available": variant.remaining_stock > 0,
                    }
                )
            if not any(row["is_available"] for row in variants):
                continue
            default_variant = next((row for row in variants if row["is_available"]), variants[0] if variants else None)
            cards.append(
                {
                    "product": product,
                    "variants": variants,
                    "default_variant": default_variant,
                    "has_variants": True,
                }
            )
            continue

        unit_variant = next((variant for variant in active_variants if variant.name == "Unidad"), None)
        if unit_variant is None or unit_variant.remaining_stock <= 0:
            continue
        cards.append(
            {
                "product": product,
                "variants": [
                    {
                        "variant": unit_variant,
                        "remaining_stock": unit_variant.remaining_stock,
                        "is_available": True,
                    }
                ],
                "default_variant": {
                    "variant": unit_variant,
                    "remaining_stock": unit_variant.remaining_stock,
                    "is_available": True,
                },
                "has_variants": False,
            }
        )
    return cards


def _build_ticket_type_summary(tickets_queryset):
    rows = list(
        tickets_queryset.values("ticket_type_id", "ticket_type__code", "ticket_type__name")
        .annotate(sold_count=Count("id"))
        .order_by("ticket_type__code")
    )
    order_ids = list(tickets_queryset.values_list("order_id", flat=True).distinct())
    revenue_by_type = {
        row["ticket_type_id"]: row["revenue_total"]
        for row in OrderItem.objects.filter(
            order_id__in=order_ids,
            item_type=OrderItem.ItemType.TICKET,
            ticket_type_id__in=[row["ticket_type_id"] for row in rows if row["ticket_type_id"]],
        )
        .values("ticket_type_id")
        .annotate(revenue_total=Coalesce(Sum("total_usd"), Decimal("0.00")))
    }
    summary = []
    for row in rows:
        code = row["ticket_type__code"] or EventTicketType.Code.GENERAL
        name = row["ticket_type__name"] or "General"
        summary.append(
            {
                "code": code,
                "name": name,
                "sold_count": row["sold_count"] or 0,
                "revenue_total": revenue_by_type.get(row["ticket_type_id"]) or Decimal("0.00"),
                "is_vip": code == EventTicketType.Code.VIP,
            }
        )
    return summary


def _ticket_type_label(ticket):
    if getattr(ticket, "ticket_type_id", None) and ticket.ticket_type:
        return ticket.ticket_type.name
    return "General"


def _active_cart_for_user(user):
    cart = Cart.objects.filter(user=user, status=Cart.Status.ACTIVE).first()
    if cart:
        return cart
    return Cart.objects.create(user=user, status=Cart.Status.ACTIVE)


def _cart_return_target(request, fallback_name, **fallback_kwargs):
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return reverse(fallback_name, kwargs=fallback_kwargs)


def _sync_cart_event(cart):
    first_item = cart.items.select_related("ticket_type__event", "product_variant__product__event").first()
    target_event = first_item.event if first_item else None
    target_event_id = target_event.id if target_event else None
    if cart.event_id != target_event_id:
        cart.event = target_event
        cart.save(update_fields=["event", "updated_at"])
    return cart


def _ensure_cart_event(cart, event):
    first_item = cart.items.select_related("ticket_type__event", "product_variant__product__event").first()
    target_event_id = event.id if event else None
    if first_item is None:
        if cart.event_id != target_event_id:
            cart.event = event
            cart.save(update_fields=["event", "updated_at"])
        return True

    current_event = first_item.event
    current_event_id = current_event.id if current_event else None
    if current_event_id != target_event_id:
        return False
    if cart.event_id != target_event_id:
        cart.event = event
        cart.save(update_fields=["event", "updated_at"])
    return True


def _build_checkout_snapshot(items, event):
    total_quantity = 0
    total_usd = Decimal("0.00")
    ticket_lines = []
    product_lines = []

    for item in items:
        item_event_id = item.event.id if item.event else None
        order_event_id = event.id if event else None
        if item_event_id != order_event_id:
            return {"error": "Todos los items del carrito deben pertenecer al mismo contexto de venta."}

        if item.item_type == CartItem.ItemType.PRODUCT:
            product_variant = ProductVariant.objects.select_for_update().select_related("product").get(
                pk=item.product_variant_id
            )
            if not product_variant.is_active or not product_variant.product.is_active:
                return {"error": f'El producto "{product_variant.product.name}" ya no esta disponible.'}
            if item.quantity > product_variant.remaining_stock:
                return {
                    "error": f'No hay suficiente stock para "{product_variant.product.name} - {product_variant.name}".'
                }
            unit_price = product_variant.product.price_usd
            line_total = (unit_price * Decimal(item.quantity)).quantize(Decimal("0.01"))
            product_lines.append((item, product_variant, unit_price, line_total))
        else:
            ticket_type = EventTicketType.objects.select_for_update().get(pk=item.ticket_type_id)
            if not ticket_type.is_active:
                return {"error": f'La boleta "{ticket_type.name}" ya no esta disponible.'}
            if item.quantity > ticket_type.remaining_tickets_count:
                return {"error": f'No hay suficientes boletas "{ticket_type.name}" disponibles.'}
            unit_price = ticket_type.price_usd
            line_total = (unit_price * Decimal(item.quantity)).quantize(Decimal("0.01"))
            ticket_lines.append((item, ticket_type, unit_price, line_total))

        total_quantity += item.quantity
        total_usd += line_total

    if total_quantity <= 0:
        return {"error": "Tu carrito no tiene items validos."}

    order_ticket_type = ticket_lines[0][1] if len(ticket_lines) == 1 else None
    if len(ticket_lines) == 1:
        order_unit_price = ticket_lines[0][2]
    else:
        order_unit_price = (total_usd / Decimal(total_quantity)).quantize(Decimal("0.01"))

    return {
        "event": event,
        "total_quantity": total_quantity,
        "total_usd": total_usd.quantize(Decimal("0.01")),
        "ticket_lines": ticket_lines,
        "product_lines": product_lines,
        "order_ticket_type": order_ticket_type,
        "order_unit_price": order_unit_price,
    }


def _create_order_from_snapshot(user, snapshot, status, payment_provider="", payment_status_detail=""):
    return Order.objects.create(
        user=user,
        event=snapshot["event"],
        ticket_type=snapshot["order_ticket_type"],
        unit_price_usd=snapshot["order_unit_price"],
        quantity=snapshot["total_quantity"],
        total_usd=snapshot["total_usd"],
        status=status,
        payment_provider=payment_provider,
        payment_status_detail=payment_status_detail,
    )


def _get_or_create_pending_order_from_snapshot(user, snapshot):
    pending_order = (
        Order.objects.select_for_update()
        .filter(
            user=user,
            event=snapshot["event"],
            status=Order.Status.PENDING,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if pending_order is None:
        return _create_order_from_snapshot(
            user=user,
            snapshot=snapshot,
            status=Order.Status.PENDING,
            payment_status_detail="pending_gateway_checkout",
        )

    pending_order.ticket_type = snapshot["order_ticket_type"]
    pending_order.unit_price_usd = snapshot["order_unit_price"]
    pending_order.quantity = snapshot["total_quantity"]
    pending_order.total_usd = snapshot["total_usd"]
    pending_order.payment_provider = ""
    pending_order.payment_status_detail = "pending_gateway_checkout"
    pending_order.stripe_checkout_session_id = ""
    pending_order.stripe_payment_intent_id = ""
    pending_order.payment_confirmed_at = None
    pending_order.save(
        update_fields=[
            "ticket_type",
            "unit_price_usd",
            "quantity",
            "total_usd",
            "payment_provider",
            "payment_status_detail",
            "stripe_checkout_session_id",
            "stripe_payment_intent_id",
            "payment_confirmed_at",
        ]
    )
    pending_order.items.all().delete()
    return pending_order


def _create_order_items_from_snapshot(order, snapshot):
    event = snapshot["event"]
    for item, ticket_type, unit_price, line_total in snapshot["ticket_lines"]:
        OrderItem.objects.create(
            order=order,
            event=event,
            item_type=OrderItem.ItemType.TICKET,
            ticket_type=ticket_type,
            item_name=f"{event.title} - {ticket_type.name}",
            unit_price_usd=unit_price,
            quantity=item.quantity,
            total_usd=line_total,
        )

    for item, product_variant, unit_price, line_total in snapshot["product_lines"]:
        OrderItem.objects.create(
            order=order,
            event=event,
            item_type=OrderItem.ItemType.PRODUCT,
            product_variant=product_variant,
            item_name=product_variant.product.name,
            variant_name="" if product_variant.name == "Unidad" else product_variant.name,
            unit_price_usd=unit_price,
            quantity=item.quantity,
            total_usd=line_total,
        )


class OrderFulfillmentError(RuntimeError):
    pass


def _build_fulfillment_snapshot_from_order(order):
    ticket_type_ids = list(
        order.items.filter(item_type=OrderItem.ItemType.TICKET).values_list("ticket_type_id", flat=True)
    )
    product_variant_ids = list(
        order.items.filter(item_type=OrderItem.ItemType.PRODUCT).values_list("product_variant_id", flat=True)
    )
    locked_ticket_types = {
        ticket_type.pk: ticket_type
        for ticket_type in EventTicketType.objects.select_for_update().filter(pk__in=ticket_type_ids)
    }
    locked_product_variants = {
        product_variant.pk: product_variant
        for product_variant in ProductVariant.objects.select_for_update()
        .select_related("product")
        .filter(pk__in=product_variant_ids)
    }

    ticket_lines = []
    product_lines = []
    for order_item in order.items.select_related("ticket_type", "product_variant__product").order_by(
        "created_at",
        "id",
    ):
        if order_item.item_type == OrderItem.ItemType.TICKET:
            ticket_type = locked_ticket_types.get(order_item.ticket_type_id)
            if ticket_type is None:
                raise OrderFulfillmentError("La orden tiene una boleta sin tipo disponible para entrega.")
            ticket_lines.append(
                (order_item, ticket_type, order_item.unit_price_usd, order_item.total_usd)
            )
            continue

        if order_item.item_type == OrderItem.ItemType.PRODUCT:
            product_variant = locked_product_variants.get(order_item.product_variant_id)
            if product_variant is None:
                raise OrderFulfillmentError("La orden tiene un producto sin variante disponible para entrega.")
            product_lines.append(
                (order_item, product_variant, order_item.unit_price_usd, order_item.total_usd)
            )

    return {
        "event": order.event,
        "ticket_lines": ticket_lines,
        "product_lines": product_lines,
    }


def _convert_active_cart_after_payment(order):
    active_cart = (
        Cart.objects.select_for_update()
        .filter(
            user=order.user,
            event=order.event,
            status=Cart.Status.ACTIVE,
        )
        .order_by("-updated_at", "-id")
        .first()
    )
    if active_cart is None:
        return
    active_cart.status = Cart.Status.CONVERTED
    active_cart.save(update_fields=["status", "updated_at"])


def _fulfill_paid_order(order, snapshot):
    tickets_with_qr = []
    event = snapshot["event"]

    for item, ticket_type, unit_price, line_total in snapshot["ticket_lines"]:
        used_numbers = set(
            Ticket.objects.filter(ticket_type=ticket_type, raffle_number__isnull=False).values_list(
                "raffle_number",
                flat=True,
            )
        )
        available_numbers = [
            number
            for number in range(1, ticket_type.stock_total + 1)
            if number not in used_numbers
        ]
        if len(available_numbers) < item.quantity:
            raise OrderFulfillmentError(
                f"No hay stock suficiente para la boleta {ticket_type.name}."
            )
        assigned_numbers = available_numbers[: item.quantity]
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

    product_redemption = None
    if snapshot["product_lines"]:
        for item, product_variant, unit_price, line_total in snapshot["product_lines"]:
            if product_variant.remaining_stock < item.quantity:
                raise OrderFulfillmentError(
                    f"No hay stock suficiente para la variante {product_variant.name}."
                )
        product_redemption = ProductRedemption.objects.create(
            order=order,
            user=order.user,
            event=event,
        )

    return {
        "tickets_with_qr": tickets_with_qr,
        "product_redemption": product_redemption,
    }


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

        original_order = Order.objects.select_for_update().get(pk=ticket.order_id)
        order_items = list(original_order.items.select_for_update().order_by("created_at", "id"))

        ticket_line = next(
            (
                item
                for item in order_items
                if item.item_type == OrderItem.ItemType.TICKET and item.ticket_type_id == ticket.ticket_type_id
            ),
            None,
        )
        transfer_unit_price = (
            ticket_line.unit_price_usd if ticket_line else original_order.unit_price_usd
        )
        recipient_order = Order.objects.create(
            user=recipient,
            event=ticket.event,
            ticket_type=original_order.ticket_type or ticket.ticket_type,
            unit_price_usd=transfer_unit_price,
            quantity=1,
            total_usd=transfer_unit_price,
            status=Order.Status.PAID,
        )
        OrderItem.objects.create(
            order=recipient_order,
            event=ticket.event,
            item_type=OrderItem.ItemType.TICKET,
            ticket_type=ticket.ticket_type,
            item_name=f"{ticket.event.title} - {_ticket_type_label(ticket)}",
            unit_price_usd=transfer_unit_price,
            quantity=1,
            total_usd=transfer_unit_price,
        )
        ticket.order = recipient_order
        ticket.save(update_fields=["order"])

        if ticket_line:
            if ticket_line.quantity <= 1:
                ticket_line.delete()
            else:
                ticket_line.quantity -= 1
                ticket_line.total_usd = (
                    ticket_line.unit_price_usd * Decimal(ticket_line.quantity)
                ).quantize(Decimal("0.01"))
                ticket_line.save(update_fields=["quantity", "total_usd"])

        remaining_items = list(original_order.items.order_by("created_at", "id"))
        if remaining_items:
            original_order.quantity = sum(item.quantity for item in remaining_items)
            original_order.total_usd = sum(
                (item.total_usd for item in remaining_items),
                Decimal("0.00"),
            ).quantize(Decimal("0.01"))
            if len(remaining_items) == 1:
                original_order.unit_price_usd = remaining_items[0].unit_price_usd
            else:
                original_order.unit_price_usd = (
                    original_order.total_usd / Decimal(original_order.quantity)
                ).quantize(Decimal("0.01"))
            remaining_ticket_items = [
                item for item in remaining_items if item.item_type == OrderItem.ItemType.TICKET
            ]
            if len(remaining_ticket_items) == 1 and len(remaining_items) == 1:
                original_order.ticket_type = remaining_ticket_items[0].ticket_type
            else:
                original_order.ticket_type = None
            original_order.save(update_fields=["quantity", "total_usd", "unit_price_usd", "ticket_type"])
        else:
            remaining_ticket_count = original_order.tickets.count()
            if remaining_ticket_count == 0:
                original_order.delete()
            else:
                original_order.quantity = remaining_ticket_count
                original_order.total_usd = (
                    original_order.unit_price_usd * Decimal(remaining_ticket_count)
                ).quantize(Decimal("0.01"))
                original_order.ticket_type = ticket.ticket_type if remaining_ticket_count == 1 else None
                original_order.save(update_fields=["quantity", "total_usd", "ticket_type"])

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


def _delete_event_assets(event):
    if event.buyer_image:
        event.buyer_image.delete(save=False)
    for image in event.images.all():
        if image.image:
            image.image.delete(save=False)
    for product in event.products.all():
        if product.image:
            product.image.delete(save=False)


def _detect_moment_media_type(uploaded_file):
    content_type = (getattr(uploaded_file, "content_type", "") or "").lower()
    if content_type.startswith("video/"):
        return MomentMedia.MediaType.VIDEO
    return MomentMedia.MediaType.IMAGE


def _parse_focus_percent(raw_value, default=50.0):
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default
    return min(100.0, max(0.0, value))


def _delete_moment_media_files(media_items):
    for media in media_items:
        if media.file:
            media.file.delete(save=False)


def _delete_event_related_records(event):
    # Clear dependents with protected references before deleting the event itself.
    event.carts.all().delete()
    event.orders.all().delete()


def _active_and_not_finished_filter():
    now = timezone.now()
    return (
        Q(status=Event.Status.ACTIVE)
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
        context["home_latest_events"] = (
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
            .order_by("-created_at", "-id")[:2]
        )
        context["home_video_embed_url"] = _youtube_embed_url(_site_settings().home_video_url)
        context["home_display_name"] = display_name
        context["home_role"] = home_role
        context["home_photo_url"] = home_photo_url
        return context


class CustomerLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = EmailOrUsernameAuthenticationForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pending_verification_email"] = self.request.session.get("pending_verification_email", "")
        return context


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


class MomentsView(ListView):
    model = MomentBlock
    template_name = "events/moments.html"
    context_object_name = "moment_blocks"

    def get_queryset(self):
        return MomentBlock.objects.filter(is_active=True).prefetch_related("media_items").order_by("-created_at", "-id")


@login_required
@user_passes_test(lambda user: user.is_staff)
def manage_moments(request):
    form = MomentBlockForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        media_files = request.FILES.getlist("media_files")
        if not media_files:
            form.add_error(None, "Debes cargar al menos una foto o video para este bloque.")
        elif len(media_files) > MAX_MOMENT_MEDIA:
            form.add_error(
                None,
                f"Solo puedes cargar hasta {MAX_MOMENT_MEDIA} archivos por bloque, contando imagenes y videos.",
            )
        else:
            block = form.save(commit=False)
            block.is_active = True
            block.save()
            has_non_recommended_image = False
            for index, media_file in enumerate(media_files, start=1):
                media_type = _detect_moment_media_type(media_file)
                if media_type == MomentMedia.MediaType.IMAGE and not _is_recommended_image_ratio(media_file):
                    has_non_recommended_image = True
                MomentMedia.objects.create(
                    block=block,
                    media_type=media_type,
                    file=media_file,
                    display_order=index,
                    focal_point_x=_parse_focus_percent(request.POST.get(f"focal_point_x_new_{index}")),
                    focal_point_y=_parse_focus_percent(request.POST.get(f"focal_point_y_new_{index}")),
                )
            messages.success(request, f'Se agrego el bloque "{block.title}" a Momentos.')
            if has_non_recommended_image:
                messages.warning(
                    request,
                    "Algunas imagenes no usan formato recomendado 16:9. Puedes ajustar el encuadre al editar el bloque.",
                )
            return redirect("manage_moments")

    blocks = MomentBlock.objects.prefetch_related("media_items").order_by("-created_at", "-id")
    return render(
        request,
        "admin_tools/manage_moments.html",
        {"form": form, "moment_blocks": blocks},
    )


@login_required
@user_passes_test(lambda user: user.is_staff)
def manage_site_settings(request):
    site_settings = _site_settings()
    form = SiteSettingsForm(request.POST or None, instance=site_settings)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Configuración del sitio actualizada.")
        return redirect("manage_site_settings")

    return render(
        request,
        "admin_tools/manage_site_settings.html",
        {"form": form},
    )


@login_required
@user_passes_test(lambda user: user.is_staff)
def update_moment_block(request, pk):
    block = get_object_or_404(MomentBlock.objects.prefetch_related("media_items"), pk=pk)
    form = MomentBlockForm(request.POST or None, instance=block)
    if request.method == "POST" and form.is_valid():
        block = form.save(commit=False)
        block.is_active = True
        delete_media_ids = [
            int(value)
            for value in request.POST.getlist("delete_media_ids")
            if str(value).isdigit()
        ]
        new_media_files = request.FILES.getlist("media_files")
        remaining_count = block.media_items.exclude(id__in=delete_media_ids).count()
        final_count = remaining_count + len(new_media_files)

        if final_count <= 0:
            form.add_error(None, "El bloque debe conservar al menos una foto o video.")
        elif final_count > MAX_MOMENT_MEDIA:
            form.add_error(
                None,
                f"Solo puedes guardar hasta {MAX_MOMENT_MEDIA} archivos por bloque, contando imagenes y videos.",
            )
        else:
            block.save()

            if delete_media_ids:
                media_to_delete = list(block.media_items.filter(id__in=delete_media_ids))
                _delete_moment_media_files(media_to_delete)
                block.media_items.filter(id__in=delete_media_ids).delete()

            remaining_media = list(block.media_items.exclude(id__in=delete_media_ids))
            for fallback_index, media in enumerate(remaining_media, start=1):
                order_raw = (request.POST.get(f"media_order_{media.pk}") or "").strip()
                media.display_order = int(order_raw) if order_raw.isdigit() and int(order_raw) > 0 else fallback_index
                if media.media_type == MomentMedia.MediaType.IMAGE:
                    media.focal_point_x = _parse_focus_percent(request.POST.get(f"focal_point_x_{media.pk}"))
                    media.focal_point_y = _parse_focus_percent(request.POST.get(f"focal_point_y_{media.pk}"))
                    media.save(update_fields=["display_order", "focal_point_x", "focal_point_y"])
                else:
                    media.save(update_fields=["display_order"])

            existing_count = block.media_items.count()
            has_non_recommended_image = False
            for offset, media_file in enumerate(new_media_files, start=1):
                media_type = _detect_moment_media_type(media_file)
                if media_type == MomentMedia.MediaType.IMAGE and not _is_recommended_image_ratio(media_file):
                    has_non_recommended_image = True
                MomentMedia.objects.create(
                    block=block,
                    media_type=media_type,
                    file=media_file,
                    display_order=existing_count + offset,
                    focal_point_x=_parse_focus_percent(request.POST.get(f"focal_point_x_new_{offset}")),
                    focal_point_y=_parse_focus_percent(request.POST.get(f"focal_point_y_new_{offset}")),
                )

            messages.success(request, f'Se actualizo el bloque "{block.title}".')
            if has_non_recommended_image:
                messages.warning(
                    request,
                    "Algunas imagenes nuevas no usan formato recomendado 16:9. Ajusta el encuadre si lo necesitas.",
                )
            return redirect("manage_moments")

    return render(
        request,
        "admin_tools/update_moment_block.html",
        {"form": form, "block": block, "media_items": block.media_items.all()},
    )


@login_required
@user_passes_test(lambda user: user.is_staff)
def delete_moment_block(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    block = get_object_or_404(MomentBlock.objects.prefetch_related("media_items"), pk=pk)
    block_title = block.title
    _delete_moment_media_files(list(block.media_items.all()))
    block.delete()
    messages.success(request, f'El bloque "{block_title}" fue eliminado de Momentos.')
    return redirect("manage_moments")


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
        context["product_cards"] = _active_product_cards(self.object)
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
        return context


def signup(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        form = EmailRequiredUserCreationForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save()
                    profile, _ = Profile.objects.get_or_create(user=user)
                    profile.email_verified = False
                    profile.email_verified_at = None
                    profile.save(update_fields=["email_verified", "email_verified_at"])
                    _send_email_verification_message(request, user)
            except Exception:
                form.add_error(
                    None,
                    "No pudimos enviar el correo de verificacion en este momento. Intenta nuevamente.",
                )
            else:
                request.session["pending_verification_email"] = user.email
                messages.warning(
                    request,
                    f"Enviamos el enlace de verificacion a {user.email}. Revisa tu bandeja de entrada y tambien la carpeta SPAM. Debes confirmar tu correo antes de iniciar sesion.",
                    extra_tags="email-verification-pending",
                )
                return redirect("home")
    else:
        form = EmailRequiredUserCreationForm()

    return render(request, "registration/signup.html", {"form": form})


def email_verification_sent(request):
    pending_email = request.session.get("pending_verification_email", "")
    return render(
        request,
        "registration/email_verification_sent.html",
        {"pending_email": pending_email},
    )


def verify_email_confirm(request):
    token = (request.GET.get("token") or "").strip()
    user, error = _get_email_verification_user(token)
    if user is None:
        messages.error(
            request,
            "El enlace de verificacion no es valido o ya expiro. Solicita uno nuevo.",
        )
        return redirect("resend_email_verification")

    profile, _ = Profile.objects.get_or_create(user=user)
    if not profile.email_verified:
        profile.email_verified = True
        profile.email_verified_at = timezone.now()
        profile.save(update_fields=["email_verified", "email_verified_at"])

    if request.session.get("pending_verification_email", "").strip().lower() == (user.email or "").strip().lower():
        request.session.pop("pending_verification_email", None)

    messages.success(
        request,
        "Tu correo fue confirmado correctamente. Ya puedes iniciar sesion.",
        extra_tags="email-verification-success",
    )
    return redirect("login")


def resend_email_verification(request):
    initial_email = request.session.get("pending_verification_email", "")
    form = EmailVerificationResendForm(request.POST or None, initial={"email": initial_email})
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        user = User.objects.filter(email__iexact=email).first()
        if user and _user_email_is_verified(user):
            messages.success(
                request,
                "Si la cuenta ya estaba verificada, no necesitas un nuevo enlace. Ya puedes iniciar sesion.",
            )
            return redirect("login")

        if user and not _can_resend_email_verification(email):
            form.add_error(
                "email",
                "Ya alcanzaste el limite de 3 reenvios por hora. Intenta nuevamente mas tarde.",
            )
        else:
            if user:
                _send_email_verification_message(request, user)
                _register_email_verification_resend(email)
                request.session["pending_verification_email"] = user.email
            messages.success(
                request,
                "Si encontramos una cuenta pendiente con ese correo, enviamos un nuevo enlace de verificacion.",
            )
            return redirect("email_verification_sent")

    return render(
        request,
        "registration/resend_email_verification.html",
        {"form": form},
    )


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
    return HttpResponseBadRequest("La compra directa ya no esta disponible. Usa el carrito.")


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
            detail = "No pudimos validar esta entrada. Verifica el QR o solicita otra imagen."
            ValidationLog.objects.create(
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

        if ticket.event_id != payload["event_id"]:
            detail = "El codigo no coincide con una entrada valida del sistema."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

        if int(ticket.issued_at.timestamp()) != int(payload["issued_at"]):
            detail = "El codigo no coincide con una entrada valida del sistema."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

        if ticket.status == Ticket.Status.VOID:
            detail = "Esta entrada fue anulada y no debe permitir ingreso."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

        if ticket.status == Ticket.Status.USED:
            detail = "Esta entrada ya fue validada antes."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

        if ticket.event.has_finished():
            detail = "Esta entrada ya no puede usarse porque el evento finalizo."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

        if consume_ticket_atomic(ticket.pk):
            ticket.refresh_from_db()
            detail = "Acceso aprobado. Puedes permitir el ingreso."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.ACCEPTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket, result="ok")
        else:
            detail = "No pudimos validar esta entrada. Verifica el QR o intenta de nuevo."
            ValidationLog.objects.create(
                ticket=ticket,
                admin=request.user,
                outcome=ValidationLog.Outcome.REJECTED,
                detail=detail,
            )
            return render_validator_popup(detail, ticket=ticket)

    return render(request, "admin_tools/validate_token.html", context)


@login_required
@user_passes_test(_can_validate_tickets)
def validate_product_redemption(request):
    context = {
        "message": "",
        "result": "",
        "redemption": None,
        "popup_title": "",
        "popup_message": "",
    }

    def render_redemption_popup(message_text, redemption=None, result="error"):
        popup_context = {
            "message": message_text,
            "result": result,
            "redemption": redemption,
            "popup_title": "Informacion de la reclamacion",
            "popup_message": message_text,
        }
        return render(request, "admin_tools/validate_product_redemption.html", popup_context)

    if request.method == "POST":
        code = (request.POST.get("code") or "").strip().upper()
        if not code:
            return render_redemption_popup("Ingresa un codigo de reclamacion para continuar.")
        normalized_code = code if code.startswith("PROD-") else f"PROD-{code}"

        redemption = (
            ProductRedemption.objects.select_related("user", "event", "order", "delivered_by")
            .filter(code=normalized_code)
            .first()
        )
        if not redemption:
            return render_redemption_popup("No encontramos ese codigo. Verificalo o pide otro comprobante.")

        action = (request.POST.get("action") or "lookup").strip().lower()
        if action == "deliver":
            if redemption.status == ProductRedemption.Status.DELIVERED:
                return render_redemption_popup("Este codigo ya fue marcado como entregado. No repitas la entrega.", redemption=redemption)
            redemption.status = ProductRedemption.Status.DELIVERED
            redemption.delivered_by = request.user
            redemption.delivered_at = timezone.now()
            redemption.save(update_fields=["status", "delivered_by", "delivered_at"])
            redemption.refresh_from_db()
            return render_redemption_popup(
                "Entrega aprobada. Puedes entregar los productos.",
                redemption=redemption,
                result="ok",
            )

        if redemption.status == ProductRedemption.Status.DELIVERED:
            return render_redemption_popup("Este codigo ya fue marcado como entregado. No repitas la entrega.", redemption=redemption)
        return render_redemption_popup("Codigo valido. Revisa el detalle y confirma la entrega.", redemption=redemption, result="ok")

    return render(request, "admin_tools/validate_product_redemption.html", context)


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
        try:
            with transaction.atomic():
                event = form.save(commit=False)
                event.created_by = request.user
                event.save()
                form._save_m2m()
                form._save_ticket_types(event)
                form._save_products(event, request.FILES)

                files = request.FILES.getlist("images")
                selected_files = files[:MAX_EVENT_IMAGES]
                has_non_recommended_image = any(
                    not _is_recommended_image_ratio(image_file) for image_file in selected_files
                )
                for image_file in selected_files:
                    EventImage.objects.create(event=event, image=image_file)
        except IntegrityError:
            form.add_error(
                "products_payload",
                "Revisa los productos y variantes: no se pudieron guardar por datos repetidos o invalidos.",
            )
        else:
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

    if request.method == "POST" and form.errors:
        messages.error(request, "No se pudo guardar el evento. Revisa los campos marcados.")

    return render(request, "admin_tools/create_event.html", {"form": form})


@login_required
@user_passes_test(lambda user: user.is_staff)
def update_event(request, pk):
    event = get_object_or_404(Event, pk=pk)
    if request.method == "POST":
        form = EventUpdateForm(request.POST, instance=event)
        if not form.is_valid():
            if "general_ticket_limit" in form.errors:
                messages.error(
                    request,
                    "No puedes reducir el limite por debajo de las entradas ya emitidas.",
                )
            elif any(field in form.errors for field in ["vip_ticket_limit", "vip_price_usd"]):
                messages.error(
                    request,
                    "Revisa la configuracion VIP antes de guardar.",
                )
            else:
                messages.error(request, "No se pudieron guardar los cambios del evento.")
            return render(
                request,
                "admin_tools/update_event.html",
                {"event": event, "form": form, "event_images": event.images.order_by("created_at")},
                status=200,
            )

        form.save(commit=False)
        event.save()
        form._save_m2m()
        form._save_ticket_types(event)
        form._save_products(event, request.FILES)

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

    form = EventUpdateForm(instance=event)
    return render(
        request,
        "admin_tools/update_event.html",
        {"event": event, "form": form, "event_images": event.images.order_by("created_at")},
    )


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
def delete_event(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    event = get_object_or_404(
        Event.objects.prefetch_related("images", "products"),
        pk=pk,
    )
    event_title = event.title
    _delete_event_assets(event)
    _delete_event_related_records(event)
    event.delete()
    messages.success(request, f'El evento "{event_title}" y toda su informacion relacionada fueron eliminados.')
    return redirect("event_list")


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
@user_passes_test(lambda user: not user.is_staff)
def cart_detail(request):
    cart = _active_cart_for_user(request.user)
    cart = _sync_cart_event(cart)
    items = list(
        cart.items.select_related("ticket_type__event", "product_variant__product__event").order_by("created_at", "id")
    )
    ticket_items = [item for item in items if item.item_type == CartItem.ItemType.TICKET]
    product_items = [item for item in items if item.item_type == CartItem.ItemType.PRODUCT]
    return render(
        request,
        "cart/cart_detail.html",
        {
            "cart": cart,
            "cart_items": items,
            "cart_items_count": sum(item.quantity for item in items),
            "ticket_items": ticket_items,
            "product_items": product_items,
        },
    )


@login_required
@user_passes_test(lambda user: not user.is_staff)
def add_ticket_to_cart(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    event = get_object_or_404(Event, pk=pk, status=Event.Status.ACTIVE)
    purchase_block_reason = _event_purchase_block_reason(event)
    if purchase_block_reason:
        messages.error(request, purchase_block_reason)
        return redirect("event_detail", pk=event.pk)

    ticket_type_code = (request.POST.get("ticket_type") or EventTicketType.Code.GENERAL).strip().lower()
    quantity_raw = (request.POST.get("quantity") or "1").strip()
    if not quantity_raw.isdigit():
        messages.error(request, "La cantidad seleccionada no es valida.")
        return redirect("event_detail", pk=event.pk)
    quantity = int(quantity_raw)
    if quantity < 1:
        messages.error(request, "Debes agregar al menos una boleta.")
        return redirect("event_detail", pk=event.pk)

    ticket_type = event.ticket_types.filter(code=ticket_type_code, is_active=True).first()
    if not ticket_type:
        if ticket_type_code == EventTicketType.Code.GENERAL:
            ticket_type = event.ensure_general_ticket_type()
        else:
            messages.error(request, "La boleta seleccionada no esta disponible.")
            return redirect("event_detail", pk=event.pk)

    cart = _active_cart_for_user(request.user)
    if not _ensure_cart_event(cart, event):
        messages.error(request, "Tu carrito actual pertenece a otro evento. Vacialo primero para continuar.")
        return redirect("cart_detail")

    existing_item = cart.items.filter(ticket_type=ticket_type).first()
    requested_total = quantity + (existing_item.quantity if existing_item else 0)
    if requested_total > ticket_type.remaining_tickets_count:
        messages.error(request, "No hay suficientes boletas disponibles para esa cantidad.")
        return redirect("event_detail", pk=event.pk)

    if existing_item:
        existing_item.quantity = requested_total
        existing_item.unit_price_usd = ticket_type.price_usd
        existing_item.save(update_fields=["quantity", "unit_price_usd", "updated_at"])
    else:
        CartItem.objects.create(
            cart=cart,
            item_type=CartItem.ItemType.TICKET,
            ticket_type=ticket_type,
            quantity=quantity,
            unit_price_usd=ticket_type.price_usd,
        )
    _sync_cart_event(cart)
    messages.success(request, f'Se agrego {ticket_type.name} al carrito.', extra_tags="cart-added")
    return redirect(_cart_return_target(request, "event_detail", pk=event.pk))


@login_required
@user_passes_test(lambda user: not user.is_staff)
def add_product_to_cart(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    event = get_object_or_404(Event, pk=pk, status=Event.Status.ACTIVE)
    purchase_block_reason = _event_purchase_block_reason(event)
    if purchase_block_reason:
        messages.error(request, purchase_block_reason)
        return redirect("event_detail", pk=event.pk)

    variant_id_raw = (request.POST.get("product_variant") or "").strip()
    quantity_raw = (request.POST.get("quantity") or "1").strip()
    if not variant_id_raw.isdigit():
        messages.error(request, "Debes seleccionar una variante valida.")
        return redirect("event_detail", pk=event.pk)
    if not quantity_raw.isdigit():
        messages.error(request, "La cantidad seleccionada no es valida.")
        return redirect("event_detail", pk=event.pk)

    quantity = int(quantity_raw)
    if quantity < 1:
        messages.error(request, "Debes agregar al menos un producto.")
        return redirect("event_detail", pk=event.pk)

    variant = (
        ProductVariant.objects.select_related("product__event")
        .filter(
            pk=int(variant_id_raw),
            product__event=event,
            product__is_active=True,
            is_active=True,
        )
        .first()
    )
    if not variant:
        messages.error(request, "La variante seleccionada no esta disponible.")
        return redirect("event_detail", pk=event.pk)

    cart = _active_cart_for_user(request.user)
    if not _ensure_cart_event(cart, event):
        messages.error(request, "Tu carrito actual pertenece a otro evento. Vacialo primero para continuar.")
        return redirect("cart_detail")

    existing_item = cart.items.filter(product_variant=variant).first()
    requested_total = quantity + (existing_item.quantity if existing_item else 0)
    if requested_total > variant.remaining_stock:
        messages.error(request, "No hay suficiente stock disponible para esa variante.")
        return redirect("event_detail", pk=event.pk)

    if existing_item:
        existing_item.quantity = requested_total
        existing_item.unit_price_usd = variant.product.price_usd
        existing_item.save(update_fields=["quantity", "unit_price_usd", "updated_at"])
    else:
        CartItem.objects.create(
            cart=cart,
            item_type=CartItem.ItemType.PRODUCT,
            product_variant=variant,
            quantity=quantity,
            unit_price_usd=variant.product.price_usd,
        )
    _sync_cart_event(cart)
    messages.success(request, f'Se agrego {variant.product.name} al carrito.', extra_tags="cart-added")
    return redirect(_cart_return_target(request, "event_detail", pk=event.pk))


@login_required
@user_passes_test(lambda user: not user.is_staff)
def update_cart_item(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    cart = _active_cart_for_user(request.user)
    item = get_object_or_404(
        CartItem.objects.select_related("ticket_type", "product_variant__product"),
        pk=pk,
        cart=cart,
    )
    quantity_raw = (request.POST.get("quantity") or "").strip()
    if not quantity_raw.isdigit():
        messages.error(request, "La cantidad seleccionada no es valida.")
        return redirect("cart_detail")

    quantity = int(quantity_raw)
    if quantity < 1:
        messages.error(request, "La cantidad minima es 1.")
        return redirect("cart_detail")

    if item.item_type == CartItem.ItemType.PRODUCT:
        max_available = item.product_variant.remaining_stock + item.quantity
        if quantity > max_available:
            messages.error(request, "La cantidad supera la disponibilidad actual.")
            return redirect("cart_detail")
        item.quantity = quantity
        item.unit_price_usd = item.product_variant.product.price_usd
        item.save(update_fields=["quantity", "unit_price_usd", "updated_at"])
        messages.success(request, "Cantidad actualizada.")
        return redirect("cart_detail")

    max_available = item.ticket_type.remaining_tickets_count
    if quantity > max_available:
        messages.error(request, "La cantidad supera la disponibilidad actual.")
        return redirect("cart_detail")

    item.quantity = quantity
    item.unit_price_usd = item.ticket_type.price_usd
    item.save(update_fields=["quantity", "unit_price_usd", "updated_at"])
    messages.success(request, "Cantidad actualizada.")
    return redirect("cart_detail")


@login_required
@user_passes_test(lambda user: not user.is_staff)
def remove_cart_item(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    cart = _active_cart_for_user(request.user)
    item = get_object_or_404(CartItem, pk=pk, cart=cart)
    item.delete()
    _sync_cart_event(cart)
    messages.success(request, "Item eliminado del carrito.")
    return redirect("cart_detail")


@login_required
@user_passes_test(lambda user: not user.is_staff)
def clear_cart(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    cart = _active_cart_for_user(request.user)
    cart.items.all().delete()
    _sync_cart_event(cart)
    messages.success(request, "Carrito vaciado.")
    return redirect("cart_detail")


@login_required
@user_passes_test(lambda user: not user.is_staff)
def checkout_cart(request):
    cart = _active_cart_for_user(request.user)
    cart = _sync_cart_event(cart)
    items = list(
        cart.items.select_related("ticket_type__event", "product_variant__product__event").order_by("created_at", "id")
    )
    if not items:
        messages.error(request, "Tu carrito esta vacio.")
        return redirect("cart_detail")

    if request.method == "GET":
        return render(
            request,
            "cart/checkout.html",
            {
                "cart": cart,
                "cart_items": items,
                "cart_items_count": sum(item.quantity for item in items),
            },
        )

    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            with transaction.atomic():
                cart = Cart.objects.select_for_update().get(pk=cart.pk, user=request.user, status=Cart.Status.ACTIVE)
                items = list(
                    cart.items.select_related("ticket_type__event", "product_variant__product__event").order_by("created_at", "id")
                )
                if not items:
                    messages.error(request, "Tu carrito esta vacio.")
                    return redirect("cart_detail")

                event = cart.event or items[0].event
                if event is not None:
                    event = Event.objects.select_for_update().get(pk=event.pk)
                    if event.status != Event.Status.ACTIVE:
                        messages.error(request, "El evento asociado a tu carrito ya no esta disponible.")
                        return redirect("cart_detail")
                    purchase_block_reason = _event_purchase_block_reason(event)
                    if purchase_block_reason:
                        if purchase_block_reason == "Este evento ya finalizo.":
                            purchase_block_reason = "Este evento ya finalizo y no admite nuevas compras."
                        messages.error(request, purchase_block_reason)
                        return redirect("cart_detail")
                snapshot = _build_checkout_snapshot(items, event)
                if snapshot.get("error"):
                    messages.error(request, snapshot["error"])
                    return redirect("cart_detail")

                order = _get_or_create_pending_order_from_snapshot(request.user, snapshot)
                _create_order_items_from_snapshot(order, snapshot)
                tickets_with_qr = []
                product_redemption = None
                stripe_redirect = _redirect_order_to_stripe_checkout(
                    request,
                    order,
                )
                if stripe_redirect is not None:
                    return stripe_redirect
            break
        except (OperationalError, IntegrityError):
            if attempt == max_attempts - 1:
                return HttpResponse(
                    "No fue posible completar el checkout. Intenta de nuevo.",
                    status=503,
                )
            continue

    return render(
        request,
        "cart/checkout_pending.html",
        {
            "event": event,
            "order": order,
            "cart_items": items,
            "tickets_with_qr": tickets_with_qr,
            "product_redemption": product_redemption,
        },
    )


@login_required
@user_passes_test(lambda user: not user.is_staff)
def start_stripe_checkout(request, order_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    order = get_object_or_404(
        Order.objects.select_related("event").prefetch_related("items"),
        pk=order_id,
        user=request.user,
        status=Order.Status.PENDING,
    )
    if order.is_payment_final:
        messages.error(request, "Esta orden ya no esta disponible para iniciar pago.")
        return redirect("cart_detail")

    stripe_redirect = _redirect_order_to_stripe_checkout(
        request,
        order,
    )
    if stripe_redirect is not None:
        return stripe_redirect
    return redirect("checkout_cart")


@login_required
@user_passes_test(lambda user: not user.is_staff)
def stripe_checkout_success(request):
    order_id = request.GET.get("order_id", "").strip()
    order = None
    order_has_products = False
    redirect_target = reverse("my_tickets")
    if order_id.isdigit():
        order = (
            Order.objects.select_related("event")
            .prefetch_related("items")
            .filter(pk=int(order_id), user=request.user)
            .first()
        )
    if order is not None:
        order_has_products = order.items.filter(item_type=OrderItem.ItemType.PRODUCT).exists()
        if order_has_products and order.event_id:
            redirect_target = f"{reverse('my_tickets')}?event_id={order.event_id}"
    return render(
        request,
        "cart/stripe_checkout_success.html",
        {
            "order": order,
            "order_has_products": order_has_products,
            "redirect_target": redirect_target,
            "session_id": request.GET.get("session_id", "").strip(),
        },
    )


@login_required
@user_passes_test(lambda user: not user.is_staff)
def stripe_checkout_cancel(request):
    order_id = request.GET.get("order_id", "").strip()
    order = None
    if order_id.isdigit():
        order = (
            Order.objects.select_related("event")
            .filter(pk=int(order_id), user=request.user)
            .first()
        )
    return render(
        request,
        "cart/stripe_checkout_cancel.html",
        {
            "order": order,
        },
    )


def _build_stripe_checkout_urls(request, order):
    success_url = request.build_absolute_uri(
        reverse("stripe_checkout_success")
    ) + f"?order_id={order.pk}&session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = request.build_absolute_uri(
        reverse("stripe_checkout_cancel")
    ) + f"?order_id={order.pk}"
    return success_url, cancel_url


def _redirect_order_to_stripe_checkout(request, order):
    success_url, cancel_url = _build_stripe_checkout_urls(request, order)

    try:
        session = create_checkout_session(order, success_url, cancel_url)
    except StripeConfigurationError as exc:
        messages.error(request, str(exc))
        return None
    except Exception:
        messages.error(request, "No fue posible iniciar Stripe Checkout. Intenta de nuevo.")
        return None

    order.payment_provider = "stripe"
    order.payment_status_detail = "checkout_session_created"
    order.stripe_checkout_session_id = session.id
    order.save(
        update_fields=[
            "payment_provider",
            "payment_status_detail",
            "stripe_checkout_session_id",
        ]
    )
    return redirect(session.url, permanent=False)


@csrf_exempt
def stripe_webhook(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        event = construct_webhook_event(
            request.body,
            request.META.get("HTTP_STRIPE_SIGNATURE", "").strip(),
        )
    except (StripeConfigurationError, StripeWebhookError) as exc:
        return HttpResponse(str(exc), status=400)

    event_type = event.get("type", "")
    if event_type != "checkout.session.completed":
        return JsonResponse({"received": True, "ignored": event_type})

    session = event.get("data", {}).get("object", {})
    order_id = str(
        session.get("metadata", {}).get("order_id")
        or session.get("client_reference_id")
        or ""
    ).strip()
    if not order_id.isdigit():
        return HttpResponse("El webhook de Stripe no incluye un order_id valido.", status=400)

    session_id = str(session.get("id") or "").strip()
    payment_intent_id = str(session.get("payment_intent") or "").strip()

    with transaction.atomic():
        order = (
            Order.objects.select_for_update()
            .select_related("user")
            .prefetch_related("items")
            .filter(pk=int(order_id))
            .first()
        )
        if order is None:
            return HttpResponse("La orden asociada al webhook no existe.", status=404)

        if (
            order.stripe_checkout_session_id
            and session_id
            and order.stripe_checkout_session_id != session_id
        ):
            return HttpResponse("La sesion de Stripe no coincide con la orden.", status=400)

        if order.status == Order.Status.PAID:
            return JsonResponse({"received": True, "status": "already_paid"})

        if order.status != Order.Status.PENDING:
            return JsonResponse({"received": True, "status": order.status.lower()})

        try:
            snapshot = _build_fulfillment_snapshot_from_order(order)
            _fulfill_paid_order(order, snapshot)
        except OrderFulfillmentError:
            order.payment_provider = "stripe"
            order.payment_status_detail = "paid_fulfillment_failed"
            if session_id:
                order.stripe_checkout_session_id = session_id
            if payment_intent_id:
                order.stripe_payment_intent_id = payment_intent_id
            order.save(
                update_fields=[
                    "payment_provider",
                    "payment_status_detail",
                    "stripe_checkout_session_id",
                    "stripe_payment_intent_id",
                ]
            )
            return HttpResponse(
                "El pago fue confirmado, pero la orden no pudo entregarse automaticamente.",
                status=409,
            )

        order.status = Order.Status.PAID
        order.payment_provider = "stripe"
        order.payment_status_detail = "paid"
        if session_id:
            order.stripe_checkout_session_id = session_id
        if payment_intent_id:
            order.stripe_payment_intent_id = payment_intent_id
        order.payment_confirmed_at = timezone.now()
        order.save(
            update_fields=[
                "status",
                "payment_provider",
                "payment_status_detail",
                "stripe_checkout_session_id",
                "stripe_payment_intent_id",
                "payment_confirmed_at",
            ]
        )
        _convert_active_cart_after_payment(order)

    return JsonResponse({"received": True, "status": "paid"})


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
def user_products(request):
    event_id = request.GET.get("event_id", "").strip()
    username_query = request.GET.get("username", "").strip()
    redemptions = ProductRedemption.objects.select_related("user", "event", "delivered_by", "order")
    if event_id.isdigit():
        redemptions = redemptions.filter(event_id=int(event_id))
    if username_query:
        redemptions = redemptions.filter(user__username__icontains=username_query)
    redemptions = redemptions.prefetch_related(
        Prefetch(
            "order__items",
            queryset=OrderItem.objects.filter(item_type=OrderItem.ItemType.PRODUCT)
            .select_related("product_variant__product")
            .order_by("created_at", "id"),
        )
    ).order_by("-created_at", "-id")
    events = Event.objects.order_by("title")
    return render(
        request,
        "admin_tools/user_products.html",
        {
            "redemptions": redemptions,
            "selected_user": None,
            "events": events,
            "selected_event_id": event_id,
            "username_query": username_query,
        },
    )


@login_required
@user_passes_test(_can_validate_tickets)
def user_products_by_user(request, user_id):
    selected_user = get_object_or_404(User, pk=user_id)
    event_id = request.GET.get("event_id", "").strip()
    redemptions = ProductRedemption.objects.select_related("user", "event", "delivered_by", "order").filter(
        user_id=user_id
    )
    if event_id.isdigit():
        redemptions = redemptions.filter(event_id=int(event_id))
    redemptions = redemptions.prefetch_related(
        Prefetch(
            "order__items",
            queryset=OrderItem.objects.filter(item_type=OrderItem.ItemType.PRODUCT)
            .select_related("product_variant__product")
            .order_by("created_at", "id"),
        )
    ).order_by("-created_at", "-id")
    events = Event.objects.order_by("title")
    return render(
        request,
        "admin_tools/user_products.html",
        {
            "redemptions": redemptions,
            "selected_user": selected_user,
            "events": events,
            "selected_event_id": event_id,
            "username_query": "",
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
        messages.success(request, "Cambios guardados.")
        return redirect("home")

    return render(
        request,
        "accounts/edit_profile.html",
        {"form": form, "email_form": email_form, "profile": profile},
    )


@login_required
def submit_event_review(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    event = get_object_or_404(Event, pk=pk, status=Event.Status.ACTIVE)
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




