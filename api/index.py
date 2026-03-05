import io
import json
import logging
import os
import threading
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


def tg_send_text(chat_id, text, parse_mode=None, reply_to=None):
    body = {"chat_id": chat_id, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    if reply_to:
        body["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
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


def tg_edit(chat_id, msg_id, text, parse_mode="HTML"):
    body = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": parse_mode}
    try:
        tg("editMessageText", json=body)
    except Exception as e:
        LOGGER.warning("tg_edit failed: %s", e)


ACTION_LABELS = {"new": "📝 Post", "reply": "💬 Reply", "quote": "🔁 Repost", "video": "🎬 Post Video", "gif": "🔁 Post GIF"}
PLATFORM_EMOJI = {"Telegram": "📱", "Mastodon": "🐘"}


def render_result(action, content, results):
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
• Text messages
• Images with captions
• Videos and GIFs (send as file for best quality)
• Replies and forwards

Start sending messages! ✨"""
    tg_send_text(chat_id, welcome_text, parse_mode="HTML")


def sync_process(data):
    if data.get("channel_post"):
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

    st = tg_send_text(ADMIN_ID, "⏳ Syncing…", parse_mode="HTML")
    st_id = st.get("message_id")
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

    ma_id = None
    try:
        from mastodon import Mastodon
        masto = Mastodon(access_token=MASTO_TOKEN, api_base_url=MASTO_INSTANCE)

        if action == "quote" and mapping and mapping.get("masto"):
            res = masto.status_reblog(mapping["masto"])
            ma_id = res["id"]
        else:
            media_ids = []
            if media and media_type:
                media_ids = [masto.media_post(io.BytesIO(media), mime_type=mime_type)["id"]]
            rid = mapping.get("masto") if action == "reply" and mapping else None
            res = masto.status_post(content, in_reply_to_id=rid, media_ids=media_ids)
            ma_id = res["id"]
        results.append({"name": "Mastodon", "ok": True})
    except Exception as e:
        results.append({"name": "Mastodon", "ok": False, "err": str(e)[:50]})

    ids = {"tg_chan": tg_cid, "masto": ma_id}
    if any(ids.values()):
        store_mapping(msg["message_id"], ids, tg_cid)

    display_action = media_type if media_type in ACTION_LABELS else action
    tg_edit(ADMIN_ID, st_id, render_result(display_action, content, results))


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    thread = threading.Thread(target=sync_process, args=(data,))
    thread.start()
    return "ok", 200
