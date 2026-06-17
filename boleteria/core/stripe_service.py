from decimal import Decimal

from django.conf import settings


class StripeConfigurationError(RuntimeError):
    pass


class StripeWebhookError(RuntimeError):
    pass


def _require_stripe_sdk():
    try:
        import stripe
    except ImportError as exc:
        raise StripeConfigurationError(
            "El SDK de Stripe no esta instalado en este entorno."
        ) from exc
    return stripe


def _build_line_item(order_item, currency):
    unit_amount_decimal = (order_item.unit_price_usd * Decimal("100")).quantize(Decimal("1"))
    return {
        "quantity": order_item.quantity,
        "price_data": {
            "currency": currency,
            "unit_amount": int(unit_amount_decimal),
            "product_data": {
                "name": order_item.item_name,
            },
        },
    }


def create_checkout_session(order, success_url, cancel_url):
    if not settings.STRIPE_SECRET_KEY:
        raise StripeConfigurationError("Falta configurar STRIPE_SECRET_KEY.")

    stripe = _require_stripe_sdk()
    stripe.api_key = settings.STRIPE_SECRET_KEY
    currency = settings.STRIPE_CURRENCY or "usd"

    line_items = [
        _build_line_item(order_item, currency)
        for order_item in order.items.order_by("created_at", "id")
    ]
    if not line_items:
        raise StripeConfigurationError("La orden no tiene items para enviar a Stripe.")

    return stripe.checkout.Session.create(
        mode="payment",
        line_items=line_items,
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=str(order.pk),
        metadata={
            "order_id": str(order.pk),
        },
    )


def construct_webhook_event(payload, signature_header):
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise StripeConfigurationError("Falta configurar STRIPE_WEBHOOK_SECRET.")
    if not signature_header:
        raise StripeWebhookError("Falta la firma del webhook de Stripe.")

    stripe = _require_stripe_sdk()
    try:
        return stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature_header,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except ValueError as exc:
        raise StripeWebhookError("El payload del webhook de Stripe es invalido.") from exc
    except stripe.error.SignatureVerificationError as exc:
        raise StripeWebhookError("La firma del webhook de Stripe no es valida.") from exc
