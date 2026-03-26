import re
import unicodedata

from django.conf import settings


DEFAULT_COMMENT_BLOCKLIST = (
    "hp",
    "hpta",
    "hijueputa",
    "gonorrea",
    "marica",
    "mierda",
    "puta",
)

_LEET_MAP = str.maketrans(
    {
        "@": "a",
        "$": "s",
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
    }
)


def normalize_for_moderation(value):
    text = (value or "").strip().lower().translate(_LEET_MAP)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def contains_blocked_language(value):
    normalized_text = normalize_for_moderation(value)
    if not normalized_text:
        return False
    compact_text = normalized_text.replace(" ", "")

    terms = getattr(settings, "COMMENT_BLOCKLIST", DEFAULT_COMMENT_BLOCKLIST)
    for raw_term in terms:
        term = normalize_for_moderation(raw_term)
        if not term:
            continue
        pattern = rf"\b{re.escape(term)}\b"
        if re.search(pattern, normalized_text):
            return True
        compact_term = term.replace(" ", "")
        if len(compact_term) >= 5 and compact_term in compact_text:
            return True
    return False
