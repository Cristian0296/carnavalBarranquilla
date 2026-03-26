import base64
import hashlib
import hmac
import json
import time
from io import BytesIO
from typing import Any

import qrcode
from django.conf import settings
from django.db import OperationalError, transaction
from django.utils import timezone


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(f"{data}{padding}".encode("utf-8"))


def generate_ticket_token(ticket) -> str:
    payload = {
        "ticket_uuid": str(ticket.ticket_uuid),
        "event_id": ticket.event_id,
        "issued_at": int(ticket.issued_at.timestamp()),
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_encoded = _b64url_encode(payload_json)
    signature = hmac.new(
        settings.HMAC_SECRET_KEY.encode("utf-8"),
        payload_json,
        hashlib.sha256,
    ).digest()
    signature_encoded = _b64url_encode(signature)
    return f"{payload_encoded}.{signature_encoded}"


def verify_ticket_token(token: str) -> tuple[bool, dict[str, Any] | None, str]:
    if not token or "." not in token:
        return False, None, "Formato de token invalido."

    payload_encoded, signature_encoded = token.split(".", 1)
    try:
        payload_json = _b64url_decode(payload_encoded)
        given_signature = _b64url_decode(signature_encoded)
        payload = json.loads(payload_json.decode("utf-8"))
    except Exception:
        return False, None, "No se pudo decodificar el token."

    expected_signature = hmac.new(
        settings.HMAC_SECRET_KEY.encode("utf-8"),
        payload_json,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(given_signature, expected_signature):
        return False, None, "Firma HMAC invalida."

    required_fields = {"ticket_uuid", "event_id", "issued_at"}
    if not isinstance(payload, dict) or not required_fields.issubset(payload.keys()):
        return False, None, "Payload de token incompleto."

    return True, payload, "Token valido."


def build_qr_data_uri(content: str) -> str:
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(content)
    qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white")
    output = BytesIO()
    image.save(output, format="PNG")
    png_base64 = base64.b64encode(output.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{png_base64}"


def build_qr_jpg_bytes(content: str) -> bytes:
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(content)
    qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    output = BytesIO()
    image.save(output, format="JPEG", quality=92)
    return output.getvalue()


def consume_ticket_atomic(ticket_id: int, retries: int = 10) -> bool:
    from .models import Ticket

    for attempt in range(retries):
        try:
            with transaction.atomic():
                updated_rows = Ticket.objects.filter(
                    pk=ticket_id,
                    status=Ticket.Status.UNUSED,
                ).update(status=Ticket.Status.USED, used_at=timezone.now())
            return updated_rows == 1
        except OperationalError:
            if attempt == retries - 1:
                raise
            time.sleep(0.02 * (attempt + 1))

    return False
