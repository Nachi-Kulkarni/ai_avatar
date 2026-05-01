import re
from supabase import create_client, Client
from config import settings

_client: Client | None = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _client


# E.164 normalization for Indian mobile numbers
_INDIA_PREFIX = "+91"
_INDIAN_MOBILE_RE = re.compile(r"^(?:\+91|91|0)?([6-9]\d{9})$")


def normalize_phone(raw: str) -> str:
    m = _INDIAN_MOBILE_RE.match(raw.strip().replace(" ", "").replace("-", ""))
    if not m:
        raise ValueError(f"Invalid Indian mobile number: {raw}")
    return _INDIA_PREFIX + m.group(1)
