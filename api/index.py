import hmac
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from flask import Flask, jsonify, request

from api.clients import (
    answer_callback_query,
    delete_mastodon_status,
    delete_tg_message,
    edit_mastodon_status,
    edit_message_text,
    edit_tg_message,
    post_to_mastodon,
    send_inline_keyboard,
    send_tg_message,
    telegram_request,
)
from api.config import (
    ADMIN_ID,
    SETUP_TOKEN,
    TG_WEBHOOK_SECRET,
    get_missing_config,
    is_config_complete,
)
from api.db import init_db, is_database_configured
from api.messages import WELCOME_TEXT
from api.repositories import (
    check_rate_limit,
    delete_mapping,
    delete_pending_media_group_items,
    get_mapping,
    get_pending_media_group_items,
    save_mapping,
    save_pending_media_group_item,
)
from api.services import (
    delete_message,
    edit_message,
    handle_media_group_message,
    is_supported_message,
    message_text,
    process_pending_media_group,
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


def verify_setup_token(req_obj: Any) -> bool:
    token = req_obj.args.get("token", "")
    return bool(SETUP_TOKEN) and hmac.compare_digest(token, SETUP_TOKEN)


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


def has_target(value: Optional[str]) -> bool:
    """检查同步目标是否存在"""
    return value not in (None, "")


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


def handle_media_group(msg: Dict[str, Any]) -> None:
    handle_media_group_message(
        msg,
        send_tg_message,
        edit_message_text,
        telegram_request,
        post_to_mastodon,
        save_mapping,
        save_pending_media_group_item,
        schedule_media_group_processing,
        get_pending_media_group_items,
        delete_pending_media_group_items,
        logger,
    )


def schedule_media_group_processing(msg: Dict[str, Any]) -> None:
    try:
        requests.post(
            f"https://{request.host}/internal/process-media-group",
            json={"message": msg},
            headers={"X-Internal-Token": TG_WEBHOOK_SECRET},
            timeout=(5, 0.5),
        )
    except requests.exceptions.ReadTimeout:
        pass  # 预期行为：内部端点会 sleep 2 秒，我们不需要等它返回
    except Exception as exc:
        logger.error("触发相册异步处理失败：%s", exc)


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
    text = message_text(msg)

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

    if text == "/start":
        handle_start_command(user_id)
        return True

    if not is_config_complete():
        return True

    if "media_group_id" in msg:
        handle_media_group(msg)
        return True

    if not is_supported_message(msg):
        warning_text = unsupported_message_text(msg)
        if warning_text:
            send_tg_message(ADMIN_ID, warning_text)
        return True

    if text == "/delete":
        handle_delete_command(msg)
        return True

    if text or is_supported_message(msg):
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

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        logger.warning("收到无效 Webhook payload")
        return "OK", 200

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


@app.route("/internal/process-media-group", methods=["POST"])
def process_media_group():
    internal_token = request.headers.get("X-Internal-Token", "")
    if not TG_WEBHOOK_SECRET or not hmac.compare_digest(internal_token, TG_WEBHOOK_SECRET):
        return "Unauthorized", 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return "OK", 200

    message = data.get("message")
    if not isinstance(message, dict):
        return "OK", 200

    process_pending_media_group(
        message,
        send_tg_message,
        edit_message_text,
        telegram_request,
        post_to_mastodon,
        save_mapping,
        get_pending_media_group_items,
        delete_pending_media_group_items,
        logger,
    )
    return "OK", 200


@app.route("/setup", methods=["GET"])
def setup():
    """初始化 Webhook 和命令"""
    if not verify_setup_token(request):
        logger.warning("Setup 鉴权失败")
        return "Unauthorized", 401

    missing = get_missing_config()
    if missing:
        return f'配置不完整，缺少：{", ".join(missing)}', 500

    db_error = init_db()
    if db_error:
        return f"数据库初始化失败，缺少：{db_error}", 500

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
    database_status = "disabled"
    if is_database_configured():
        try:
            init_db()
            database_status = "connected"
        except Exception:
            database_status = "error"

    config_status = "complete" if is_config_complete() else "incomplete"
    missing_config = get_missing_config() if not is_config_complete() else []

    health_data = {
        "status": "ok",
        "service": "SyncPost",
        "version": "1.0.0",
        "database": database_status,
        "config": config_status,
        "missing_config": missing_config,
        "timestamp": datetime.now().isoformat(),
    }

    status_code = (
        200
        if config_status == "complete" and database_status in ["connected", "disabled"]
        else 503
    )

    return jsonify(health_data), status_code
