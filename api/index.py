import io
import json
import logging
import os
import threading
import time
import requests as req
from flask import Flask, request
from upstash_redis import Redis

app = Flask(__name__)
LOGGER = logging.getLogger(__name__)

# --- Config ---
def _get_int_env(name, default=0):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


ADMIN_ID = _get_int_env("ADMIN_ID", 0)
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID")
MASTO_TOKEN = os.getenv("MASTO_TOKEN")
MASTO_INSTANCE = os.getenv("MASTO_INSTANCE")

TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"

CACHE_TTL_MAPPING = 604800
CACHE_TTL_DEDUP = 300
CACHE_TTL_ALBUM = 10
ALBUM_WAIT_SECONDS = 2
ALBUM_MAX_ITEMS = 4
MASTO_MAX_LEN = 480
PREVIEW_LEN = 60

redis = Redis(url=os.getenv("KV_REST_API_URL"), token=os.getenv("KV_REST_API_TOKEN"))


# ── Telegram Bot API (fully sync) ────────────────────────────────────

def tg(method, **kwargs):
    r = req.post(f"{TG_API}/{method}", timeout=15, **kwargs)
    if not r.ok:
        LOGGER.warning("tg %s failed: %s", method, r.text[:200])
        try:
            err_msg = r.json().get("description", "Unknown TG Error")
        except Exception:
            err_msg = r.text[:100]
        raise Exception(f"API Error: {err_msg}")
    return r.json().get("result") or {}


def tg_send_text(chat_id, text, parse_mode=None, reply_to=None, reply_markup=None):
    body = {"chat_id": chat_id, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    if reply_to:
        body["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    if reply_markup:
        body["reply_markup"] = reply_markup
    return tg("sendMessage", json=body)


def _tg_media_payload(chat_id, caption=None, reply_to=None):
    data = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption
    if reply_to:
        data["reply_parameters"] = json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})
    return data


def tg_send_document(chat_id, document, caption=None, reply_to=None):
    data = _tg_media_payload(chat_id, caption=caption, reply_to=reply_to)
    return tg("sendDocument", data=data, files={"document": ("file", document, "application/octet-stream")})


def tg_send_photo(chat_id, photo, caption=None, reply_to=None, filename="photo.jpg"):
    data = _tg_media_payload(chat_id, caption=caption, reply_to=reply_to)
    return tg("sendPhoto", data=data, files={"photo": (filename, photo, "image/jpeg")})


def tg_send_video(chat_id, video, caption=None, reply_to=None, filename="video.mp4"):
    data = _tg_media_payload(chat_id, caption=caption, reply_to=reply_to)
    return tg("sendVideo", data=data, files={"video": (filename, video, "video/mp4")})


def tg_send_animation(chat_id, animation, caption=None, reply_to=None, filename="gif.mp4"):
    data = _tg_media_payload(chat_id, caption=caption, reply_to=reply_to)
    return tg("sendAnimation", data=data, files={"animation": (filename, animation, "video/mp4")})


def tg_send_media_group(chat_id, media_items, reply_to=None):
    body = {"chat_id": str(chat_id), "media": json.dumps(media_items)}
    if reply_to:
        body["reply_parameters"] = json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})
    return tg("sendMediaGroup", json=body)


def tg_edit(chat_id, msg_id, text, parse_mode="HTML", reply_markup=None):
    body = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        body["reply_markup"] = reply_markup
    try:
        tg("editMessageText", json=body)
    except Exception as e:
        LOGGER.warning("tg_edit failed: %s", e)


def tg_delete_message(chat_id, msg_id):
    try:
        tg("deleteMessage", json={"chat_id": chat_id, "message_id": msg_id})
    except Exception as e:
        LOGGER.warning("tg_delete_message failed: %s", e)


def tg_edit_channel_caption(chat_id, msg_id, caption):
    try:
        tg("editMessageCaption", json={"chat_id": chat_id, "message_id": msg_id, "caption": caption})
    except Exception as e:
        LOGGER.warning("tg_edit_channel_caption failed: %s", e)


def tg_answer_callback(callback_id, text=None):
    body = {"callback_query_id": callback_id}
    if text:
        body["text"] = text
    try:
        tg("answerCallbackQuery", json=body)
    except Exception as e:
        LOGGER.warning("tg_answer_callback failed: %s", e)


ACTION_LABELS = {"new": "📝 Post", "reply": "💬 Reply", "quote": "🔁 Repost", "video": "🎬 Post Video", "gif": "🔁 Post GIF", "album": "🖼 Post Gallery", "thread": "📝 Post Thread"}
PLATFORM_EMOJI = {"Telegram": "📱", "Mastodon": "🐘"}


def render_result(action, content, results, with_delete_btn=True, bot_msg_id=None):
    """Render a structured result card in HTML."""
    label = ACTION_LABELS.get(action, "🔄 Sync")
    preview = content[:PREVIEW_LEN] + "…" if len(content) > PREVIEW_LEN else content

    ok = sum(1 for r in results if r["ok"])
    total = len(results)
    all_ok = ok == total

    status_emoji = "✅" if all_ok else "⚠️"
    status_text = "All succeeded" if all_ok else "Partial failure"
    lines = [
        f"<b>{status_emoji} {label} · {status_text}</b>",
        f"<blockquote expandable>{preview}</blockquote>",
        "",
        f"<b>📊 Sync result ({ok}/{total})</b>",
        "",
    ]

    success_items = [r for r in results if r["ok"]]
    if success_items:
        for r in success_items:
            emoji = PLATFORM_EMOJI.get(r["name"], "✓")
            detail = r.get("detail", "")
            if detail:
                lines.append(f"{emoji} <b>{r['name']}</b> · {detail}")
            else:
                lines.append(f"{emoji} <b>{r['name']}</b> ✓")
        lines.append("")

    failed_items = [r for r in results if not r["ok"]]
    if failed_items:
        lines.append("<b>❌ Failure details</b>")
        for r in failed_items:
            emoji = PLATFORM_EMOJI.get(r["name"], "✗")
            err = r.get("err", "Unknown error")
            lines.append(f"{emoji} <b>{r['name']}</b>")
            lines.append(f"   <code>{err}</code>")
        lines.append("")

    if not all_ok:
        lines.append("<i>💡 Try resending to retry failed sync</i>")

    text = "\n".join(lines)

    if with_delete_btn and bot_msg_id and all_ok:
        reply_markup = {
            "inline_keyboard": [[
                {"text": "🗑 Withdraw from all platforms", "callback_data": f"del_{bot_msg_id}"}
            ]]
        }
        return text, reply_markup
    return text, None


def render_updated_result(content, results):
    """Render result card for edited message."""
    preview = content[:PREVIEW_LEN] + "…" if len(content) > PREVIEW_LEN else content
    ok = sum(1 for r in results if r["ok"])
    total = len(results)

    lines = [
        "<b>✏️ Content Updated Globally</b>",
        f"<blockquote expandable>{preview}</blockquote>",
        "",
        f"<b>📊 Update result ({ok}/{total})</b>",
        "",
    ]

    for r in results:
        emoji = PLATFORM_EMOJI.get(r["name"], "✓")
        if r["ok"]:
            lines.append(f"{emoji} <b>{r['name']}</b> Updated ✓")
        else:
            lines.append(f"{emoji} <b>{r['name']}</b> Failed")
            lines.append(f"   <code>{r.get('err', 'Unknown error')}</code>")

    return "\n".join(lines)


def render_deleted_result(content):
    """Render result card for deleted message."""
    preview = content[:PREVIEW_LEN] + "…" if len(content) > PREVIEW_LEN else content

    lines = [
        "<b>🗑 Post Removed Globally</b>",
        f"<blockquote expandable>{preview}</blockquote>",
        "",
        "<b>📊 Delete result (2/2)</b>",
        "",
        "📱 <b>Telegram</b> Deleted ✓",
        "🐘 <b>Mastodon</b> Deleted ✓",
    ]
    return "\n".join(lines)


def render_limit_exceeded(count):
    """Render error card for media limit exceeded."""
    lines = [
        "<b>⚠️ Sync Blocked · Media Limit Exceeded</b>",
        f"<blockquote>[Request contains {count} media files]</blockquote>",
        f"Mastodon natively supports a maximum of <b>4</b> media attachments per post.",
        "",
        "<i>💡 To ensure cross-platform consistency, this sync has been cancelled. Please select up to 4 items and try again.</i>",
    ]
    return "\n".join(lines)


def tg_download(file_id):
    info = tg("getFile", json={"file_id": file_id})
    fp = info.get("file_path") if info else None
    if not fp:
        return None, None
    r = req.get(f"https://api.telegram.org/file/bot{TG_TOKEN}/{fp}", timeout=60)
    if not r.ok:
        return None, None
    return r.content, fp


# ── Redis mapping helpers ─────────────────────────────────────────────

def load_mapping(key):
    raw = redis.get(key)
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def store_mapping(bot_msg_id, mapping, chan_msg_id=None):
    val = json.dumps(mapping)
    redis.set(f"tg_{bot_msg_id}", val, ex=CACHE_TTL_MAPPING)
    if chan_msg_id:
        redis.set(f"chan_{chan_msg_id}", val, ex=CACHE_TTL_MAPPING)


def _determine_action(msg):
    reply_to = msg.get("reply_to_message")
    if reply_to:
        mapping = load_mapping(f"tg_{reply_to['message_id']}")
        if mapping:
            return "reply", mapping

    fwd_chat = msg.get("forward_from_chat")
    if fwd_chat and str(fwd_chat.get("id")) == str(TG_CHANNEL_ID):
        fwd_id = msg.get("forward_from_message_id")
        if fwd_id:
            mapping = load_mapping(f"chan_{fwd_id}")
            if mapping:
                return "quote", mapping

    return "new", None


# ── Mastodon helpers ─────────────────────────────────────────────

def get_masto_mime_type(filepath):
    """Determine MIME type for Mastodon upload based on file extension."""
    ext = filepath.lower().split(".")[-1] if "." in filepath else ""
    mime_map = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
        "webp": "image/webp", "mp4": "video/mp4", "mov": "video/quicktime",
        "webm": "video/webm",
    }
    return mime_map.get(ext, "application/octet-stream")


def split_text_for_masto(text, max_len=MASTO_MAX_LEN):
    """Split text into parts for Mastodon thread, respecting paragraph boundaries."""
    if len(text) <= max_len:
        return [text]

    parts = []
    paragraphs = text.split("\n\n")
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_len:
            current = current + "\n\n" + para if current else para
        else:
            if current:
                parts.append(current.strip())
            if len(para) > max_len:
                while len(para) > max_len:
                    parts.append(para[:max_len].strip())
                    para = para[max_len:].strip()
                if para:
                    current = para
            else:
                current = para

    if current.strip():
        parts.append(current.strip())

    if len(parts) > 1:
        parts = [f"{p} ({i+1}/{len(parts)})" for i, p in enumerate(parts)]

    return parts


# ── Album aggregation helpers ─────────────────────────────────────────────

def save_album_item(group_id, item_data):
    """Save a single album item to Redis and return current count."""
    key = f"album_{group_id}"
    existing = redis.get(key)
    items = []
    if existing:
        if isinstance(existing, dict):
            items = existing.get("items", [])
        elif isinstance(existing, str):
            try:
                data = json.loads(existing)
                items = data.get("items", [])
            except (json.JSONDecodeError, TypeError):
                items = []
    items.append(item_data)
    redis.set(key, json.dumps({"items": items}), ex=CACHE_TTL_ALBUM)
    return len(items)


def get_album_items(group_id):
    """Get all album items from Redis."""
    key = f"album_{group_id}"
    raw = redis.get(key)
    if not raw:
        return []
    if isinstance(raw, dict):
        return raw.get("items", [])
    try:
        data = json.loads(raw)
        return data.get("items", [])
    except (json.JSONDecodeError, TypeError):
        return []


def clear_album(group_id):
    """Clear album data from Redis."""
    redis.delete(f"album_{group_id}")


# ── Main sync logic ──────────────────────────────────────────────────

def handle_start(chat_id):
    """Send welcome message when user sends /start command."""
    welcome_text = """👋 <b>Welcome to SyncPost Bot</b>

This bot syncs your messages to multiple platforms:
• Telegram Channel
• Mastodon

<b>How to use:</b>
1️⃣ Send a text message → Publish a new post
2️⃣ Reply to bot's synced message → Sync as reply/comment
3️⃣ Forward channel message to bot → Boost to Mastodon

<b>Supported content:</b>
• Text messages (long text auto-split for Mastodon)
• Images with captions (up to 4 per post)
• Videos and GIFs (send as file for best quality)
• Replies and forwards

<b>Edit & Delete:</b>
• Edit your message → Updates all platforms
• Click "Withdraw" button → Removes from all platforms

Start sending messages! ✨"""
    tg_send_text(chat_id, welcome_text, parse_mode="HTML")


def handle_edited_message(msg):
    """Handle edited message - update on all platforms."""
    orig_msg_id = msg.get("message_id")
    mapping = load_mapping(f"tg_{orig_msg_id}")
    if not mapping:
        return

    new_content = msg.get("text") or msg.get("caption") or ""
    if not new_content:
        return

    results = []

    tg_chan_id = mapping.get("tg_chan")
    if tg_chan_id:
        try:
            tg_edit_channel_caption(TG_CHANNEL_ID, tg_chan_id, new_content)
            results.append({"name": "Telegram", "ok": True})
        except Exception as e:
            results.append({"name": "Telegram", "ok": False, "err": str(e)[:50]})

    ma_ids = mapping.get("masto_ids", [])
    ma_id = mapping.get("masto")
    if not ma_ids and ma_id:
        ma_ids = [ma_id]

    if ma_ids:
        try:
            from mastodon import Mastodon
            masto = Mastodon(access_token=MASTO_TOKEN, api_base_url=MASTO_INSTANCE)
            parts = split_text_for_masto(new_content)
            for i, ma_id in enumerate(ma_ids):
                if i < len(parts):
                    masto.status_update(ma_id, parts[i])
            results.append({"name": "Mastodon", "ok": True})
        except Exception as e:
            results.append({"name": "Mastodon", "ok": False, "err": str(e)[:50]})

    if results:
        tg_send_text(ADMIN_ID, render_updated_result(new_content, results), parse_mode="HTML")


def handle_delete_callback(callback_data, callback_id, bot_msg_id):
    """Handle delete button callback."""
    mapping = load_mapping(f"tg_{bot_msg_id}")
    if not mapping:
        tg_answer_callback(callback_id, "Mapping not found")
        return

    content_preview = "Post deleted"
    results = []

    tg_chan_id = mapping.get("tg_chan")
    tg_album = mapping.get("tg_album", [])
    if tg_chan_id:
        try:
            for cid in tg_album:
                tg_delete_message(TG_CHANNEL_ID, cid)
            if not tg_album:
                tg_delete_message(TG_CHANNEL_ID, tg_chan_id)
            results.append({"name": "Telegram", "ok": True})
        except Exception as e:
            results.append({"name": "Telegram", "ok": False, "err": str(e)[:50]})

    ma_ids = mapping.get("masto_ids", [])
    ma_id = mapping.get("masto")
    if not ma_ids and ma_id:
        ma_ids = [ma_id]

    if ma_ids:
        try:
            from mastodon import Mastodon
            masto = Mastodon(access_token=MASTO_TOKEN, api_base_url=MASTO_INSTANCE)
            for mid in reversed(ma_ids):
                masto.status_delete(mid)
            results.append({"name": "Mastodon", "ok": True})
        except Exception as e:
            results.append({"name": "Mastodon", "ok": False, "err": str(e)[:50]})

    if results:
        ok = sum(1 for r in results if r["ok"])
        if ok == len(results):
            tg_edit(ADMIN_ID, bot_msg_id, render_deleted_result(content_preview))
        else:
            tg_answer_callback(callback_id, "Delete failed on some platforms")

    redis.delete(f"tg_{bot_msg_id}")


def sync_single(msg, st_id, action=None, mapping=None):
    """Sync a single message (non-album)."""
    results = []
    content = msg.get("caption") or msg.get("text") or ""
    media = None
    media_type = None
    mime_type = "image/jpeg"

    if msg.get("video"):
        media, fp = tg_download(msg["video"]["file_id"])
        if media:
            media_type = "video"
            mime_type = get_masto_mime_type(fp) if fp else "video/mp4"
    elif msg.get("animation"):
        media, fp = tg_download(msg["animation"]["file_id"])
        if media:
            media_type = "gif"
            mime_type = get_masto_mime_type(fp) if fp else "video/mp4"
    elif msg.get("photo"):
        best = max(msg["photo"], key=lambda p: p.get("file_size", 0))
        media, _ = tg_download(best["file_id"])
        if media:
            media_type = "photo"
    elif msg.get("document"):
        doc = msg["document"]
        mime = doc.get("mime_type", "")
        media, fp = tg_download(doc["file_id"])
        if media:
            if mime.startswith("image/"):
                media_type = "photo"
                mime_type = mime
            elif mime.startswith("video/"):
                media_type = "video"
                mime_type = mime

    if action is None:
        action, mapping = _determine_action(msg)

    tg_cid = None
    try:
        if action == "quote":
            tg_cid = mapping.get("tg_chan") if mapping else None
            results.append({"name": "Telegram", "ok": True, "detail": "Skipped (original post)"})
        else:
            rid = mapping.get("tg_chan") if action == "reply" and mapping else None
            if media_type == "video":
                res = tg_send_video(TG_CHANNEL_ID, media, caption=content, reply_to=rid)
            elif media_type == "gif":
                res = tg_send_animation(TG_CHANNEL_ID, media, caption=content, reply_to=rid)
            elif media_type == "photo":
                res = tg_send_photo(TG_CHANNEL_ID, media, caption=content, reply_to=rid)
            else:
                res = tg_send_text(TG_CHANNEL_ID, content, reply_to=rid)
            tg_cid = res.get("message_id")
            results.append({"name": "Telegram", "ok": True})
    except Exception as e:
        results.append({"name": "Telegram", "ok": False, "err": str(e)[:50]})

    ma_ids = []
    is_thread = len(content) > MASTO_MAX_LEN and not media
    try:
        from mastodon import Mastodon
        masto = Mastodon(access_token=MASTO_TOKEN, api_base_url=MASTO_INSTANCE)

        if action == "quote" and mapping and mapping.get("masto"):
            res = masto.status_reblog(mapping["masto"])
            ma_ids = [res["id"]]
        else:
            media_ids = []
            if media and media_type:
                media_ids = [masto.media_post(io.BytesIO(media), mime_type=mime_type)["id"]]

            rid = mapping.get("masto") if action == "reply" and mapping else None
            parts = split_text_for_masto(content) if not media else [content]

            for i, part in enumerate(parts):
                reply_to = rid if i == 0 else ma_ids[-1]
                res = masto.status_post(part, in_reply_to_id=reply_to, media_ids=media_ids if i == 0 else None)
                ma_ids.append(res["id"])

        if is_thread:
            results.append({"name": "Mastodon", "ok": True, "detail": f"Thread {len(ma_ids)}/{len(ma_ids)}"})
        else:
            results.append({"name": "Mastodon", "ok": True})
    except Exception as e:
        results.append({"name": "Mastodon", "ok": False, "err": str(e)[:50]})

    ids = {"tg_chan": tg_cid, "masto": ma_ids[0] if ma_ids else None, "masto_ids": ma_ids}
    if any([ids["tg_chan"], ids["masto"]]):
        store_mapping(msg["message_id"], ids, tg_cid)

    display_action = "thread" if is_thread else (media_type if media_type in ACTION_LABELS else action)
    text, reply_markup = render_result(display_action, content, results, with_delete_btn=True, bot_msg_id=msg["message_id"])
    tg_edit(ADMIN_ID, st_id, text, reply_markup=reply_markup)


def sync_album(items, st_id, action, mapping):
    """Sync an album (media group) to both platforms."""
    results = []
    content = items[0].get("caption", "") if items else ""

    if len(items) > ALBUM_MAX_ITEMS:
        tg_edit(ADMIN_ID, st_id, render_limit_exceeded(len(items)))
        return

    media_list = []
    for item in items:
        if item.get("photo"):
            best = max(item["photo"], key=lambda p: p.get("file_size", 0))
            media, _ = tg_download(best["file_id"])
            if media:
                media_list.append({"type": "photo", "media": media, "file_id": best["file_id"]})

    if not media_list:
        tg_edit(ADMIN_ID, st_id, render_result("album", content, [{"name": "Telegram", "ok": False, "err": "No media found"}])[0])
        return

    tg_cids = []
    try:
        rid = mapping.get("tg_chan") if action == "reply" and mapping else None
        media_items = []
        for i, m in enumerate(media_list):
            item = {"type": m["type"], "media": f"attach://file{i}"}
            if i == 0 and content:
                item["caption"] = content
            media_items.append(item)

        files = {f"file{i}": (f"photo{i}.jpg", m["media"], "image/jpeg") for i, m in enumerate(media_list)}
        body = {"chat_id": str(TG_CHANNEL_ID), "media": json.dumps(media_items)}
        if rid:
            body["reply_parameters"] = json.dumps({"message_id": rid, "allow_sending_without_reply": True})

        res = tg("sendMediaGroup", json=body, files=files)
        tg_cids = [r.get("message_id") for r in res] if res else []
        results.append({"name": "Telegram", "ok": True, "detail": f"{len(media_list)} items"})
    except Exception as e:
        results.append({"name": "Telegram", "ok": False, "err": str(e)[:50]})

    ma_id = None
    try:
        from mastodon import Mastodon
        masto = Mastodon(access_token=MASTO_TOKEN, api_base_url=MASTO_INSTANCE)

        media_ids = []
        for m in media_list:
            mid = masto.media_post(io.BytesIO(m["media"]), mime_type="image/jpeg")["id"]
            media_ids.append(mid)

        rid = mapping.get("masto") if action == "reply" and mapping else None
        res = masto.status_post(content, in_reply_to_id=rid, media_ids=media_ids)
        ma_id = res["id"]
        results.append({"name": "Mastodon", "ok": True, "detail": f"{len(media_list)} items"})
    except Exception as e:
        results.append({"name": "Mastodon", "ok": False, "err": str(e)[:50]})

    first_msg_id = items[0].get("message_id")
    if tg_cids:
        store_mapping(first_msg_id, {"tg_chan": tg_cids[0], "masto": ma_id, "tg_album": tg_cids, "masto_ids": [ma_id] if ma_id else []}, tg_cids[0])

    text, reply_markup = render_result("album", content, results, with_delete_btn=True, bot_msg_id=first_msg_id)
    tg_edit(ADMIN_ID, st_id, text, reply_markup=reply_markup)


def process_album(group_id):
    """Process a complete album after aggregation."""
    items = get_album_items(group_id)
    if not items:
        return

    clear_album(group_id)

    first_msg = items[0]
    action, mapping = _determine_action(first_msg)

    st = tg_send_text(ADMIN_ID, "⏳ Syncing album…", parse_mode="HTML")
    st_id = st.get("message_id")

    sync_album(items, st_id, action, mapping)


def sync_process(data):
    if data.get("channel_post"):
        return

    # Handle callback query (inline button clicks)
    callback = data.get("callback_query")
    if callback:
        callback_id = callback.get("id")
        callback_data = callback.get("data", "")
        if callback_data.startswith("del_"):
            bot_msg_id = int(callback_data[4:])
            handle_delete_callback(callback_data, callback_id, bot_msg_id)
        return

    # Handle edited message
    edited_msg = data.get("edited_message")
    if edited_msg:
        if edited_msg.get("from", {}).get("id") != ADMIN_ID:
            return
        handle_edited_message(edited_msg)
        return

    msg = data.get("message")
    if not msg:
        return

    text = msg.get("text", "")
    if text and text.strip() == "/start":
        chat_id = msg.get("chat", {}).get("id")
        if chat_id:
            handle_start(chat_id)
        return

    if msg.get("from", {}).get("id") != ADMIN_ID:
        chat_id = msg.get("chat", {}).get("id")
        if chat_id:
            tg_send_text(chat_id, "No permission. Please contact the admin.")
        return

    uid = data.get("update_id")
    dk = f"proc_{uid}"
    if redis.get(dk):
        return
    redis.set(dk, "1", ex=CACHE_TTL_DEDUP)

    media_group_id = msg.get("media_group_id")

    if media_group_id:
        has_photo = msg.get("photo") is not None
        if not has_photo:
            return

        count = save_album_item(media_group_id, msg)

        if count == 1:
            threading.Timer(ALBUM_WAIT_SECONDS, process_album, args=(media_group_id,)).start()
        return

    st = tg_send_text(ADMIN_ID, "⏳ Syncing…", parse_mode="HTML")
    st_id = st.get("message_id")
    sync_single(msg, st_id)


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    thread = threading.Thread(target=sync_process, args=(data,))
    thread.start()
    return "ok", 200
