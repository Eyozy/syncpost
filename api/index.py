import hmac
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, request
import requests

from api.clients import (
    answer_callback_query,
    delete_mastodon_status,
    delete_tg_message,
    delete_tg_messages,
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
    MEDIA_GROUP_SETTLE_SECONDS,
    SETUP_TOKEN,
    TG_WEBHOOK_SECRET,
    get_missing_config,
    is_config_complete,
)
from api.db import init_db, is_database_configured
from api.messages import WELCOME_TEXT
from api.repositories import (
    bump_media_group_stable_check,
    claim_next_job,
    cancel_jobs_for_media_group,
    cancel_jobs_for_source_message,
    delete_media_group_state,
    check_rate_limit,
    complete_job,
    delete_mapping,
    delete_pending_media_group_items,
    enqueue_job,
    get_mapping,
    get_mapping_by_media_group_id,
    get_mappings_by_media_group_id,
    get_media_group_state,
    get_media_group_source_message_ids,
    get_pending_media_group_items,
    get_ready_pending_media_group_ids,
    has_media_group_mapping,
    has_pending_media_group_job,
    mark_media_group_published,
    pop_ready_pending_media_group_items,
    retry_job,
    resolve_source_message_id,
    save_mapping,
    save_private_message_alias,
    save_pending_media_group_item,
    touch_media_group_state,
)
from api.services import (
    delete_message,
    edit_command,
    edit_replied_message,
    edit_message,
    handle_media_group_message,
    is_supported_message,
    message_text,
    process_job,
    process_pending_media_group,
    publish_message,
    unsupported_message_text,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=4)


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
        get_mapping=get_mapping,
        resolve_source_message_id=resolve_source_message_id,
        save_private_message_alias=save_private_message_alias,
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
        get_pending_media_group_items,
        delete_pending_media_group_items,
        logger,
        touch_media_group_state,
    )
    public_base_url = request.url_root.rstrip("/")

    def dispatch_internal_media_group_process() -> None:
        try:
            requests.post(
                f"{public_base_url}/internal/process-media-group",
                json={
                    "message": msg,
                    "expected_latest_message_id": msg["message_id"],
                },
                headers={"X-Internal-Token": TG_WEBHOOK_SECRET},
                timeout=MEDIA_GROUP_SETTLE_SECONDS + 10,
            )
        except requests.RequestException as exc:
            logger.error("触发内部相册处理失败：%s", exc)

    executor.submit(dispatch_internal_media_group_process)


def handle_edit_message(msg: Dict[str, Any]) -> None:
    edit_message(
        msg,
        send_tg_message,
        get_mapping,
        has_target,
        edit_tg_message,
        edit_mastodon_status,
    )


def handle_edit_command(msg: Dict[str, Any]) -> None:
    edit_replied_message(
        msg,
        send_tg_message,
        get_mapping,
        resolve_source_message_id,
        has_target,
    )


def handle_delete_command(msg: Dict[str, Any]) -> None:
    delete_message(
        msg,
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
        resolve_source_message_id=resolve_source_message_id,
        get_mappings_by_media_group_id=get_mappings_by_media_group_id,
        delete_media_group_state=delete_media_group_state,
    )


def run_worker_once() -> bool:
    job = claim_next_job()
    if not job:
        ready_group_ids = get_ready_pending_media_group_ids(min_age_seconds=1)
        if not ready_group_ids:
            return False

        for media_group_id in ready_group_ids:
            if has_pending_media_group_job(media_group_id):
                logger.info("worker fallback 跳过相册 %s：已有排队中的正式任务", media_group_id)
                continue

            if has_media_group_mapping(media_group_id):
                logger.warning("worker fallback 发现已发布相册 %s 的迟到分片，保留用于删除兜底", media_group_id)
                continue

            logger.warning("worker fallback 发现无正式任务的相册残留 %s，保留等待后续 webhook 重新入队", media_group_id)

        return False

    try:
        processed = process_job(
            job["job_type"],
            job["payload_json"],
            send_tg_message,
            edit_message_text,
            telegram_request,
            post_to_mastodon,
            save_mapping,
            save_private_message_alias,
            get_pending_media_group_items,
            pop_ready_pending_media_group_items,
            logger,
            get_mapping=get_mapping,
            resolve_source_message_id=resolve_source_message_id,
            get_mapping_by_media_group_id=get_mapping_by_media_group_id,
            get_mappings_by_media_group_id=get_mappings_by_media_group_id,
            get_media_group_source_message_ids=get_media_group_source_message_ids,
            get_media_group_state=get_media_group_state,
            bump_media_group_stable_check=bump_media_group_stable_check,
            mark_media_group_published=mark_media_group_published,
            delete_media_group_state=delete_media_group_state,
            cancel_jobs_for_source_message=cancel_jobs_for_source_message,
            cancel_jobs_for_media_group=cancel_jobs_for_media_group,
            has_target=has_target,
            delete_tg_message=delete_tg_message,
            delete_tg_messages=delete_tg_messages,
            delete_mastodon_status=delete_mastodon_status,
            delete_mapping=delete_mapping,
            delete_pending_media_group_items=delete_pending_media_group_items,
        )
        if processed:
            complete_job(job["id"])
        else:
            delay_seconds = 5 if job["job_type"] == "process_media_group" else 2
            retry_job(job["id"], delay_seconds=delay_seconds)
    except Exception as e:
        logger.exception("处理任务失败：%s", e)
        delay_seconds = 5 if job["job_type"] == "process_media_group" else 2
        retry_job(job["id"], delay_seconds=delay_seconds)
    return True


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

    if not is_supported_message(msg):
        warning_text = unsupported_message_text(msg)
        if warning_text:
            send_tg_message(ADMIN_ID, warning_text)
        return True

    if edit_command(msg):
        handle_edit_command(msg)
        return True

    if "media_group_id" in msg:
        handle_media_group(msg)
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
def internal_process_media_group():
    internal_token = request.headers.get("X-Internal-Token", "")
    if not hmac.compare_digest(internal_token, TG_WEBHOOK_SECRET):
        logger.warning("内部相册处理鉴权失败")
        return "Unauthorized", 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return "Bad Request", 400

    message = data.get("message")
    if not isinstance(message, dict):
        return "Bad Request", 400

    expected_latest_message_id = data.get("expected_latest_message_id")
    if expected_latest_message_id is not None and not isinstance(expected_latest_message_id, int):
        return "Bad Request", 400

    time.sleep(MEDIA_GROUP_SETTLE_SECONDS)

    process_pending_media_group(
        message,
        send_tg_message,
        edit_message_text,
        telegram_request,
        post_to_mastodon,
        save_mapping,
        get_pending_media_group_items,
        pop_ready_pending_media_group_items,
        logger,
        expected_latest_message_id=expected_latest_message_id,
        get_media_group_state=get_media_group_state,
        delete_media_group_state=delete_media_group_state,
        get_mapping=get_mapping,
        resolve_source_message_id=resolve_source_message_id,
        save_private_message_alias=save_private_message_alias,
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
        {"command": "edit", "description": "仅编辑纯文本帖子"},
        {"command": "edit_image_text", "description": "新增或修改图片文字"},
        {"command": "replace_image", "description": "只替换图片"},
        {"command": "replace_image_text", "description": "替换图片和文字"},
        {"command": "edit_video_text", "description": "新增或修改视频文字"},
        {"command": "replace_video", "description": "只替换视频"},
        {"command": "replace_video_text", "description": "替换视频和文字"},
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
