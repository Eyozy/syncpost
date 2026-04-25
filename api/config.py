import os
from typing import Callable, Dict, List

ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID")
TG_WEBHOOK_SECRET = os.getenv("TG_WEBHOOK_SECRET")
MASTO_TOKEN = os.getenv("MASTO_TOKEN")
MASTO_INSTANCE = os.getenv("MASTO_INSTANCE")
DATABASE_URL = os.getenv("DATABASE_URL")

TG_API = f"https://api.telegram.org/bot{TG_TOKEN}" if TG_TOKEN else ""

RequiredConfig = Dict[str, Callable[[], bool]]


REQUIRED_CONFIG: RequiredConfig = {
    "ADMIN_ID": lambda: bool(ADMIN_ID),
    "TG_TOKEN": lambda: bool(TG_TOKEN),
    "TG_CHANNEL_ID": lambda: bool(TG_CHANNEL_ID),
    "TG_WEBHOOK_SECRET": lambda: bool(TG_WEBHOOK_SECRET),
    "MASTO_INSTANCE": lambda: bool(MASTO_INSTANCE),
    "MASTO_TOKEN": lambda: bool(MASTO_TOKEN),
    "DATABASE_URL": lambda: bool(DATABASE_URL),
}


def get_missing_config() -> List[str]:
    return [name for name, checker in REQUIRED_CONFIG.items() if not checker()]


def is_config_complete() -> bool:
    return not get_missing_config()
