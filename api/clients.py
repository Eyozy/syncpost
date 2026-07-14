import json
import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional

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


def send_tg_message(
    chat_id: int, text: str, reply_to: Optional[int] = None
) -> Optional[Payload]:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to}
    resp = telegram_request("sendMessage", payload)
    if not resp:
        return None
    return resp.json() if resp.ok else None


def edit_tg_message(chat_id: Optional[str], message_id: int, text: str) -> bool:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    resp = telegram_request("editMessageText", payload)
    if not resp:
        return False
    return resp.ok


def edit_tg_message_caption(chat_id: Optional[str], message_id: int, caption: str) -> bool:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "caption": caption,
        "parse_mode": "HTML",
    }
    resp = telegram_request("editMessageCaption", payload)
    if not resp:
        return False
    return resp.ok


def edit_tg_media_message(
    chat_id: Optional[str],
    message_id: int,
    content: bytes,
    filename: str,
    mime_type: str,
    caption: str,
    media_type: str,
) -> bool:
    media = {
        "type": media_type,
        "media": f"attach://{media_type}",
        "caption": caption,
        "parse_mode": "HTML",
    }
    if media_type == "video":
        media["supports_streaming"] = True
    try:
        resp = req.post(
            f"{TG_API}/editMessageMedia",
            data={"chat_id": chat_id, "message_id": message_id, "media": json.dumps(media)},
            files={media_type: (filename, content, mime_type)},
            timeout=30,
        )
    except req.exceptions.RequestException as e:
        logger.error(f"Telegram 媒体替换失败：{e}")
        return False
    return bool(resp and resp.ok)


def get_tg_file_path(file_id: str) -> Optional[str]:
    resp = telegram_request("getFile", {"file_id": file_id})
    if not resp or not resp.ok:
        return None
    return resp.json().get("result", {}).get("file_path")


def download_tg_file(file_path: str) -> Optional[bytes]:
    from api.config import TG_TOKEN
    url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}"
    try:
        resp = req.get(url, timeout=30)
        return resp.content if resp.ok else None
    except req.exceptions.RequestException as e:
        logger.error(f"Telegram 文件下载失败：{e}")
        return None


def edit_message_text(chat_id: int, message_id: int, text: str) -> bool:
    return edit_tg_message(chat_id, message_id, text)


def delete_tg_message(chat_id: Optional[str], message_id: int) -> bool:
    payload = {"chat_id": chat_id, "message_id": message_id}
    resp = telegram_request("deleteMessage", payload)
    if not resp:
        return False
    return resp.ok


def delete_tg_messages(chat_id: int, message_ids: List[int]) -> bool:
    if not message_ids:
        return True

    payload = {
        "chat_id": chat_id,
        "message_ids": message_ids,
    }
    resp = telegram_request("deleteMessages", payload)
    if not resp:
        return False
    return resp.ok


def send_inline_keyboard(
    chat_id: int, text: str, buttons: List[List[Dict[str, str]]]
) -> Optional[Payload]:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": buttons},
    }
    resp = telegram_request("sendMessage", payload)
    if not resp:
        return None
    return resp.json() if resp.ok else None


def answer_callback_query(
    callback_query_id: str, text: Optional[str] = None, show_alert: bool = False
) -> bool:
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert
    resp = telegram_request("answerCallbackQuery", payload)
    if not resp:
        return False
    return resp.ok


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


def mastodon_get(path: str) -> Optional[Response]:
    try:
        return req.get(
            f"{MASTO_INSTANCE}{path}",
            headers=mastodon_headers(),
            timeout=10,
        )
    except req.exceptions.RequestException as e:
        logger.error(f"Mastodon GET 请求失败 ({path})：{e}")
        return None


def edit_mastodon_status_media(status_id: str, text: str, media_id: str) -> bool:
    resp = mastodon_put(
        f"/api/v1/statuses/{status_id}",
        {"status": text, "media_ids": [media_id]},
    )
    return bool(resp and resp.ok)


def mastodon_delete(path: str) -> Optional[Response]:
    try:
        return req.delete(
            f"{MASTO_INSTANCE}{path}", headers=mastodon_headers(), timeout=10
        )
    except req.exceptions.RequestException as e:
        logger.error(f"Mastodon DELETE 请求失败 ({path})：{e}")
        return None


def post_to_mastodon(
    text: str, in_reply_to_id: Optional[str] = None
) -> Optional[Payload]:
    payload = {"status": text, "visibility": "public"}
    if in_reply_to_id:
        payload["in_reply_to_id"] = in_reply_to_id
    resp = mastodon_post("/api/v1/statuses", payload)
    if not resp:
        return None
    return resp.json() if resp.ok else None


def edit_mastodon_status(status_id: str, text: str) -> bool:
    status_resp = mastodon_get(f"/api/v1/statuses/{status_id}")
    if not status_resp or not status_resp.ok:
        return False
    try:
        media_ids = [
            media["id"]
            for media in status_resp.json().get("media_attachments", [])
            if media.get("id")
        ]
    except (ValueError, TypeError, AttributeError):
        return False

    payload = {"status": text}
    if media_ids:
        payload["media_ids"] = media_ids
    resp = mastodon_put(f"/api/v1/statuses/{status_id}", payload)
    if not resp:
        return False
    return resp.ok


def delete_mastodon_status(status_id: str) -> bool:
    resp = mastodon_delete(f"/api/v1/statuses/{status_id}")
    if not resp:
        return False
    return resp.ok


def upload_mastodon_media(
    file_content: bytes, filename: str, mime_type: str
) -> Optional[Payload]:
    try:
        files = {"file": (filename, file_content, mime_type)}
        resp = req.post(
            f"{MASTO_INSTANCE}/api/v1/media",
            headers=mastodon_headers(),
            files=files,
            timeout=30,
        )
        if not resp:
            return None
        return resp.json() if resp.ok else None
    except req.exceptions.RequestException as e:
        logger.error(f"Mastodon 媒体上传失败：{e}")
        return None


@lru_cache(maxsize=1)
def get_mastodon_video_size_limit() -> Optional[int]:
    try:
        resp = req.get(f"{MASTO_INSTANCE}/api/v2/instance", timeout=10)
        if not resp or not resp.ok:
            return None
        return (
            resp.json()
            .get("configuration", {})
            .get("media_attachments", {})
            .get("video_size_limit")
        )
    except (req.exceptions.RequestException, ValueError, TypeError, AttributeError) as e:
        logger.warning(f"读取 Mastodon 视频大小限制失败：{e}")
        return None
