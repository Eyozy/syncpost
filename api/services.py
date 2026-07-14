import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from collections.abc import Mapping as MappingABC
from typing import Any, Callable, Dict, List, Optional

from api.config import ADMIN_ID, MASTO_INSTANCE, TG_CHANNEL_ID, MEDIA_GROUP_SETTLE_SECONDS
from api.messages import PARTIAL_PUBLISH_TEXT, PUBLISH_SUCCESS_TEXT, SYNCING_TEXT

Mapping = Dict[str, Any]
SendMessage = Callable[[int, str, Optional[int]], Optional[Dict[str, Any]]]
EditMessageText = Callable[[int, int, str], bool]
TelegramRequest = Callable[[str, Dict[str, Any]], Any]
PostToMastodon = Callable[[str, Optional[str]], Optional[Dict[str, Any]]]
SaveMapping = Callable[..., None]
SavePrivateMessageAlias = Callable[[int, int], None]
SavePendingMediaGroupItem = Callable[[str, int, Dict[str, Any]], bool]

GetPendingMediaGroupItems = Callable[[str], List[Dict[str, Any]]]
DeletePendingMediaGroupItems = Callable[[str], None]
PopReadyPendingMediaGroupItems = Callable[[str, int], List[Dict[str, Any]]]
TouchMediaGroupState = Callable[[str, int, int], bool]
GetMediaGroupState = Callable[[str], Optional[Dict[str, Any]]]
BumpMediaGroupStableCheck = Callable[[str], Optional[int]]
MarkMediaGroupPublished = Callable[[str], None]
DeleteMediaGroupState = Callable[[str], None]
GetMapping = Callable[[int], Optional[Mapping]]
ResolveSourceMessageId = Callable[[int], int]
GetMappingByMediaGroupId = Callable[[str], Optional[Mapping]]
GetMappingsByMediaGroupId = Callable[[str], List[Mapping]]
GetMediaGroupSourceMessageIds = Callable[[str], List[int]]
CancelJobsForSourceMessage = Callable[[int], int]
CancelJobsForMediaGroup = Callable[[str], int]
HasTarget = Callable[[Optional[str]], bool]
EditTelegramMessage = Callable[[str, int, str], bool]
EditMastodonStatus = Callable[[str, str], bool]
DeleteTelegramMessage = Callable[[str, int], bool]
DeleteTelegramMessages = Callable[[int, List[int]], bool]
DeleteMastodonStatus = Callable[[str], bool]
DeleteMapping = Callable[[int], None]
DeletePendingMediaGroupItems = Callable[[str], None]
EnqueueJob = Callable[[str, Dict[str, Any], Optional[str], int], bool]

MAX_MEDIA_SIZE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_SIZE_BYTES = 20 * 1024 * 1024
VIDEO_SOURCE_KINDS = {"video", "video_document"}
IMAGE_SOURCE_KINDS = {"photo", "document_image"}
MAX_MEDIA_GROUP_ITEMS = 4
MEDIA_GROUP_READY_AGE_SECONDS = max(3, int(MEDIA_GROUP_SETTLE_SECONDS))
MEDIA_GROUP_REQUIRED_STABLE_CHECKS = 2
SUPPORTED_DOCUMENT_IMAGE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".jfif",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
    ".heic",
    ".heif",
}
SUPPORTED_DOCUMENT_VIDEO_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mov"}


@dataclass(frozen=True)
class MediaPayload:
    file_id: str
    file_size: int
    mime_type: str
    source_kind: str
    original_filename: Optional[str] = None


def is_video_media(media: Optional[MediaPayload]) -> bool:
    return bool(media and media.source_kind in VIDEO_SOURCE_KINDS)


def is_image_media(media: Optional[MediaPayload]) -> bool:
    return bool(media and media.source_kind in IMAGE_SOURCE_KINDS)


def mastodon_video_size_limit() -> int:
    from api.clients import get_mastodon_video_size_limit

    mastodon_limit = get_mastodon_video_size_limit()
    if not isinstance(mastodon_limit, int) or mastodon_limit <= 0:
        return MAX_VIDEO_SIZE_BYTES
    return min(MAX_VIDEO_SIZE_BYTES, mastodon_limit)


def video_size_error(file_size: int, limit: int, mastodon_limit: Optional[int] = None) -> str:
    if isinstance(mastodon_limit, int) and mastodon_limit < MAX_VIDEO_SIZE_BYTES:
        limit_text = f"当前 Mastodon 实例最多支持 <b>{limit / (1024 * 1024):.0f}MB</b> 的视频。"
    else:
        limit_text = f"当前同步链路最多支持 <b>{limit / (1024 * 1024):.0f}MB</b> 的视频。"
    return (
        "⚠️ <b>视频文件过大</b>\n\n"
        f"{limit_text}\n"
        f"当前视频大小为 <b>{file_size / (1024 * 1024):.1f}MB</b>，已超出限制。\n\n"
        "请压缩视频后重新发送。"
    )


def synced_targets(mapping: Mapping, has_target: HasTarget) -> List[str]:
    targets = []
    if has_target(mapping.get("tg_channel")):
        targets.append("Telegram")
    if has_target(mapping.get("masto")):
        targets.append("Mastodon")
    return targets


def reply_targets_for_message(
    msg: Mapping,
    get_mapping: GetMapping,
    resolve_source_message_id: Optional[ResolveSourceMessageId] = None,
) -> Dict[str, Optional[Any]]:
    reply_to = msg.get("reply_to_message")
    if not reply_to:
        return {"telegram_reply_to": None, "mastodon_reply_to": None}

    reply_message_id = reply_to.get("message_id")
    if reply_message_id is None:
        return {"telegram_reply_to": None, "mastodon_reply_to": None}

    source_message_id = (
        resolve_source_message_id(reply_message_id)
        if resolve_source_message_id
        else reply_message_id
    )
    mapping = get_mapping(source_message_id)
    logging.getLogger(__name__).info(
        "回复目标解析：reply_message_id=%s resolved_source=%s mapping_found=%s",
        reply_message_id,
        source_message_id,
        bool(mapping),
    )
    if not mapping:
        return {"telegram_reply_to": None, "mastodon_reply_to": None}

    return {
        "telegram_reply_to": mapping.get("tg_channel"),
        "mastodon_reply_to": mapping.get("masto"),
    }


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


def document_video_mime(document: Optional[Mapping]) -> Optional[str]:
    if not document:
        return None
    mime = (document.get("mime_type") or "").lower()
    if mime.startswith("video/"):
        return mime
    file_name = (document.get("file_name") or "").lower()
    for ext in SUPPORTED_DOCUMENT_VIDEO_EXTENSIONS:
        if file_name.endswith(ext):
            return "video/mp4" if ext in {".mp4", ".m4v"} else f"video/{ext[1:]}"
    return None


def message_text(msg: Mapping) -> str:
    return msg.get("text", msg.get("caption", "")).strip()


def media_group_caption(messages: List[Mapping]) -> str:
    for msg in messages:
        text = message_text(msg)
        if text:
            return text
    return ""


def extract_media_payload(msg: Mapping) -> Optional[MediaPayload]:
    photo = msg.get("photo")
    if photo:
        best_photo = photo[-1]
        return MediaPayload(
            file_id=best_photo["file_id"],
            file_size=best_photo.get("file_size", 0),
            mime_type="image/jpeg",
            source_kind="photo",
        )

    video = msg.get("video")
    if video:
        return MediaPayload(
            file_id=video["file_id"],
            file_size=video.get("file_size", 0),
            mime_type=video.get("mime_type") or "video/mp4",
            source_kind="video",
            original_filename=video.get("file_name"),
        )

    document = msg.get("document")
    document_video = document_video_mime(document)
    if document and document_video:
        return MediaPayload(
            file_id=document["file_id"],
            file_size=document.get("file_size", 0),
            mime_type=document_video,
            source_kind="video_document",
            original_filename=document.get("file_name"),
        )
    document_mime = document_image_mime(document)
    if document and document_mime:
        return MediaPayload(
            file_id=document["file_id"],
            file_size=document.get("file_size", 0),
            mime_type=document_mime,
            source_kind="document_image",
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
    reply_to_message_id: Optional[int] = None,
    downloaded_media: Optional[Mapping] = None,
) -> Any:
    if not media:
        payload = {"chat_id": TG_CHANNEL_ID, "text": text, "parse_mode": "HTML"}
        if reply_to_message_id:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        return telegram_request("sendMessage", payload)

    if media.source_kind in {"photo", "video"}:
        method = "sendVideo" if media.source_kind == "video" else "sendPhoto"
        field = "video" if media.source_kind == "video" else "photo"
        payload = {
            "chat_id": TG_CHANNEL_ID,
            field: media.file_id,
            "caption": text,
            "parse_mode": "HTML",
        }
        if media.source_kind == "video":
            payload["supports_streaming"] = True
        if reply_to_message_id:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        tg_resp = telegram_request(
            method,
            payload,
        )

        if tg_resp and tg_resp.ok:
            return tg_resp

    logger.info("直接转发媒体失败，尝试下载后重新上传...")
    from api.clients import TG_API, download_tg_file, get_tg_file_path, req

    downloaded_media = downloaded_media or download_media_file(
        media.file_id,
        media.original_filename,
        get_tg_file_path,
        download_tg_file,
    )
    if not downloaded_media:
        return tg_resp if media.source_kind in {"photo", "video"} else None

    upload_filename = downloaded_media["filename"] or media.file_id
    is_video = media.source_kind in {"video", "video_document"}
    upload_field = "video" if is_video else "photo"
    files = {
        upload_field: (
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
    if is_video:
        payload["supports_streaming"] = "true"
    if reply_to_message_id:
        payload["reply_parameters"] = json.dumps({"message_id": reply_to_message_id})
    return req.post(
        f"{TG_API}/{'sendVideo' if is_video else 'sendPhoto'}",
        data=payload,
        files=files,
        timeout=30,
    )


def publish_media_group_to_telegram_channel(
    messages: List[Mapping],
    telegram_request: TelegramRequest,
    reply_to_message_id: Optional[int] = None,
) -> Any:
    from api.clients import TG_API, download_tg_file, get_tg_file_path, req

    media_entries = []
    files: Dict[str, Any] = {}
    caption_text = media_group_caption(messages)

    for index, msg in enumerate(messages):
        media = extract_media_payload(msg)
        if not media:
            return None

        if media.source_kind == "photo":
            entry: Dict[str, Any] = {
                "type": "photo",
                "media": media.file_id,
            }
        else:
            downloaded_media = download_media_file(
                media.file_id,
                media.original_filename,
                get_tg_file_path,
                download_tg_file,
            )
            if not downloaded_media:
                return None

            upload_name = downloaded_media["filename"] or media.file_id
            attach_name = f"file{index}"
            files[attach_name] = (
                upload_name,
                downloaded_media["content"],
                media.mime_type,
            )
            entry = {
                "type": "photo",
                "media": f"attach://{attach_name}",
            }

        if index == 0 and caption_text:
            entry["caption"] = caption_text
            entry["parse_mode"] = "HTML"
        media_entries.append(entry)

    if not files:
        payload = {"chat_id": TG_CHANNEL_ID, "media": media_entries}
        if reply_to_message_id:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        return telegram_request("sendMediaGroup", payload)

    data = {"chat_id": TG_CHANNEL_ID, "media": json.dumps(media_entries)}
    if reply_to_message_id:
        data["reply_parameters"] = json.dumps({"message_id": reply_to_message_id})

    return req.post(
        f"{TG_API}/sendMediaGroup",
        data=data,
        files=files,
        timeout=30,
    )


def upload_media_to_mastodon(
    media: Optional[MediaPayload],
    downloaded_media: Optional[Mapping] = None,
) -> Optional[List[str]]:
    if not media:
        return []

    from api.clients import download_tg_file, get_tg_file_path, upload_mastodon_media

    downloaded_media = downloaded_media or download_media_file(
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
    in_reply_to_id: Optional[str] = None,
) -> Optional[Mapping]:
    if not media_ids:
        if in_reply_to_id:
            try:
                return post_to_mastodon(text, in_reply_to_id)
            except TypeError:
                return post_to_mastodon(text)
        return post_to_mastodon(text)

    import requests as _req
    from api.clients import mastodon_headers

    form_data = [
        ("status", text),
        ("visibility", "public"),
    ]
    if in_reply_to_id:
        form_data.append(("in_reply_to_id", in_reply_to_id))
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
    in_reply_to_id: Optional[str] = None,
) -> Optional[Mapping]:
    media_ids: List[str] = []
    for msg in messages:
        media_ids_for_message = upload_media_to_mastodon(extract_media_payload(msg))
        if media_ids_for_message is None:
            return None
        media_ids.extend(media_ids_for_message)

    return publish_to_mastodon_status(
        media_group_caption(messages),
        media_ids,
        post_to_mastodon,
        in_reply_to_id,
    )


def publish_message(
    msg: Mapping,
    send_tg_message: SendMessage,
    edit_message_text: EditMessageText,
    telegram_request: TelegramRequest,
    post_to_mastodon: PostToMastodon,
    save_mapping: SaveMapping,
    logger: logging.Logger,
    get_mapping: Optional[GetMapping] = None,
    resolve_source_message_id: Optional[ResolveSourceMessageId] = None,
    save_private_message_alias: Optional[SavePrivateMessageAlias] = None,
) -> None:
    text = message_text(msg)
    media = extract_media_payload(msg)
    reply_targets = (
        reply_targets_for_message(msg, get_mapping, resolve_source_message_id)
        if get_mapping
        else {"telegram_reply_to": None, "mastodon_reply_to": None}
    )

    video_limit = mastodon_video_size_limit()
    if is_video_media(media) and media.file_size > video_limit:
        from api.clients import get_mastodon_video_size_limit

        send_tg_message(
            ADMIN_ID,
            video_size_error(media.file_size, video_limit, get_mastodon_video_size_limit()),
        )
        return
    if media and not is_video_media(media) and media.file_size > MAX_MEDIA_SIZE_BYTES:
        send_tg_message(ADMIN_ID, "❌ 附件超过 10MB 限制，无法发布")
        return

    logger.info(f"开始发布消息 (含附件: {bool(media)})")
    status_message = send_tg_message(ADMIN_ID, SYNCING_TEXT, reply_to=msg["message_id"])
    status_message_id = None
    if status_message:
        status_message_id = status_message.get("result", {}).get("message_id")
    logger.info(
        "发布状态消息：source=%s status_message_id=%s",
        msg["message_id"],
        status_message_id,
    )
    if save_private_message_alias and status_message_id:
        save_private_message_alias(status_message_id, msg["message_id"])

    def finish(result_text: str) -> None:
        if status_message_id and edit_message_text(
            ADMIN_ID, status_message_id, result_text
        ):
            if save_private_message_alias:
                save_private_message_alias(status_message_id, msg["message_id"])
            logger.info(
                "发布结果通过编辑状态消息返回：source=%s status_message_id=%s",
                msg["message_id"],
                status_message_id,
            )
            return
        if status_message_id:
            from api.clients import delete_tg_message

            delete_tg_message(ADMIN_ID, status_message_id)
        alias_message = send_tg_message(ADMIN_ID, result_text, reply_to=msg["message_id"])
        if save_private_message_alias and alias_message:
            alias_message_id = alias_message.get("result", {}).get("message_id")
            if alias_message_id:
                save_private_message_alias(alias_message_id, msg["message_id"])
        logger.info(
            "发布结果通过新消息返回：source=%s alias_message_id=%s",
            msg["message_id"],
            alias_message.get("result", {}).get("message_id") if alias_message else None,
        )

    def finish_partial_publish(log_message: str) -> None:
        logger.error(log_message)
        save_mapping(msg["message_id"], tg_channel_msg_id, None)
        finish(PARTIAL_PUBLISH_TEXT)

    downloaded_media = None
    if media and media.source_kind in {"document_image", "video_document"}:
        from api.clients import download_tg_file, get_tg_file_path

        downloaded_media = download_media_file(
            media.file_id,
            media.original_filename,
            get_tg_file_path,
            download_tg_file,
        )
        if not downloaded_media:
            finish("❌ <b>发布失败</b>\n\n媒体文件下载失败")
            return

    if media:
        with ThreadPoolExecutor(max_workers=2) as executor:
            tg_future = executor.submit(
                publish_to_telegram_channel,
                text,
                media,
                telegram_request,
                logger,
                reply_targets["telegram_reply_to"],
                downloaded_media,
            )
            mastodon_media_future = executor.submit(
                upload_media_to_mastodon,
                media,
                downloaded_media,
            )
            tg_resp = tg_future.result()
            media_ids = mastodon_media_future.result()
    else:
        tg_resp = publish_to_telegram_channel(
            text,
            media,
            telegram_request,
            logger,
            reply_to_message_id=reply_targets["telegram_reply_to"],
        )
        media_ids = []

    if not tg_resp or not tg_resp.ok:
        error_text = tg_resp.text if tg_resp else "request failed"
        logger.error(f"Telegram 发布失败：{error_text}")
        finish("❌ <b>发布失败</b>\n\nTelegram 频道发送失败")
        return

    tg_channel_msg_id = tg_resp.json()["result"]["message_id"]
    logger.info(f"Telegram 发布成功：msg_id={tg_channel_msg_id}")

    if media and media_ids is None:
        finish_partial_publish("Mastodon 媒体上传失败: media upload failed")
        return

    masto_data = publish_to_mastodon_status(
        text,
        media_ids,
        post_to_mastodon,
        in_reply_to_id=reply_targets["mastodon_reply_to"],
    )
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
    touch_media_group_state: Optional[TouchMediaGroupState] = None,
) -> None:
    media_group_id = msg.get("media_group_id")
    if not media_group_id or not is_media_message(msg):
        warning_text = unsupported_message_text(msg)
        if warning_text:
            send_tg_message(ADMIN_ID, warning_text)
        return

    saved = save_pending_media_group_item(media_group_id, msg["message_id"], dict(msg))
    if not saved:
        send_tg_message(
            ADMIN_ID,
            "❌ 相册暂存失败\n\n多图同步依赖数据库暂存图片分组，当前数据库不可用或写入失败。",
            reply_to=msg["message_id"],
        )
        return

    touch_state = touch_media_group_state or (
        lambda group_id, source_message_id, settle_seconds: True
    )
    if not touch_state(media_group_id, msg["message_id"], int(MEDIA_GROUP_SETTLE_SECONDS)):
        delete_pending_media_group_items(media_group_id)
        send_tg_message(
            ADMIN_ID,
            "❌ 相册状态初始化失败\n\n多图同步依赖数据库状态跟踪，当前数据库不可用或写入失败。",
            reply_to=msg["message_id"],
        )
        return

    logger.info(
        "相册消息已暂存：media_group_id=%s source_message_id=%s",
        media_group_id,
        msg["message_id"],
    )


def enqueue_publish_message(
    msg: Mapping,
    send_tg_message: SendMessage,
    enqueue_job: EnqueueJob,
) -> None:
    if not enqueue_job("publish_message", dict(msg), None, 0):
        send_tg_message(ADMIN_ID, "❌ 消息入队失败，无法开始同步", reply_to=msg["message_id"])


def enqueue_media_group_processing(
    msg: Mapping,
    send_tg_message: SendMessage,
    enqueue_job: EnqueueJob,
) -> None:
    media_group_id = msg.get("media_group_id")
    if not media_group_id:
        return

    queued = enqueue_job(
        "process_media_group",
        {"message": dict(msg), "expected_latest_message_id": msg["message_id"]},
        f"media_group:{media_group_id}",
        int(MEDIA_GROUP_SETTLE_SECONDS),
    )
    if not queued:
        send_tg_message(ADMIN_ID, "❌ 相册任务入队失败，无法开始同步", reply_to=msg["message_id"])
        return

    logging.getLogger(__name__).info(
        "相册任务已入队：media_group_id=%s source_message_id=%s",
        media_group_id,
        msg["message_id"],
    )


def enqueue_delete_message(
    msg: Mapping,
    send_tg_message: SendMessage,
    enqueue_job: EnqueueJob,
) -> None:
    if not enqueue_job("delete_message", dict(msg), None, 0):
        send_tg_message(ADMIN_ID, "❌ 删除任务入队失败", reply_to=msg["message_id"])


def process_pending_media_group(
    msg: Mapping,
    send_tg_message: SendMessage,
    edit_message_text: EditMessageText,
    telegram_request: TelegramRequest,
    post_to_mastodon: PostToMastodon,
    save_mapping: SaveMapping,
    get_pending_media_group_items: GetPendingMediaGroupItems,
    pop_ready_pending_media_group_items: PopReadyPendingMediaGroupItems,
    logger: logging.Logger,
    expected_latest_message_id: Optional[int] = None,
    get_media_group_state: Optional[GetMediaGroupState] = None,
    bump_media_group_stable_check: Optional[BumpMediaGroupStableCheck] = None,
    mark_media_group_published: Optional[MarkMediaGroupPublished] = None,
    delete_media_group_state: Optional[DeleteMediaGroupState] = None,
    get_mapping: Optional[GetMapping] = None,
    resolve_source_message_id: Optional[ResolveSourceMessageId] = None,
    save_private_message_alias: Optional[SavePrivateMessageAlias] = None,
) -> bool:
    media_group_id = msg.get("media_group_id")
    if not media_group_id:
        return True

    pending_messages = get_pending_media_group_items(media_group_id)
    if len(pending_messages) < 2:
        logger.info("相册 %s 尚未收齐，当前仅 %s 条，稍后重试", media_group_id, len(pending_messages))
        return False
    pending_messages.sort(key=lambda item: item["message_id"])
    latest_message_id = pending_messages[-1]["message_id"]
    state = get_media_group_state(media_group_id) if get_media_group_state else None
    state_latest_message_id = (
        (state.get("latest_source_message_id") if state else None) or msg["message_id"]
    )
    expected_latest_message_id = expected_latest_message_id or state_latest_message_id
    if latest_message_id != expected_latest_message_id or latest_message_id != state_latest_message_id:
        logger.info(
            "相册 %s 最新消息尚未稳定，任务期望=%s，状态最新=%s，当前最新=%s，稍后重试",
            media_group_id,
            expected_latest_message_id,
            state_latest_message_id,
            latest_message_id,
        )
        return False

    if bump_media_group_stable_check:
        stable_checks = bump_media_group_stable_check(media_group_id)
        if stable_checks is None:
            return False
        if stable_checks < MEDIA_GROUP_REQUIRED_STABLE_CHECKS:
            logger.info(
                "相册 %s 已到静默窗口，但仅完成第 %s/%s 次稳定确认，稍后重试",
                media_group_id,
                stable_checks,
                MEDIA_GROUP_REQUIRED_STABLE_CHECKS,
            )
            return False

    grouped_messages = pop_ready_pending_media_group_items(
        media_group_id, MEDIA_GROUP_READY_AGE_SECONDS
    )
    if not grouped_messages:
        logger.info("相册 %s 尚未达到静默窗口，稍后重试", media_group_id)
        return False
    grouped_messages.sort(key=lambda item: item["message_id"])

    group_media = [extract_media_payload(item) for item in grouped_messages]
    video_count = sum(is_video_media(media) for media in group_media)
    if video_count:
        message = (
            "⚠️ <b>不支持混合媒体相册</b>\n\n"
            "视频不能与图片组合发送，请单独发送一个视频。"
            if video_count < len(grouped_messages)
            else "⚠️ <b>暂不支持视频相册</b>\n\n"
            "一次只能发送一个视频文件，请不要同时发送两个或以上视频。"
        )
        send_tg_message(ADMIN_ID, message, reply_to=grouped_messages[0]["message_id"])
        if delete_media_group_state:
            delete_media_group_state(media_group_id)
        return True

    if len(grouped_messages) > MAX_MEDIA_GROUP_ITEMS:
        send_tg_message(
            ADMIN_ID,
            "❌ 不支持超过 4 张图片的相册消息\n\n"
            "Mastodon 最多只支持 4 张图片，请减少到 4 张或更少后再发送。",
            reply_to=grouped_messages[0]["message_id"],
        )
        if delete_media_group_state:
            delete_media_group_state(media_group_id)
        return True

    for item in grouped_messages:
        media = extract_media_payload(item)
        if not media:
            send_tg_message(
                ADMIN_ID,
                "❌ 相册中包含不支持的内容\n\n仅支持静态图片。",
                reply_to=grouped_messages[0]["message_id"],
            )
            if delete_media_group_state:
                delete_media_group_state(media_group_id)
            return True
        if media.file_size > MAX_MEDIA_SIZE_BYTES:
            send_tg_message(
                ADMIN_ID,
                "❌ 相册中存在超过 10MB 的附件，无法发布",
                reply_to=grouped_messages[0]["message_id"],
            )
            if delete_media_group_state:
                delete_media_group_state(media_group_id)
            return True

    reply_targets = (
        reply_targets_for_message(
            grouped_messages[0],
            get_mapping,
            resolve_source_message_id,
        )
        if get_mapping
        else {"telegram_reply_to": None, "mastodon_reply_to": None}
    )
    status_message = send_tg_message(
        ADMIN_ID, SYNCING_TEXT, reply_to=grouped_messages[0]["message_id"]
    )
    status_message_id = None
    if status_message:
        status_message_id = status_message.get("result", {}).get("message_id")
    logger.info(
        "相册发布状态消息：source=%s status_message_id=%s",
        grouped_messages[0]["message_id"],
        status_message_id,
    )
    if save_private_message_alias and status_message_id:
        save_private_message_alias(status_message_id, grouped_messages[0]["message_id"])

    def finish(result_text: str) -> None:
        if status_message_id and edit_message_text(
            ADMIN_ID, status_message_id, result_text
        ):
            if save_private_message_alias:
                save_private_message_alias(status_message_id, grouped_messages[0]["message_id"])
            logger.info(
                "相册发布结果通过编辑状态消息返回：source=%s status_message_id=%s",
                grouped_messages[0]["message_id"],
                status_message_id,
            )
            return
        if status_message_id:
            from api.clients import delete_tg_message

            delete_tg_message(ADMIN_ID, status_message_id)
        alias_message = send_tg_message(
            ADMIN_ID, result_text, reply_to=grouped_messages[0]["message_id"]
        )
        if save_private_message_alias and alias_message:
            alias_message_id = alias_message.get("result", {}).get("message_id")
            if alias_message_id:
                save_private_message_alias(alias_message_id, grouped_messages[0]["message_id"])
        logger.info(
            "相册发布结果通过新消息返回：source=%s alias_message_id=%s",
            grouped_messages[0]["message_id"],
            alias_message.get("result", {}).get("message_id") if alias_message else None,
        )

    if reply_targets["telegram_reply_to"]:
        try:
            tg_resp = publish_media_group_to_telegram_channel(
                grouped_messages,
                telegram_request,
                reply_to_message_id=reply_targets["telegram_reply_to"],
            )
        except TypeError:
            tg_resp = publish_media_group_to_telegram_channel(
                grouped_messages,
                telegram_request,
            )
    else:
        tg_resp = publish_media_group_to_telegram_channel(
            grouped_messages,
            telegram_request,
        )
    if not tg_resp or not tg_resp.ok:
        error_text = tg_resp.text if tg_resp else "request failed"
        logger.error(f"Telegram 相册发布失败：{error_text}")
        finish("❌ <b>发布失败</b>\n\nTelegram 频道发送失败")
        if delete_media_group_state:
            delete_media_group_state(media_group_id)
        return True

    tg_results = tg_resp.json()["result"]
    tg_message_ids = [item["message_id"] for item in tg_results]
    if len(tg_message_ids) != len(grouped_messages):
        logger.error(
            "Telegram 相册返回数量异常：source=%s result=%s media_group_id=%s",
            len(grouped_messages),
            len(tg_message_ids),
            media_group_id,
        )
        finish("❌ <b>发布失败</b>\n\nTelegram 相册返回数量异常，请重试")
        if delete_media_group_state:
            delete_media_group_state(media_group_id)
        return True

    if reply_targets["mastodon_reply_to"]:
        try:
            masto_data = publish_album_to_mastodon(
                grouped_messages,
                post_to_mastodon,
                in_reply_to_id=reply_targets["mastodon_reply_to"],
            )
        except TypeError:
            masto_data = publish_album_to_mastodon(
                grouped_messages,
                post_to_mastodon,
            )
    else:
        masto_data = publish_album_to_mastodon(
            grouped_messages,
            post_to_mastodon,
        )
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
        if mark_media_group_published:
            mark_media_group_published(media_group_id)
        finish(PARTIAL_PUBLISH_TEXT)
        return True

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

    if mark_media_group_published:
        mark_media_group_published(media_group_id)
    finish(PUBLISH_SUCCESS_TEXT)
    return True


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

    message_has_media = is_media_message(msg)
    masto_ok = True
    if has_target(mapping.get("masto")):
        if message_has_media:
            from api.clients import edit_mastodon_status_with_existing_media

            masto_ok = edit_mastodon_status_with_existing_media(mapping["masto"], new_text)
        else:
            masto_ok = edit_mastodon_status(mapping["masto"], new_text)
    if not masto_ok:
        mastodon_error_text = (
            "Mastodon 无法更新并保留原媒体附件，已停止本次编辑，避免图片或视频丢失。"
            if message_has_media
            else "Mastodon 更新失败，已停止本次编辑，避免两端内容不一致。"
        )
        send_tg_message(
            ADMIN_ID,
            f"❌ <b>编辑未完成</b>\n\n{mastodon_error_text}",
            reply_to=source_msg_id,
        )
        return

    tg_ok = True
    if has_target(mapping.get("tg_channel")):
        if message_has_media:
            from api.clients import edit_tg_message_caption

            tg_ok = edit_tg_message_caption(TG_CHANNEL_ID, mapping["tg_channel"], new_text)
        else:
            tg_ok = edit_tg_message(TG_CHANNEL_ID, mapping["tg_channel"], new_text)

    if tg_ok and masto_ok:
        target_text = "、".join(synced_targets(mapping, has_target)) or "已同步的平台"
        send_tg_message(
            ADMIN_ID,
            f"✅ <b>编辑成功</b>\n\n已同步更新到：\n• {target_text}",
            reply_to=source_msg_id,
        )
        return

    send_tg_message(
        ADMIN_ID,
        (
            "⚠️ <b>编辑部分完成</b>\n\n"
            "Mastodon 已更新，Telegram 频道更新失败。请检查 Telegram 原消息是否仍允许编辑。"
            if has_target(mapping.get("masto"))
            else "❌ <b>编辑未完成</b>\n\nTelegram 频道更新失败。请检查 Telegram 原消息是否仍允许编辑。"
        ),
        reply_to=source_msg_id,
    )


def edit_command(msg: Mapping) -> Optional[str]:
    text = message_text(msg)
    commands = (
        "replace_image_text",
        "replace_video_text",
        "edit_image_text",
        "edit_video_text",
        "replace_image",
        "replace_video",
        "edit_image",
        "edit_video",
        "edit",
    )
    for command in commands:
        if text == f"/{command}" or text.startswith(f"/{command} "):
            return command
    return None


def edit_command_text(msg: Mapping, command: str) -> str:
    text = message_text(msg)
    return re.sub(rf"^/{command}(?:\s+|$)", "", text, count=1).strip()


def edit_replied_message(
    msg: Mapping,
    send_tg_message: SendMessage,
    get_mapping: GetMapping,
    resolve_source_message_id: Optional[ResolveSourceMessageId],
    has_target: HasTarget,
) -> None:
    command = edit_command(msg)
    reply_to = msg.get("reply_to_message")
    if not command or not reply_to:
        send_tg_message(ADMIN_ID, "❌ 请回复要编辑的原帖子后再使用编辑命令")
        return
    if msg.get("media_group_id"):
        send_tg_message(ADMIN_ID, "❌ 编辑命令不支持相册消息，请单独发送视频或文字")
        return

    reply_message_id = reply_to.get("message_id")
    if reply_message_id is None:
        send_tg_message(ADMIN_ID, "❌ 无法识别要编辑的原帖子")
        return
    source_message_id = (
        resolve_source_message_id(reply_message_id)
        if resolve_source_message_id
        else reply_message_id
    )
    mapping = get_mapping(source_message_id)
    if not mapping:
        send_tg_message(ADMIN_ID, "❌ 未找到原帖子的同步记录，无法编辑")
        return

    if command in {"edit_image", "edit_video"}:
        send_tg_message(
            ADMIN_ID,
            f"⚠️ <b>命令已更新</b>\n\n请使用 /replace_{command.split('_')[1]}。",
            reply_to=reply_message_id,
        )
        return

    text_only_commands = {"edit", "edit_image_text", "edit_video_text"}
    if command in text_only_commands:
        if is_media_message(msg):
            send_tg_message(
                ADMIN_ID,
                "❌ <b>文字编辑命令不接受新媒体</b>\n\n"
                "如需替换图片或视频，请使用对应的 /replace_* 命令。",
                reply_to=reply_message_id,
            )
            return
        old_media = extract_media_payload(reply_to)
        target_matches = {
            "edit": old_media is None,
            "edit_image_text": is_image_media(old_media),
            "edit_video_text": is_video_media(old_media),
        }
        if not target_matches[command]:
            target_names = {
                "edit": "纯文本",
                "edit_image_text": "图片",
                "edit_video_text": "视频",
            }
            send_tg_message(
                ADMIN_ID,
                f"❌ <b>原帖子类型不匹配</b>\n\n"
                f"此命令只适用于{target_names[command]}帖子。",
                reply_to=reply_message_id,
            )
            return
        new_text = edit_command_text(msg, command)
        if not new_text:
            send_tg_message(
                ADMIN_ID,
                f"❌ 请在 /{command} 后填写新的文字内容",
                reply_to=reply_message_id,
            )
            return
        masto_ok = True
        if has_target(mapping.get("masto")):
            from api.clients import (
                edit_mastodon_status,
                edit_mastodon_status_with_existing_media,
            )

            masto_ok = (
                edit_mastodon_status_with_existing_media(mapping["masto"], new_text)
                if old_media
                else edit_mastodon_status(mapping["masto"], new_text)
            )
        if not masto_ok:
            mastodon_error_text = (
                "Mastodon 无法更新并保留原媒体附件，已停止本次编辑，避免图片或视频丢失。"
                if old_media
                else "Mastodon 更新失败，已停止本次编辑，避免两端内容不一致。"
            )
            send_tg_message(
                ADMIN_ID,
                f"❌ <b>文字编辑未完成</b>\n\n{mastodon_error_text}",
                reply_to=reply_message_id,
            )
            return

        tg_ok = True
        if has_target(mapping.get("tg_channel")):
            from api.clients import edit_tg_message, edit_tg_message_caption

            tg_ok = (
                edit_tg_message_caption(TG_CHANNEL_ID, mapping["tg_channel"], new_text)
                if old_media
                else edit_tg_message(TG_CHANNEL_ID, mapping["tg_channel"], new_text)
            )
        if tg_ok:
            send_tg_message(ADMIN_ID, "✅ <b>文字编辑成功</b>", reply_to=reply_message_id)
            return
        send_tg_message(
            ADMIN_ID,
            (
                "⚠️ <b>文字编辑部分完成</b>\n\n"
                "Mastodon 已更新，Telegram 频道更新失败。请检查 Telegram 原消息是否仍允许编辑。"
                if has_target(mapping.get("masto"))
                else "❌ <b>文字编辑未完成</b>\n\nTelegram 频道更新失败。请检查 Telegram 原消息是否仍允许编辑。"
            ),
            reply_to=reply_message_id,
        )
        return

    replacement_commands = {
        "replace_image": (is_image_media, "图片", "photo"),
        "replace_image_text": (is_image_media, "图片", "photo"),
        "replace_video": (is_video_media, "视频", "video"),
        "replace_video_text": (is_video_media, "视频", "video"),
    }
    validator, media_name, media_type = replacement_commands[command]
    media = extract_media_payload(msg)
    old_media = extract_media_payload(reply_to)
    if not validator(media) or not validator(old_media):
        send_tg_message(
            ADMIN_ID,
            f"❌ <b>{media_name}替换失败</b>\n\n"
            f"请回复原{media_name}帖子，并发送一个新的{media_name}。",
            reply_to=reply_message_id,
        )
        return
    new_text = edit_command_text(msg, command)
    needs_text = command.endswith("_text")
    if needs_text and not new_text:
        send_tg_message(
            ADMIN_ID,
            f"❌ 请在 /{command} 后填写新的文字内容",
            reply_to=reply_message_id,
        )
        return
    if not needs_text and new_text:
        send_tg_message(
            ADMIN_ID,
            f"❌ /{command} 只替换{media_name}；"
            f"如需同时修改文字，请使用 /{command}_text。",
            reply_to=reply_message_id,
        )
        return
    if is_video_media(media):
        limit = mastodon_video_size_limit()
        if media.file_size > limit:
            from api.clients import get_mastodon_video_size_limit

            send_tg_message(
                ADMIN_ID,
                video_size_error(media.file_size, limit, get_mastodon_video_size_limit()),
                reply_to=reply_message_id,
            )
            return
    elif media.file_size > MAX_MEDIA_SIZE_BYTES:
        send_tg_message(
            ADMIN_ID,
            "⚠️ <b>图片文件过大</b>\n\n"
            "当前最多支持 <b>10MB</b> 的图片，请压缩后重新发送。",
            reply_to=reply_message_id,
        )
        return

    from api.clients import (
        download_tg_file,
        edit_mastodon_status_media,
        edit_tg_media_message,
        get_tg_file_path,
        upload_mastodon_media,
    )

    downloaded_media = download_media_file(
        media.file_id, media.original_filename, get_tg_file_path, download_tg_file
    )
    if not downloaded_media or not downloaded_media["filename"]:
        send_tg_message(
            ADMIN_ID,
            f"❌ 新{media_name}下载失败，原帖子保持不变",
            reply_to=reply_message_id,
        )
        return
    filename = downloaded_media["filename"]
    masto_media = upload_mastodon_media(
        downloaded_media["content"], filename, media.mime_type
    )
    if not masto_media:
        send_tg_message(
            ADMIN_ID,
            f"❌ 新{media_name}上传到 Mastodon 失败，原帖子保持不变",
            reply_to=reply_message_id,
        )
        return

    new_text = new_text or message_text(reply_to)
    tg_ok = True
    if has_target(mapping.get("tg_channel")):
        tg_ok = edit_tg_media_message(
            TG_CHANNEL_ID,
            mapping["tg_channel"],
            downloaded_media["content"],
            filename,
            media.mime_type,
            new_text,
            media_type,
        )
    masto_ok = True
    if has_target(mapping.get("masto")):
        masto_ok = edit_mastodon_status_media(mapping["masto"], new_text, masto_media["id"])
    if tg_ok and masto_ok:
        send_tg_message(ADMIN_ID, f"✅ <b>{media_name}替换成功</b>", reply_to=reply_message_id)
        return
    failed_targets = [
        name
        for name, ok in (("Telegram", tg_ok), ("Mastodon", masto_ok))
        if not ok
    ]
    send_tg_message(
        ADMIN_ID,
        "⚠️ <b>媒体替换部分失败</b>\n\n"
        f"失败平台：{'、'.join(failed_targets)}\n"
        "可能已有平台完成替换，请检查 Telegram 和 Mastodon 两端状态。",
        reply_to=reply_message_id,
    )


def delete_message(
    msg: Mapping,
    send_tg_message: SendMessage,
    get_mapping: GetMapping,
    get_mapping_by_media_group_id: GetMappingByMediaGroupId,
    get_media_group_source_message_ids: GetMediaGroupSourceMessageIds,
    cancel_jobs_for_source_message: CancelJobsForSourceMessage,
    cancel_jobs_for_media_group: CancelJobsForMediaGroup,
    get_pending_media_group_items: GetPendingMediaGroupItems,
    delete_pending_media_group_items: DeletePendingMediaGroupItems,
    has_target: HasTarget,
    delete_tg_message: DeleteTelegramMessage,
    delete_tg_messages: DeleteTelegramMessages,
    delete_mastodon_status: DeleteMastodonStatus,
    delete_mapping: DeleteMapping,
    resolve_source_message_id: Optional[ResolveSourceMessageId] = None,
    get_mappings_by_media_group_id: Optional[GetMappingsByMediaGroupId] = None,
    delete_media_group_state: Optional[DeleteMediaGroupState] = None,
) -> None:
    reply_to = msg.get("reply_to_message")
    if not reply_to:
        send_tg_message(ADMIN_ID, "❌ 请回复要删除的消息后使用 /delete 命令")
        return

    reply_source_message_id = (
        resolve_source_message_id(reply_to["message_id"])
        if resolve_source_message_id
        else reply_to["message_id"]
    )
    mapping = get_mapping(reply_source_message_id)
    if not mapping and reply_to.get("media_group_id"):
        mapping = get_mapping_by_media_group_id(reply_to["media_group_id"])
    if not mapping:
        cancelled_jobs = cancel_jobs_for_source_message(reply_source_message_id)
        media_group_id = reply_to.get("media_group_id")
        if media_group_id:
            cancelled_jobs += cancel_jobs_for_media_group(media_group_id)
            if get_pending_media_group_items(media_group_id):
                delete_pending_media_group_items(media_group_id)
                cancelled_jobs += 1

        if cancelled_jobs:
            delete_tg_message(ADMIN_ID, reply_to["message_id"])
            delete_tg_message(ADMIN_ID, msg["message_id"])
            send_tg_message(
                ADMIN_ID,
                "✅ <b>删除成功</b>\n\n已取消尚未同步完成的消息任务。",
            )
            return

        send_tg_message(ADMIN_ID, "❌ 未找到原消息的映射记录，无法删除")
        return

    source_msg_id = mapping["source"]
    targets = synced_targets(mapping, has_target)
    media_group_id = mapping.get("media_group_id")

    tg_message_ids = mapping.get("tg_channel_messages") or []
    if media_group_id:
        all_group_mappings = (
            get_mappings_by_media_group_id(media_group_id)
            if get_mappings_by_media_group_id
            else []
        )
        if all_group_mappings:
            tg_message_ids = sorted(
                {
                    message_id
                    for item in all_group_mappings
                    for message_id in (item.get("tg_channel_messages") or [])
                }
            )
            if not tg_message_ids:
                tg_message_ids = sorted(
                    {
                        item["tg_channel"]
                        for item in all_group_mappings
                        if item.get("tg_channel")
                    }
                )

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

    source_message_ids = [source_msg_id]
    if media_group_id:
        grouped_source_ids = get_media_group_source_message_ids(media_group_id)
        if grouped_source_ids:
            source_message_ids = grouped_source_ids

        pending_grouped_messages = get_pending_media_group_items(media_group_id)
        pending_source_ids = [
            item["message_id"]
            for item in pending_grouped_messages
            if item.get("message_id") not in source_message_ids
        ]
        if pending_source_ids:
            source_message_ids.extend(pending_source_ids)

        cancel_jobs_for_media_group(media_group_id)
        if pending_grouped_messages:
            delete_pending_media_group_items(media_group_id)
        if delete_media_group_state:
            delete_media_group_state(media_group_id)

    if len(source_message_ids) > 1:
        delete_tg_messages(ADMIN_ID, source_message_ids)
    else:
        for source_id in source_message_ids:
            delete_tg_message(ADMIN_ID, source_id)
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


def process_job(
    job_type: str,
    payload: Mapping,
    send_tg_message: SendMessage,
    edit_message_text: EditMessageText,
    telegram_request: TelegramRequest,
    post_to_mastodon: PostToMastodon,
    save_mapping: SaveMapping,
    save_private_message_alias: Optional[SavePrivateMessageAlias],
    get_pending_media_group_items: GetPendingMediaGroupItems,
    pop_ready_pending_media_group_items: PopReadyPendingMediaGroupItems,
    logger: logging.Logger,
    get_mapping: Optional[GetMapping] = None,
    resolve_source_message_id: Optional[ResolveSourceMessageId] = None,
    get_mapping_by_media_group_id: Optional[GetMappingByMediaGroupId] = None,
    get_mappings_by_media_group_id: Optional[GetMappingsByMediaGroupId] = None,
    get_media_group_source_message_ids: Optional[GetMediaGroupSourceMessageIds] = None,
    get_media_group_state: Optional[GetMediaGroupState] = None,
    bump_media_group_stable_check: Optional[BumpMediaGroupStableCheck] = None,
    mark_media_group_published: Optional[MarkMediaGroupPublished] = None,
    delete_media_group_state: Optional[DeleteMediaGroupState] = None,
    cancel_jobs_for_source_message: Optional[CancelJobsForSourceMessage] = None,
    cancel_jobs_for_media_group: Optional[CancelJobsForMediaGroup] = None,
    has_target: Optional[HasTarget] = None,
    delete_tg_message: Optional[DeleteTelegramMessage] = None,
    delete_tg_messages: Optional[DeleteTelegramMessages] = None,
    delete_mastodon_status: Optional[DeleteMastodonStatus] = None,
    delete_mapping: Optional[DeleteMapping] = None,
    delete_pending_media_group_items: Optional[DeletePendingMediaGroupItems] = None,
) -> bool:
    if job_type == "publish_message":
        if not get_mapping:
            return False
        publish_message(
            payload,
            send_tg_message,
            edit_message_text,
            telegram_request,
            post_to_mastodon,
            save_mapping,
            logger,
            get_mapping,
            resolve_source_message_id,
            save_private_message_alias,
        )
        return True

    if job_type == "process_media_group":
        message = payload.get("message")
        if not isinstance(message, MappingABC):
            return True
        if not all([
            get_media_group_state,
            bump_media_group_stable_check,
            mark_media_group_published,
            delete_media_group_state,
            get_mapping,
        ]):
            return True
        return process_pending_media_group(
            dict(message),
            send_tg_message,
            edit_message_text,
            telegram_request,
            post_to_mastodon,
            save_mapping,
            get_pending_media_group_items,
            pop_ready_pending_media_group_items,
            logger,
            payload.get("expected_latest_message_id"),
            get_media_group_state,
            bump_media_group_stable_check,
            mark_media_group_published,
            delete_media_group_state,
            get_mapping,
            resolve_source_message_id,
            save_private_message_alias,
        )

    if job_type == "delete_message":
        if not all([
            get_mapping,
            get_mapping_by_media_group_id,
            get_mappings_by_media_group_id,
            get_media_group_source_message_ids,
            cancel_jobs_for_source_message,
            cancel_jobs_for_media_group,
            get_pending_media_group_items,
            delete_pending_media_group_items,
            delete_media_group_state,
            has_target,
            delete_tg_message,
            delete_tg_messages,
            delete_mastodon_status,
            delete_mapping,
        ]):
            return False
        delete_message(
            payload,
            send_tg_message,
            get_mapping,
            get_mapping_by_media_group_id,
            get_media_group_source_message_ids,
            cancel_jobs_for_source_message,
            cancel_jobs_for_media_group,
            get_pending_media_group_items,
            delete_pending_media_group_items,
            has_target,
            delete_tg_message,
            delete_tg_messages,
            delete_mastodon_status,
            delete_mapping,
            resolve_source_message_id,
            get_mappings_by_media_group_id,
            delete_media_group_state,
        )
        return True

    logger.warning("未知任务类型：%s", job_type)
    return True


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
        if document_image_mime(msg["document"]) or document_video_mime(msg["document"]):
            return True

    # 4. 允许单个视频
    if "video" in msg:
        return True

    # 5. 排除其他不支持的类型
    other_media = ["audio", "voice", "sticker", "video_note"]
    if any(k in msg for k in other_media):
        return False

    return False


def unsupported_message_text(msg: Mapping) -> Optional[str]:
    if "forward_from" in msg or "forward_from_chat" in msg:
        return "❌ 不支持转发消息\n\n" "请直接发送原创内容，不要转发其他聊天中的消息。"

    if "document" in msg:
        if not document_image_mime(msg["document"]) and not document_video_mime(msg["document"]):
            return (
                "❌ 不支持的文件类型\n\n"
                "仅支持作为文件发送的静态图片 (JPG, PNG, WebP, HEIC, HEIF等) "
                "或常见视频文件 (MP4, MOV, WebM等)。"
            )

    if "video" in msg:
        return None

    other_media = [
        "animation",
        "audio",
        "voice",
        "sticker",
    ]
    if any(k in msg for k in other_media):
        return (
            "❌ 不支持的内容类型\n\n"
            "此机器人目前仅支持纯文本、静态图片和单个视频。\n"
            "暂不支持 GIF、语音、音频、贴纸等其他媒体。"
        )

    if "media_group_id" in msg:
        return None

    return None
