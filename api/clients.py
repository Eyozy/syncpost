import logging
from typing import Any, Dict, Optional

import requests as req
from requests import Response

from api.config import MASTO_INSTANCE, MASTO_TOKEN, TG_API

logger = logging.getLogger(__name__)


Payload = Dict[str, Any]


def telegram_request(method: str, payload: Payload) -> Optional[Response]:
    try:
        return req.post(f"{TG_API}/{method}", json=payload, timeout=10)
    except req.exceptions.RequestException as e:
        logger.error(f"Telegram 请求失败 ({method})：{e}")
        return None


def mastodon_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {MASTO_TOKEN}"}


def mastodon_post(path: str, payload: Payload) -> Optional[Response]:
    try:
        return req.post(
            f"{MASTO_INSTANCE}{path}",
            headers=mastodon_headers(),
            json=payload,
            timeout=10,
        )
    except req.exceptions.RequestException as e:
        logger.error(f"Mastodon POST 请求失败 ({path})：{e}")
        return None


def mastodon_put(path: str, payload: Payload) -> Optional[Response]:
    try:
        return req.put(
            f"{MASTO_INSTANCE}{path}",
            headers=mastodon_headers(),
            json=payload,
            timeout=10,
        )
    except req.exceptions.RequestException as e:
        logger.error(f"Mastodon PUT 请求失败 ({path})：{e}")
        return None


def mastodon_delete(path: str) -> Optional[Response]:
    try:
        return req.delete(
            f"{MASTO_INSTANCE}{path}", headers=mastodon_headers(), timeout=10
        )
    except req.exceptions.RequestException as e:
        logger.error(f"Mastodon DELETE 请求失败 ({path})：{e}")
        return None
