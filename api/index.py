import hmac
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, request

from api.clients import mastodon_delete, mastodon_post, mastodon_put, telegram_request
from api.config import (
    ADMIN_ID,
    CACHE_TTL_MAPPING,
    TG_WEBHOOK_SECRET,
    get_missing_config,
    is_config_complete,
    redis,
)
from api.messages import WELCOME_TEXT
from api.services import (
    delete_message,
    edit_message,
    is_supported_message,
    publish_message,
    unsupported_message_text,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


# ============ 辅助函数 ============


def verify_webhook(req_obj: Any) -> bool:
    """验证 Telegram Webhook 签名"""
    token = req_obj.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return hmac.compare_digest(token, TG_WEBHOOK_SECRET)


def format_missing_config_text(missing: List[str]) -> str:
    missing_list = "\n".join([f"• {item}" for item in missing])
    return (
        "⚠️ <b>配置未完成</b>\n\n"
        f"缺少以下环境变量：\n{missing_list}\n\n"
        "📖 <b>配置指引：</b>\n"
        "请在 Vercel 项目设置中添加以上环境变量，然后重新部署。\n\n"
        "详细说明：\nhttps://github.com/Eyozy/syncpost"
    )


def is_admin(user_id: Optional[int]) -> bool:
    """检查是否为管理员"""
    return user_id == ADMIN_ID


def check_rate_limit(user_id: int) -> bool:
    """检查速率限制：每分钟最多 10 条消息"""
    if not redis:
        return True

    try:
        key = f"rate:{user_id}"
        count = redis.incr(key)
        if count == 1:
            redis.expire(key, 60)

        if count > 10:
            logger.warning(f"用户 {user_id} 触发速率限制：{count}/分钟")
            return False

        return True
    except Exception as e:
        logger.error(f"速率限制检查失败：{e}")
        return True


def send_tg_message(
    chat_id: int, text: str, reply_to: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """发送 Telegram 消息"""
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    resp = telegram_request("sendMessage", payload)
    if not resp:
        return None
    return resp.json() if resp.ok else None


def edit_tg_message(chat_id: str, message_id: int, text: str) -> bool:
    """编辑 Telegram 消息"""
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


def delete_tg_message(chat_id: str, message_id: int) -> bool:
    """删除 Telegram 消息"""
    payload = {"chat_id": chat_id, "message_id": message_id}
    resp = telegram_request("deleteMessage", payload)
    if not resp:
        return False
    return resp.ok


def post_to_mastodon(
    text: str, in_reply_to_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """发布到 Mastodon"""
    payload = {"status": text, "visibility": "public"}
    if in_reply_to_id:
        payload["in_reply_to_id"] = in_reply_to_id
    resp = mastodon_post("/api/v1/statuses", payload)
    if not resp:
        return None
    return resp.json() if resp.ok else None


def edit_mastodon_status(status_id: str, text: str) -> bool:
    """编辑 Mastodon 状态"""
    payload = {"status": text}
    resp = mastodon_put(f"/api/v1/statuses/{status_id}", payload)
    if not resp:
        return False
    return resp.ok


def delete_mastodon_status(status_id: str) -> bool:
    """删除 Mastodon 状态"""
    resp = mastodon_delete(f"/api/v1/statuses/{status_id}")
    if not resp:
        return False
    return resp.ok


def save_mapping(
    source_msg_id: int, tg_channel_msg_id: int, masto_status_id: Optional[str]
) -> None:
    """保存消息映射关系"""
    if not redis:
        return
    try:
        mapping = {
            "source": source_msg_id,
            "tg_channel": tg_channel_msg_id,
            "masto": masto_status_id,
            "timestamp": datetime.now().isoformat(),
        }
        redis.setex(f"msg:{source_msg_id}", CACHE_TTL_MAPPING, json.dumps(mapping))
        redis.setex(
            f"msg:tg:{tg_channel_msg_id}", CACHE_TTL_MAPPING, json.dumps(mapping)
        )
        logger.info(
            "保存映射：source=%s, tg=%s, masto=%s",
            source_msg_id,
            tg_channel_msg_id,
            masto_status_id,
        )
    except Exception as e:
        logger.error(f"保存映射失败：{e}")


def has_target(value: Optional[str]) -> bool:
    """检查同步目标是否存在"""
    return value not in (None, "")


def get_mapping(source_msg_id: int) -> Optional[Dict[str, Any]]:
    """获取消息映射关系"""
    if not redis:
        return None
    try:
        data = redis.get(f"msg:{source_msg_id}")
        if not data:
            data = redis.get(f"msg:tg:{source_msg_id}")
        return json.loads(data) if data else None
    except Exception as e:
        logger.error(f"获取映射失败：{e}")
        return None


def delete_mapping(source_msg_id: int) -> None:
    """删除消息映射关系"""
    if not redis:
        return
    try:
        mapping = get_mapping(source_msg_id)
        source_key = source_msg_id
        if mapping:
            source_key = mapping["source"]
            if has_target(mapping.get("tg_channel")):
                redis.delete(f"msg:tg:{mapping['tg_channel']}")
        redis.delete(f"msg:{source_key}")
        logger.info(f"删除映射：source={source_msg_id}")
    except Exception as e:
        logger.error(f"删除映射失败：{e}")


def send_inline_keyboard(
    chat_id: int, text: str, buttons: List[List[Dict[str, str]]]
) -> Optional[Dict[str, Any]]:
    """发送带内联键盘的消息"""
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
    """回应回调查询"""
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert
    resp = telegram_request("answerCallbackQuery", payload)
    if not resp:
        return False
    return resp.ok


def edit_message_text(chat_id: int, message_id: int, text: str) -> bool:
    """编辑消息文本（用于更新按钮消息）"""
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


# ============ 命令处理函数 ============


def handle_start_command(user_id: int) -> None:
    """处理 /start 命令"""
    if not is_config_complete():
        missing = get_missing_config()
        buttons = [[{"text": "✅ 我已完成配置", "callback_data": "check_config"}]]
        send_inline_keyboard(user_id, format_missing_config_text(missing), buttons)
        return

    send_tg_message(user_id, WELCOME_TEXT)


def handle_check_config_callback(callback: Dict[str, Any]) -> None:
    """处理"我已完成配置"按钮点击"""
    user_id = callback["from"]["id"]
    message_id = callback["message"]["message_id"]
    callback_query_id = callback["id"]

    if is_config_complete():
        edit_message_text(
            user_id, message_id, f"✅ <b>配置检测通过！</b>\n\n{WELCOME_TEXT}"
        )
        answer_callback_query(callback_query_id, "配置检测通过！")
        return

    missing = get_missing_config()
    missing_text = "、".join(missing)
    answer_callback_query(
        callback_query_id, f"配置仍未完成，缺少：{missing_text}", show_alert=True
    )


# ============ 核心处理逻辑 ============


def handle_text_message(msg: Dict[str, Any]) -> None:
    publish_message(
        msg,
        send_tg_message,
        edit_message_text,
        telegram_request,
        post_to_mastodon,
        save_mapping,
        logger,
    )


def handle_edit_message(msg: Dict[str, Any]) -> None:
    edit_message(
        msg,
        send_tg_message,
        get_mapping,
        has_target,
        edit_tg_message,
        edit_mastodon_status,
    )


def handle_delete_command(msg: Dict[str, Any]) -> None:
    delete_message(
        msg,
        send_tg_message,
        get_mapping,
        has_target,
        delete_tg_message,
        delete_mastodon_status,
        delete_mapping,
    )


def handle_unauthorized_message(user_id: Optional[int]) -> None:
    if user_id is None:
        return
    send_tg_message(
        user_id,
        "🚫 访问被拒绝\n\n" "此机器人仅供授权用户使用。\n" "如需使用，请联系管理员。",
    )


def handle_incoming_message(msg: Dict[str, Any]) -> bool:
    user_id = msg.get("from", {}).get("id")

    if not is_admin(user_id):
        handle_unauthorized_message(user_id)
        return True

    if not check_rate_limit(user_id):
        send_tg_message(
            user_id,
            "⚠️ <b>速率限制</b>\n\n"
            "您的操作过于频繁，请稍后再试。\n"
            "限制：每分钟最多 10 条消息",
        )
        return True

    text = msg.get("text", "")
    if text.strip() == "/start":
        handle_start_command(user_id)
        return True

    if not is_config_complete():
        return True

    if not is_supported_message(msg):
        warning_text = unsupported_message_text(msg)
        if warning_text:
            send_tg_message(ADMIN_ID, warning_text)
        return True

    if text.strip() == "/delete":
        handle_delete_command(msg)
        return True

    if text:
        handle_text_message(msg)
        return True

    return False


def handle_edited_message(msg: Dict[str, Any]) -> bool:
    user_id = msg.get("from", {}).get("id")
    if not is_admin(user_id) or not is_config_complete():
        return True
    handle_edit_message(msg)
    return True


def handle_callback(callback: Dict[str, Any]) -> bool:
    user_id = callback["from"]["id"]
    if not is_admin(user_id):
        return True

    if callback["data"] == "check_config":
        handle_check_config_callback(callback)
        return True

    return False


# ============ Webhook 路由 ============


@app.route("/webhook", methods=["POST"])
def webhook():
    """处理 Telegram Webhook"""
    if not verify_webhook(request):
        logger.warning("Webhook 验证失败")
        return "Unauthorized", 401

    data = request.get_json()
    logger.info(f"收到 Webhook: {data.get('update_id', 'unknown')}")

    if "message" in data:
        if handle_incoming_message(data["message"]):
            return "OK", 200

    if "edited_message" in data:
        if handle_edited_message(data["edited_message"]):
            return "OK", 200

    if "callback_query" in data:
        if handle_callback(data["callback_query"]):
            return "OK", 200

    return "OK", 200


@app.route("/setup", methods=["GET"])
def setup():
    """初始化 Webhook 和命令"""
    missing = get_missing_config()
    if missing:
        return f'配置不完整，缺少：{", ".join(missing)}', 500

    webhook_url = f"https://{request.host}/webhook"

    # 设置 Webhook
    webhook_resp = telegram_request(
        "setWebhook",
        {
            "url": webhook_url,
            "secret_token": TG_WEBHOOK_SECRET,
            "allowed_updates": ["message", "edited_message", "callback_query"],
        },
    )

    if not webhook_resp or not webhook_resp.ok:
        return f"Webhook 设置失败：{webhook_resp.text}", 500

    delete_resp = telegram_request("deleteMyCommands", {})
    if delete_resp and not delete_resp.ok:
        logger.warning(f"删除旧命令失败：{delete_resp.text}")

    # 设置新命令
    commands = [
        {"command": "start", "description": "显示欢迎消息"},
        {"command": "delete", "description": "删除已发布的消息（回复消息后使用）"},
    ]

    cmd_resp = telegram_request("setMyCommands", {"commands": commands})

    if not cmd_resp or not cmd_resp.ok:
        return f"命令设置失败：{cmd_resp.text}", 500

    return f"✅ Webhook 已设置为 {webhook_url}，命令已注册（旧命令已清除）", 200


@app.route("/", methods=["GET"])
def index():
    """健康检查"""
    redis_status = "disabled"
    if redis:
        try:
            redis.ping()
            redis_status = "connected"
        except Exception:
            redis_status = "error"

    config_status = "complete" if is_config_complete() else "incomplete"
    missing_config = get_missing_config() if not is_config_complete() else []

    health_data = {
        "status": "ok",
        "service": "SyncPost",
        "version": "1.0.0",
        "redis": redis_status,
        "config": config_status,
        "missing_config": missing_config,
        "timestamp": datetime.now().isoformat(),
    }

    status_code = (
        200
        if config_status == "complete" and redis_status in ["connected", "disabled"]
        else 503
    )

    return jsonify(health_data), status_code
