import logging
from typing import Any, Callable, Dict, List, Optional

from api.config import ADMIN_ID, TG_CHANNEL_ID
from api.messages import PARTIAL_PUBLISH_TEXT, PUBLISH_SUCCESS_TEXT, SYNCING_TEXT

Mapping = Dict[str, Any]
SendMessage = Callable[[int, str, Optional[int]], Optional[Dict[str, Any]]]
EditMessageText = Callable[[int, int, str], bool]
TelegramRequest = Callable[[str, Dict[str, Any]], Any]
PostToMastodon = Callable[[str], Optional[Dict[str, Any]]]
SaveMapping = Callable[[int, int, Optional[str]], None]
GetMapping = Callable[[int], Optional[Mapping]]
HasTarget = Callable[[Optional[str]], bool]
EditTelegramMessage = Callable[[str, int, str], bool]
EditMastodonStatus = Callable[[str, str], bool]
DeleteTelegramMessage = Callable[[str, int], bool]
DeleteMastodonStatus = Callable[[str], bool]
DeleteMapping = Callable[[int], None]


def synced_targets(mapping: Mapping, has_target: HasTarget) -> List[str]:
    targets = []
    if has_target(mapping.get("tg_channel")):
        targets.append("Telegram")
    if has_target(mapping.get("masto")):
        targets.append("Mastodon")
    return targets


def publish_message(
    msg: Mapping,
    send_tg_message: SendMessage,
    edit_message_text: EditMessageText,
    telegram_request: TelegramRequest,
    post_to_mastodon: PostToMastodon,
    save_mapping: SaveMapping,
    logger: logging.Logger,
) -> None:
    text = msg.get("text", "").strip()
    if not text:
        send_tg_message(ADMIN_ID, "❌ 消息内容为空，无法发布")
        return

    logger.info(f"开始发布消息：{text[:50]}...")
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

    tg_resp = telegram_request(
        "sendMessage", {"chat_id": TG_CHANNEL_ID, "text": text, "parse_mode": "HTML"}
    )
    if not tg_resp or not tg_resp.ok:
        error_text = tg_resp.text if tg_resp else "request failed"
        logger.error(f"Telegram 发布失败：{error_text}")
        finish("❌ <b>发布失败</b>\n\nTelegram 频道发送失败")
        return

    tg_channel_msg_id = tg_resp.json()["result"]["message_id"]
    logger.info(f"Telegram 发布成功：msg_id={tg_channel_msg_id}")

    masto_data = post_to_mastodon(text)
    if not masto_data:
        save_mapping(msg["message_id"], tg_channel_msg_id, None)
        finish(PARTIAL_PUBLISH_TEXT)
        return

    masto_status_id = masto_data["id"]
    logger.info(f"Mastodon 发布成功：status_id={masto_status_id}")
    save_mapping(msg["message_id"], tg_channel_msg_id, masto_status_id)
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
    new_text = msg.get("text", "").strip()
    if not new_text:
        send_tg_message(ADMIN_ID, "❌ 编辑后的内容为空")
        return

    mapping = get_mapping(source_msg_id)
    if not mapping:
        send_tg_message(ADMIN_ID, "❌ 未找到原消息的映射记录，无法编辑")
        return

    tg_ok = True
    if has_target(mapping.get("tg_channel")):
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

    tg_ok = True
    if has_target(mapping.get("tg_channel")):
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
    if any(
        k in msg
        for k in [
            "photo",
            "video",
            "document",
            "animation",
            "media_group_id",
            "audio",
            "voice",
            "sticker",
        ]
    ):
        return False
    return True


def unsupported_message_text(msg: Mapping) -> Optional[str]:
    if "forward_from" in msg or "forward_from_chat" in msg:
        return "❌ 不支持转发消息\n\n" "请直接发送原创内容，不要转发其他聊天中的消息。"
    if any(
        k in msg
        for k in [
            "photo",
            "video",
            "document",
            "animation",
            "media_group_id",
            "audio",
            "voice",
            "sticker",
        ]
    ):
        return (
            "❌ 不支持的内容类型\n\n"
            "此机器人仅支持纯文本消息。\n"
            "不支持图片、视频、文件等多媒体内容。"
        )
    return None
