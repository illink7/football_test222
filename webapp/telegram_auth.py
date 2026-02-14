"""
Validate Telegram Web App initData and extract user info.
See https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hmac
import hashlib
import json
from urllib.parse import unquote, parse_qs


def validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """Validate init_data from Telegram Web App. Returns parsed data dict or None."""
    if not init_data or not bot_token:
        return None
    parsed = {}
    hash_val = None
    for part in init_data.split("&"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        value = unquote(value)
        if key == "hash":
            hash_val = value
            continue
        parsed[key] = value
    if not hash_val:
        return None
    data_check_string = "\n".join(f"{k}={parsed[k]}" for k in sorted(parsed.keys()))
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode(),
        hashlib.sha256,
    ).digest()
    computed = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    if computed != hash_val:
        return None
    return parsed


def get_user_id_from_init_data(init_data: str, bot_token: str) -> int | None:
    """Return Telegram user id from validated init_data, or None."""
    data = validate_init_data(init_data, bot_token)
    if not data:
        return None
    user_str = data.get("user")
    if not user_str:
        return None
    try:
        user = json.loads(user_str)
        return int(user.get("id"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
