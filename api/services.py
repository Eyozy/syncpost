import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from api.config import ADMIN_ID, MASTO_INSTANCE, TG_CHANNEL_ID
from api.messages import PARTIAL_PUBLISH_TEXT, PUBLISH_SUCCESS_TEXT, SYNCING_TEXT

Mapping = Dict[str, Any]
SendMessage = Callable[[int, str, Optional[int]], Optional[Dict[str, Any]]]
EditMessageText = Callable[[int, int, str], bool]
TelegramRequest = Callable[[str, Dict[str, Any]], Any]
PostToMastodon = Callable[[str], Optional[Dict[str, Any]]]
SaveMapping = Callable[..., None]
SavePendingMediaGroupItem = Callable[[str, int, Dict[str, Any]], None]

GetPendingMediaGroupItems = Callable[[str], List[Dict[str, Any]]]
DeletePendingMediaGroupItems = Callable[[str], None]
PopReadyPendingMediaGroupItems = Callable[[str, int], List[Dict[str, Any]]]
GetMapping = Callable[[int], Optional[Mapping]]
HasTarget = Callable[[Optional[str]], bool]
EditTelegramMessage = Callable[[str, int, str], bool]
EditMastodonStatus = Callable[[str, str], bool]
DeleteTelegramMessage = Callable[[str, int], bool]
DeleteMastodonStatus = Callable[[str], bool]
DeleteMapping = Callable[[int], None]

MAX_MEDIA_SIZE_BYTES = 10 * 1024 * 1024
MAX_MEDIA_GROUP_ITEMS = 4
MEDIA_GROUP_SETTLE_SECONDS = 2.0
SUPPORTED_DOCUMENT_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".heic",
    ".heif",
}


@dataclass(frozen=True)
class MediaPayload:
    file_id: str
    file_size: int
    mime_type: str
    telegram_method: str
    telegram_media_key: str
    original_filename: Optional[str] = None


def synced_targets(mapping: Mapping, has_target: HasTarget) -> List[str]:
    targets = []
    if has_target(mapping.get("tg_channel")):
        targets.append("Telegram")
    if has_target(mapping.get("masto")):
        targets.append("Mastodon")
    return targets


def document_image_mime(document: Optional[Mapping]) -> Optional[str]:
    if not document:
        return None

    mime = (document.get("mime_type") or "").lower()
    if mime.startswith("image/") and mime != "image/gif":
        return mime

    file_name = (document.get("file_name") or "").lower()
    for ext in SUPPORTED_DOCUMENT_IMAGE_EXTENSIONS:
        if file_name.endswith(ext):
            if ext in {".png", ".webp", ".heic", ".heif"}:
                return f"image/{ext[1:]}"
            return "image/jpeg"

    return None


def message_text(msg: Mapping) -> str:
    return msg.get("text", msg.get("caption", "")).strip()


def extract_media_payload(msg: Mapping) -> Optional[MediaPayload]:
    photo = msg.get("photo")
    if photo:
        best_photo = photo[-1]
        return MediaPayload(
            file_id=best_photo["file_id"],
            file_size=best_photo.get("file_size", 0),
            mime_type="image/jpeg",
            telegram_method="sendPhoto",
            telegram_media_key="photo",
        )

    document = msg.get("document")
    document_mime = document_image_mime(document)
    if document and document_mime:
        return MediaPayload(
            file_id=document["file_id"],
            file_size=document.get("file_size", 0),
            mime_type=document_mime,
            telegram_method="sendPhoto",
            telegram_media_key="photo",
            original_filename=document.get("file_name"),
        )

    return None


def is_media_message(msg: Mapping) -> bool:
    return extract_media_payload(msg) is not None


def resolve_upload_filename(
    original_filename: Optional[str], file_path: Optional[str]
) -> Optional[str]:
    if original_filename:
        return original_filename
    if file_path:
        return file_path.rsplit("/", 1)[-1]
    return None


def download_media_file(
    file_id: str,
    original_filename: Optional[str],
    get_tg_file_path: Callable[[str], Optional[str]],
    download_tg_file: Callable[[str], Optional[bytes]],
) -> Optional[Dict[str, Any]]:
    file_path = get_tg_file_path(file_id)
    if not file_path:
        return None

    file_content = download_tg_file(file_path)
    if not file_content:
        return None

    return {
        "content": file_content,
        "filename": resolve_upload_filename(original_filename, file_path),
    }


def publish_to_telegram_channel(
    text: str,
    media: Optional[MediaPayload],
    telegram_request: TelegramRequest,
    logger: logging.Logger,
) -> Any:
    if not media:
        return telegram_request(
            "sendMessage",
            {"chat_id": TG_CHANNEL_ID, "text": text, "parse_mode": "HTML"},
        )

    tg_resp = telegram_request(
        media.telegram_method,
        {
            "chat_id": TG_CHANNEL_ID,
            media.telegram_media_key: media.file_id,
            "caption": text,
            "parse_mode": "HTML",
        },
    )

    if tg_resp and tg_resp.ok:
        return tg_resp

    logger.info("直接转发媒体失败，尝试下载后重新上传...")
    from api.clients import TG_API, download_tg_file, get_tg_file_path, req

    downloaded_media = download_media_file(
        media.file_id,
        media.original_filename,
        get_tg_file_path,
        download_tg_file,
    )
    if not downloaded_media:
        return tg_resp

    upload_filename = downloaded_media["filename"] or media.file_id
    files = {
        media.telegram_media_key: (
            upload_filename,
            downloaded_media["content"],
            media.mime_type,
        )
    }
    payload = {
        "chat_id": TG_CHANNEL_ID,
        "caption": text,
        "parse_mode": "HTML",
    }
    return req.post(
        f"{TG_API}/{media.telegram_method}",
        data=payload,
        files=files,
        timeout=30,
    )


def publish_media_group_to_telegram_channel(
    messages: List[Mapping],
    telegram_request: TelegramRequest,
) -> Any:
    media_entries = []
    for index, msg in enumerate(messages):
        media = extract_media_payload(msg)
        if not media:
            return None

        entry: Dict[str, Any] = {
            "type": media.telegram_media_key,
            "media": media.file_id,
        }
        if index == 0:
            caption = message_text(msg)
            if caption:
                entry["caption"] = caption
                entry["parse_mode"] = "HTML"
        media_entries.append(entry)

    return telegram_request(
        "sendMediaGroup",
        {"chat_id": TG_CHANNEL_ID, "media": media_entries},
    )


def upload_media_to_mastodon(
    media: Optional[MediaPayload],
) -> Optional[List[str]]:
    if not media:
        return []

    from api.clients import download_tg_file, get_tg_file_path, upload_mastodon_media

    downloaded_media = download_media_file(
        media.file_id,
        media.original_filename,
        get_tg_file_path,
        download_tg_file,
    )
    if not downloaded_media or not downloaded_media["filename"]:
        return None

    masto_media = upload_mastodon_media(
        downloaded_media["content"],
        downloaded_media["filename"],
        media.mime_type,
    )
    if not masto_media:
        return None
    return [masto_media["id"]]


def publish_to_mastodon_status(
    text: str,
    media_ids: List[str],
    post_to_mastodon: PostToMastodon,
) -> Optional[Mapping]:
    if not media_ids:
        return post_to_mastodon(text)

    import requests as _req
    from api.clients import mastodon_headers

    form_data = [
        ("status", text),
        ("visibility", "public"),
    ]
    for media_id in media_ids:
        form_data.append(("media_ids[]", media_id))

    resp = _req.post(
        f"{MASTO_INSTANCE}/api/v1/statuses",
        headers=mastodon_headers(),
        data=form_data,
        timeout=30,
    )
    if not resp or not resp.ok:
        return None
    return resp.json()


def publish_album_to_mastodon(
    messages: List[Mapping],
    post_to_mastodon: PostToMastodon,
) -> Optional[Mapping]:
    media_ids: List[str] = []
    for msg in messages:
        media_ids_for_message = upload_media_to_mastodon(extract_media_payload(msg))
        if media_ids_for_message is None:
            return None
        media_ids.extend(media_ids_for_message)

    return publish_to_mastodon_status(
        message_text(messages[0]),
        media_ids,
        post_to_mastodon,
    )


def publish_message(
    msg: Mapping,
    send_tg_message: SendMessage,
    edit_message_text: EditMessageText,
    telegram_request: TelegramRequest,
    post_to_mastodon: PostToMastodon,
    save_mapping: SaveMapping,
    logger: logging.Logger,
) -> None:
    text = message_text(msg)
    media = extract_media_payload(msg)

    if media and media.file_size > MAX_MEDIA_SIZE_BYTES:
        send_tg_message(ADMIN_ID, "❌ 附件超过 10MB 限制，无法发布")
        return

    logger.info(f"开始发布消息 (含附件: {bool(media)})")
    status_message = send_tg_message(ADMIN_ID, SYNCING_TEXT, reply_to=msg["message_id"])
    status_message_id = None
    if status_message:
        status_message_id = status_message.get("result", {}).get("message_id")

    def finish(result_text: str) -> None:
        if status_message_id and edit_message_text(
            ADMIN_ID, status_message_id, result_text
        ):
            return
        send_tg_message(ADMIN_ID, result_text, reply_to=msg["message_id"])

    def finish_partial_publish(log_message: str) -> None:
        logger.error(log_message)
        save_mapping(msg["message_id"], tg_channel_msg_id, None)
        finish(PARTIAL_PUBLISH_TEXT)

    tg_resp = publish_to_telegram_channel(text, media, telegram_request, logger)
    if not tg_resp or not tg_resp.ok:
        error_text = tg_resp.text if tg_resp else "request failed"
        logger.error(f"Telegram 发布失败：{error_text}")
        finish("❌ <b>发布失败</b>\n\nTelegram 频道发送失败")
        return

    tg_channel_msg_id = tg_resp.json()["result"]["message_id"]
    logger.info(f"Telegram 发布成功：msg_id={tg_channel_msg_id}")

    media_ids = upload_media_to_mastodon(media)
    if media and media_ids is None:
        finish_partial_publish("Mastodon 媒体上传失败: media upload failed")
        return

    masto_data = publish_to_mastodon_status(text, media_ids, post_to_mastodon)
    if not masto_data:
        finish_partial_publish("Mastodon 状态发布失败: no response")
        return

    masto_status_id = masto_data["id"]
    logger.info(f"Mastodon 发布成功：status_id={masto_status_id}")
    save_mapping(msg["message_id"], tg_channel_msg_id, masto_status_id)
    finish(PUBLISH_SUCCESS_TEXT)


def handle_media_group_message(
    msg: Mapping,
    send_tg_message: SendMessage,
    edit_message_text: EditMessageText,
    telegram_request: TelegramRequest,
    post_to_mastodon: PostToMastodon,
    save_mapping: SaveMapping,
    save_pending_media_group_item: SavePendingMediaGroupItem,
    get_pending_media_group_items: GetPendingMediaGroupItems,
    delete_pending_media_group_items: DeletePendingMediaGroupItems,
    logger: logging.Logger,
) -> None:
    media_group_id = msg.get("media_group_id")
    if not media_group_id or not is_media_message(msg):
        warning_text = unsupported_message_text(msg)
        if warning_text:
            send_tg_message(ADMIN_ID, warning_text)
        return

    save_pending_media_group_item(media_group_id, msg["message_id"], dict(msg))


def process_pending_media_group(
    msg: Mapping,
    send_tg_message: SendMessage,
    edit_message_text: EditMessageText,
    telegram_request: TelegramRequest,
    post_to_mastodon: PostToMastodon,
    save_mapping: SaveMapping,
    pop_ready_pending_media_group_items: PopReadyPendingMediaGroupItems,
    logger: logging.Logger,
) -> None:
    media_group_id = msg.get("media_group_id")
    if not media_group_id:
        return

    time.sleep(MEDIA_GROUP_SETTLE_SECONDS)

    grouped_messages = pop_ready_pending_media_group_items(
        media_group_id, int(MEDIA_GROUP_SETTLE_SECONDS)
    )
    if not grouped_messages:
        return
    grouped_messages.sort(key=lambda item: item["message_id"])

    if len(grouped_messages) > MAX_MEDIA_GROUP_ITEMS:
        send_tg_message(
            ADMIN_ID,
            "❌ 不支持超过 4 张图片的相册消息\n\n"
            "Mastodon 最多只支持 4 张图片，请减少到 4 张或更少后再发送。",
            reply_to=grouped_messages[0]["message_id"],
        )
        return

    for item in grouped_messages:
        media = extract_media_payload(item)
        if not media:
            send_tg_message(
                ADMIN_ID,
                "❌ 相册中包含不支持的内容\n\n仅支持静态图片。",
                reply_to=grouped_messages[0]["message_id"],
            )
            return
        if media.file_size > MAX_MEDIA_SIZE_BYTES:
            send_tg_message(
                ADMIN_ID,
                "❌ 相册中存在超过 10MB 的附件，无法发布",
                reply_to=grouped_messages[0]["message_id"],
            )
            return

    status_message = send_tg_message(
        ADMIN_ID, SYNCING_TEXT, reply_to=grouped_messages[0]["message_id"]
    )
    status_message_id = None
    if status_message:
        status_message_id = status_message.get("result", {}).get("message_id")

    def finish(result_text: str) -> None:
        if status_message_id and edit_message_text(
            ADMIN_ID, status_message_id, result_text
        ):
            return
        send_tg_message(
            ADMIN_ID, result_text, reply_to=grouped_messages[0]["message_id"]
        )

    tg_resp = publish_media_group_to_telegram_channel(grouped_messages, telegram_request)
    if not tg_resp or not tg_resp.ok:
        error_text = tg_resp.text if tg_resp else "request failed"
        logger.error(f"Telegram 相册发布失败：{error_text}")
        finish("❌ <b>发布失败</b>\n\nTelegram 频道发送失败")
        return

    tg_results = tg_resp.json()["result"]
    tg_message_ids = [item["message_id"] for item in tg_results]

    masto_data = publish_album_to_mastodon(grouped_messages, post_to_mastodon)
    if not masto_data:
        logger.error("Mastodon 相册发布失败: no response")
        for source_message_id, tg_message_id in zip(
            [item["message_id"] for item in grouped_messages], tg_message_ids
        ):
            save_mapping(
                source_message_id,
                tg_message_id,
                None,
                tg_channel_message_ids=tg_message_ids,
                media_group_id=media_group_id,
            )
        finish(PARTIAL_PUBLISH_TEXT)
        return

    masto_status_id = masto_data["id"]
    for source_message_id, tg_message_id in zip(
        [item["message_id"] for item in grouped_messages], tg_message_ids
    ):
        save_mapping(
            source_message_id,
            tg_message_id,
            masto_status_id,
            tg_channel_message_ids=tg_message_ids,
            media_group_id=media_group_id,
        )

    finish(PUBLISH_SUCCESS_TEXT)


def edit_message(
    msg: Mapping,
    send_tg_message: SendMessage,
    get_mapping: GetMapping,
    has_target: HasTarget,
    edit_tg_message: EditTelegramMessage,
    edit_mastodon_status: EditMastodonStatus,
) -> None:
    source_msg_id = msg["message_id"]
    new_text = message_text(msg)
    if not new_text:
        send_tg_message(ADMIN_ID, "❌ 编辑后的内容为空")
        return

    mapping = get_mapping(source_msg_id)
    if not mapping:
        send_tg_message(ADMIN_ID, "❌ 未找到原消息的映射记录，无法编辑")
        return

    tg_ok = True
    if has_target(mapping.get("tg_channel")):
        if is_media_message(msg):
            from api.clients import edit_tg_message_caption
            tg_ok = edit_tg_message_caption(TG_CHANNEL_ID, mapping["tg_channel"], new_text)
        else:
            tg_ok = edit_tg_message(TG_CHANNEL_ID, mapping["tg_channel"], new_text)

    masto_ok = True
    if has_target(mapping.get("masto")):
        masto_ok = edit_mastodon_status(mapping["masto"], new_text)

    if tg_ok and masto_ok:
        target_text = "、".join(synced_targets(mapping, has_target)) or "已同步的平台"
        send_tg_message(
            ADMIN_ID,
            f"✅ <b>编辑成功</b>\n\n已同步更新到：\n• {target_text}",
            reply_to=source_msg_id,
        )
        return

    errors = []
    if not tg_ok:
        errors.append("Telegram")
    if not masto_ok:
        errors.append("Mastodon")
    send_tg_message(ADMIN_ID, f'❌ 编辑失败：{", ".join(errors)}')


def delete_message(
    msg: Mapping,
    send_tg_message: SendMessage,
    get_mapping: GetMapping,
    has_target: HasTarget,
    delete_tg_message: DeleteTelegramMessage,
    delete_mastodon_status: DeleteMastodonStatus,
    delete_mapping: DeleteMapping,
) -> None:
    reply_to = msg.get("reply_to_message")
    if not reply_to:
        send_tg_message(ADMIN_ID, "❌ 请回复要删除的消息后使用 /delete 命令")
        return

    mapping = get_mapping(reply_to["message_id"])
    if not mapping:
        send_tg_message(ADMIN_ID, "❌ 未找到原消息的映射记录，无法删除")
        return

    source_msg_id = mapping["source"]
    targets = synced_targets(mapping, has_target)

    tg_message_ids = mapping.get("tg_channel_messages") or []
    tg_ok = True
    if has_target(mapping.get("tg_channel")):
        if tg_message_ids:
            tg_results = [
                delete_tg_message(TG_CHANNEL_ID, tg_message_id)
                for tg_message_id in tg_message_ids
            ]
            tg_ok = all(tg_results)
        else:
            tg_ok = delete_tg_message(TG_CHANNEL_ID, mapping["tg_channel"])

    masto_ok = True
    if has_target(mapping.get("masto")):
        masto_ok = delete_mastodon_status(mapping["masto"])

    delete_tg_message(ADMIN_ID, source_msg_id)
    delete_tg_message(ADMIN_ID, msg["message_id"])

    if tg_ok and masto_ok:
        delete_mapping(source_msg_id)
        target_text = "、".join(targets) if targets else "已同步的平台"
        send_tg_message(
            ADMIN_ID, f"✅ <b>删除成功</b>\n\n已从以下平台删除此消息：\n• {target_text}"
        )
        return

    errors = []
    if not tg_ok:
        errors.append("Telegram")
    if not masto_ok:
        errors.append("Mastodon")
    send_tg_message(ADMIN_ID, f'⚠️ 部分删除失败：{", ".join(errors)}')


def is_supported_message(msg: Mapping) -> bool:
    if "forward_from" in msg or "forward_from_chat" in msg:
        return False

    # 1. 允许纯文本
    if "text" in msg:
        return True

    # 2. 允许图片
    if "photo" in msg:
        return True

    # 3. 允许作为文档发送的图片
    if "document" in msg:
        if document_image_mime(msg["document"]):
            return True

    # 4. 排除其他不支持的类型
    other_media = ["video", "audio", "voice", "sticker", "video_note"]
    if any(k in msg for k in other_media):
        return False

    return False


def unsupported_message_text(msg: Mapping) -> Optional[str]:
    if "forward_from" in msg or "forward_from_chat" in msg:
        return "❌ 不支持转发消息\n\n" "请直接发送原创内容，不要转发其他聊天中的消息。"

    if "media_group_id" in msg:
        return None

    if "document" in msg:
        if not document_image_mime(msg["document"]):
            return "❌ 不支持的文件类型\n\n仅支持作为文件发送的静态图片 (JPG, PNG, WebP, HEIC, HEIF等)。"

    other_media = [
        "animation",
        "video",
        "audio",
        "voice",
        "sticker",
    ]
    if any(k in msg for k in other_media):
        return (
            "❌ 不支持的内容类型\n\n"
            "此机器人目前仅支持纯文本和静态图片。\n"
            "暂不支持视频、语音等其他媒体。"
        )
    return None
