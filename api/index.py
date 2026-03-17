import hmac
import json
import logging
import os
from datetime import datetime

import requests as req
from flask import Flask, request, jsonify

from api.config import (
    ADMIN_ID,
    CACHE_TTL_MAPPING,
    MASTO_INSTANCE,
    MASTO_TOKEN,
    TG_API,
    TG_CHANNEL_ID,
    TG_TOKEN,
    TG_WEBHOOK_SECRET,
    get_missing_config,
    is_config_complete,
    redis,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


# ============ 辅助函数 ============

def verify_webhook(req_obj):
    """验证 Telegram Webhook 签名"""
    secret = TG_WEBHOOK_SECRET.encode()
    token = req_obj.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    return hmac.compare_digest(token, TG_WEBHOOK_SECRET)


def is_admin(user_id):
    """检查是否为管理员"""
    return user_id == ADMIN_ID


def check_rate_limit(user_id):
    """检查速率限制：每分钟最多 10 条消息"""
    if not redis:
        return True

    try:
        key = f'rate:{user_id}'
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


def send_tg_message(chat_id, text, reply_to=None):
    """发送 Telegram 消息"""
    try:
        payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
        if reply_to:
            payload['reply_to_message_id'] = reply_to
        resp = req.post(f'{TG_API}/sendMessage', json=payload, timeout=10)
        return resp.json() if resp.ok else None
    except req.exceptions.RequestException as e:
        logger.error(f"发送 Telegram 消息失败：{e}")
        return None


def edit_tg_message(chat_id, message_id, text):
    """编辑 Telegram 消息"""
    try:
        payload = {'chat_id': chat_id, 'message_id': message_id, 'text': text, 'parse_mode': 'HTML'}
        resp = req.post(f'{TG_API}/editMessageText', json=payload, timeout=10)
        return resp.ok
    except req.exceptions.RequestException as e:
        logger.error(f"编辑 Telegram 消息失败：{e}")
        return False


def delete_tg_message(chat_id, message_id):
    """删除 Telegram 消息"""
    try:
        payload = {'chat_id': chat_id, 'message_id': message_id}
        resp = req.post(f'{TG_API}/deleteMessage', json=payload, timeout=10)
        return resp.ok
    except req.exceptions.RequestException as e:
        logger.error(f"删除 Telegram 消息失败：{e}")
        return False


def post_to_mastodon(text, in_reply_to_id=None):
    """发布到 Mastodon"""
    try:
        headers = {'Authorization': f'Bearer {MASTO_TOKEN}'}
        payload = {'status': text, 'visibility': 'public'}
        if in_reply_to_id:
            payload['in_reply_to_id'] = in_reply_to_id
        resp = req.post(f'{MASTO_INSTANCE}/api/v1/statuses', headers=headers, json=payload, timeout=10)
        return resp.json() if resp.ok else None
    except req.exceptions.RequestException as e:
        logger.error(f"发布到 Mastodon 失败：{e}")
        return None


def edit_mastodon_status(status_id, text):
    """编辑 Mastodon 状态"""
    try:
        headers = {'Authorization': f'Bearer {MASTO_TOKEN}'}
        payload = {'status': text}
        resp = req.put(f'{MASTO_INSTANCE}/api/v1/statuses/{status_id}', headers=headers, json=payload, timeout=10)
        return resp.ok
    except req.exceptions.RequestException as e:
        logger.error(f"编辑 Mastodon 状态失败：{e}")
        return False


def delete_mastodon_status(status_id):
    """删除 Mastodon 状态"""
    try:
        headers = {'Authorization': f'Bearer {MASTO_TOKEN}'}
        resp = req.delete(f'{MASTO_INSTANCE}/api/v1/statuses/{status_id}', headers=headers, timeout=10)
        return resp.ok
    except req.exceptions.RequestException as e:
        logger.error(f"删除 Mastodon 状态失败：{e}")
        return False


def save_mapping(source_msg_id, tg_channel_msg_id, masto_status_id):
    """保存消息映射关系"""
    if not redis:
        return
    try:
        mapping = {
            'source': source_msg_id,
            'tg_channel': tg_channel_msg_id,
            'masto': masto_status_id,
            'timestamp': datetime.now().isoformat()
        }
        redis.setex(f'msg:{source_msg_id}', CACHE_TTL_MAPPING, json.dumps(mapping))
        logger.info(f"保存映射：source={source_msg_id}, tg={tg_channel_msg_id}, masto={masto_status_id}")
    except Exception as e:
        logger.error(f"保存映射失败：{e}")


def get_mapping(source_msg_id):
    """获取消息映射关系"""
    if not redis:
        return None
    try:
        data = redis.get(f'msg:{source_msg_id}')
        return json.loads(data) if data else None
    except Exception as e:
        logger.error(f"获取映射失败：{e}")
        return None


def delete_mapping(source_msg_id):
    """删除消息映射关系"""
    if redis:
        try:
            redis.delete(f'msg:{source_msg_id}')
            logger.info(f"删除映射：source={source_msg_id}")
        except Exception as e:
            logger.error(f"删除映射失败：{e}")


def send_inline_keyboard(chat_id, text, buttons):
    """发送带内联键盘的消息"""
    try:
        payload = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML',
            'reply_markup': {
                'inline_keyboard': buttons
            }
        }
        resp = req.post(f'{TG_API}/sendMessage', json=payload, timeout=10)
        return resp.json() if resp.ok else None
    except req.exceptions.RequestException as e:
        logger.error(f"发送内联键盘消息失败：{e}")
        return None


def answer_callback_query(callback_query_id, text=None, show_alert=False):
    """回应回调查询"""
    try:
        payload = {'callback_query_id': callback_query_id}
        if text:
            payload['text'] = text
            payload['show_alert'] = show_alert
        resp = req.post(f'{TG_API}/answerCallbackQuery', json=payload, timeout=10)
        return resp.ok
    except req.exceptions.RequestException as e:
        logger.error(f"回应回调查询失败：{e}")
        return False


def edit_message_text(chat_id, message_id, text):
    """编辑消息文本（用于更新按钮消息）"""
    try:
        payload = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        resp = req.post(f'{TG_API}/editMessageText', json=payload, timeout=10)
        return resp.ok
    except req.exceptions.RequestException as e:
        logger.error(f"编辑消息文本失败：{e}")
        return False


# ============ 命令处理函数 ============

def handle_start_command(user_id):
    """处理 /start 命令"""
    if not is_config_complete():
        # 配置未完成，显示配置提示和按钮
        missing = get_missing_config()
        missing_list = '\n'.join([f'• {item}' for item in missing])

        text = (
            '⚠️ <b>配置未完成</b>\n\n'
            f'缺少以下环境变量：\n{missing_list}\n\n'
            '📖 <b>配置指引：</b>\n'
            '请在 Vercel 项目设置中添加以上环境变量，然后重新部署。\n\n'
            '详细说明：\nhttps://github.com/Eyozy/syncpost'
        )

        buttons = [[{'text': '✅ 我已完成配置', 'callback_data': 'check_config'}]]
        send_inline_keyboard(user_id, text, buttons)
    else:
        # 配置完成，显示欢迎消息
        text = (
            '👋 <b>欢迎使用 SyncPost！</b>\n\n'
            '这是一个轻量级 Telegram 机器人，把你发给它的消息同步发布到 Telegram 频道和 Mastodon。\n\n'
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
            '📝 <b>使用方法</b>\n\n'
            '• 发送纯文本消息 → 自动同步到两端\n\n'
            '• 编辑已发送的消息 → 同步更新到两端\n\n'
            '• 回复消息后发送 /delete → 删除两端的内容\n\n'
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
            '📋 <b>可用命令</b>\n\n'
            '/start - 显示此帮助信息\n'
            '/delete - 删除已发布的消息 (回复消息后使用)\n\n'
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
            '⚠️ <b>注意事项</b>\n\n'
            '• 仅支持纯文本消息\n'
            '• 不支持图片、视频等多媒体\n'
            '• 不支持转发消息'
        )
        send_tg_message(user_id, text)


def handle_check_config_callback(callback):
    """处理"我已完成配置"按钮点击"""
    user_id = callback['from']['id']
    message_id = callback['message']['message_id']
    callback_query_id = callback['id']

    if is_config_complete():
        # 配置已完成，更新消息为欢迎消息
        text = (
            '✅ <b>配置检测通过！</b>\n\n'
            '👋 <b>欢迎使用 SyncPost！</b>\n\n'
            '这是一个轻量级 Telegram 机器人，把你发给它的消息同步发布到 Telegram 频道和 Mastodon。\n\n'
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
            '📝 <b>使用方法</b>\n\n'
            '• 发送纯文本消息 → 自动同步到两端\n\n'
            '• 编辑已发送的消息 → 同步更新到两端\n\n'
            '• 回复消息后发送 /delete → 删除两端的内容\n\n'
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
            '📋 <b>可用命令</b>\n\n'
            '/start - 显示此帮助信息\n'
            '/delete - 删除已发布的消息 (回复消息后使用)\n\n'
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
            '⚠️ <b>注意事项</b>\n\n'
            '• 仅支持纯文本消息\n'
            '• 不支持图片、视频等多媒体\n'
            '• 不支持转发消息'
        )
        edit_message_text(user_id, message_id, text)
        answer_callback_query(callback_query_id, '配置检测通过！')
    else:
        # 配置仍未完成
        missing = get_missing_config()
        missing_text = '、'.join(missing)
        answer_callback_query(
            callback_query_id,
            f'配置仍未完成，缺少：{missing_text}',
            show_alert=True
        )


# ============ 核心处理逻辑 ============

def handle_text_message(msg):
    """处理纯文本消息：发布到频道和 Mastodon"""
    text = msg.get('text', '').strip()
    if not text:
        send_tg_message(ADMIN_ID, '❌ 消息内容为空，无法发布')
        return

    logger.info(f"开始发布消息：{text[:50]}...")

    status_message = send_tg_message(
        ADMIN_ID,
        '⏳ <b>已收到</b>\n\n正在同步到 Telegram 频道和 Mastodon...',
        reply_to=msg['message_id']
    )
    status_message_id = None
    if status_message:
        status_message_id = status_message.get('result', {}).get('message_id')

    def finish(text):
        if status_message_id and edit_message_text(ADMIN_ID, status_message_id, text):
            return
        send_tg_message(ADMIN_ID, text, reply_to=msg['message_id'])

    # 发布到 Telegram 频道
    try:
        tg_resp = req.post(f'{TG_API}/sendMessage', json={
            'chat_id': TG_CHANNEL_ID,
            'text': text,
            'parse_mode': 'HTML'
        }, timeout=10)

        if not tg_resp.ok:
            logger.error(f"Telegram 发布失败：{tg_resp.text}")
            finish('❌ <b>发布失败</b>\n\nTelegram 频道发送失败')
            return

        tg_channel_msg_id = tg_resp.json()['result']['message_id']
        logger.info(f"Telegram 发布成功：msg_id={tg_channel_msg_id}")
    except req.exceptions.RequestException as e:
        logger.error(f"Telegram 请求异常：{e}")
        finish('❌ <b>发布失败</b>\n\nTelegram 频道发送失败')
        return

    # 发布到 Mastodon
    masto_data = post_to_mastodon(text)
    if not masto_data:
        finish(
            '⚠️ <b>部分发布成功</b>\n\n'
            '已同步到：\n'
            '• Telegram 频道\n\n'
            '未同步到：\n'
            '• Mastodon'
        )
        return

    masto_status_id = masto_data['id']
    logger.info(f"Mastodon 发布成功：status_id={masto_status_id}")

    # 保存映射关系
    save_mapping(msg['message_id'], tg_channel_msg_id, masto_status_id)

    # 发送成功提示
    finish(
        '✅ <b>发布成功</b>\n\n'
        '已同步到：\n'
        '• Telegram 频道\n'
        '• Mastodon'
    )


def handle_edit_message(msg):
    """处理消息编辑：同步编辑到两端"""
    source_msg_id = msg['message_id']
    new_text = msg.get('text', '').strip()

    if not new_text:
        send_tg_message(ADMIN_ID, '❌ 编辑后的内容为空')
        return

    # 获取映射关系
    mapping = get_mapping(source_msg_id)
    if not mapping:
        send_tg_message(ADMIN_ID, '❌ 未找到原消息的映射记录，无法编辑')
        return

    # 编辑 Telegram 频道消息
    tg_ok = edit_tg_message(TG_CHANNEL_ID, mapping['tg_channel'], new_text)

    # 编辑 Mastodon 状态
    masto_ok = edit_mastodon_status(mapping['masto'], new_text)

    if tg_ok and masto_ok:
        send_tg_message(ADMIN_ID,
            '✅ <b>编辑成功</b>\n\n'
            '已同步更新到两端',
            reply_to=source_msg_id
        )
    else:
        errors = []
        if not tg_ok:
            errors.append('Telegram')
        if not masto_ok:
            errors.append('Mastodon')
        send_tg_message(ADMIN_ID, f'❌ 编辑失败：{", ".join(errors)}')


def handle_delete_command(msg):
    """处理删除命令：删除两端的消息"""
    reply_to = msg.get('reply_to_message')
    if not reply_to:
        send_tg_message(ADMIN_ID, '❌ 请回复要删除的消息后使用 /delete 命令')
        return

    source_msg_id = reply_to['message_id']

    # 获取映射关系
    mapping = get_mapping(source_msg_id)
    if not mapping:
        send_tg_message(ADMIN_ID, '❌ 未找到原消息的映射记录，无法删除')
        return

    # 删除 Telegram 频道消息
    tg_ok = delete_tg_message(TG_CHANNEL_ID, mapping['tg_channel'])

    # 删除 Mastodon 状态
    masto_ok = delete_mastodon_status(mapping['masto'])

    # 删除映射记录
    delete_mapping(source_msg_id)

    # 删除源消息和命令消息
    delete_tg_message(ADMIN_ID, source_msg_id)
    delete_tg_message(ADMIN_ID, msg['message_id'])

    if tg_ok and masto_ok:
        send_tg_message(ADMIN_ID,
            '✅ <b>删除成功</b>\n\n'
            '已从两端删除此消息'
        )
    else:
        errors = []
        if not tg_ok:
            errors.append('Telegram')
        if not masto_ok:
            errors.append('Mastodon')
        send_tg_message(ADMIN_ID, f'⚠️ 部分删除失败：{", ".join(errors)}')


# ============ Webhook 路由 ============

@app.route('/webhook', methods=['POST'])
def webhook():
    """处理 Telegram Webhook"""
    if not verify_webhook(request):
        logger.warning("Webhook 验证失败")
        return 'Unauthorized', 401

    data = request.get_json()
    logger.info(f"收到 Webhook: {data.get('update_id', 'unknown')}")

    # 处理普通消息
    if 'message' in data:
        msg = data['message']
        user_id = msg.get('from', {}).get('id')

        # 检查是否为管理员
        if not is_admin(user_id):
            send_tg_message(user_id,
                '🚫 访问被拒绝\n\n'
                '此机器人仅供授权用户使用。\n'
                '如需使用，请联系管理员。'
            )
            return 'OK', 200

        # 检查速率限制
        if not check_rate_limit(user_id):
            send_tg_message(user_id,
                '⚠️ <b>速率限制</b>\n\n'
                '您的操作过于频繁，请稍后再试。\n'
                '限制：每分钟最多 10 条消息'
            )
            return 'OK', 200

        # 处理 /start 命令
        text = msg.get('text', '')
        if text.strip() == '/start':
            handle_start_command(user_id)
            return 'OK', 200

        # 配置未完成时，除了 /start 其他命令都不响应
        if not is_config_complete():
            return 'OK', 200

        # 检测转发消息
        if 'forward_from' in msg or 'forward_from_chat' in msg:
            send_tg_message(ADMIN_ID,
                '❌ 不支持转发消息\n\n'
                '请直接发送原创内容，不要转发其他聊天中的消息。'
            )
            return 'OK', 200

        # 检测多媒体内容
        if any(k in msg for k in ['photo', 'video', 'document', 'animation', 'media_group_id', 'audio', 'voice', 'sticker']):
            send_tg_message(ADMIN_ID,
                '❌ 不支持的内容类型\n\n'
                '此机器人仅支持纯文本消息。\n'
                '不支持图片、视频、文件等多媒体内容。'
            )
            return 'OK', 200

        # 处理 /delete 命令
        if text.strip() == '/delete':
            handle_delete_command(msg)
            return 'OK', 200

        # 处理普通文本消息
        if text:
            handle_text_message(msg)
            return 'OK', 200

    # 处理编辑消息
    if 'edited_message' in data:
        msg = data['edited_message']
        user_id = msg.get('from', {}).get('id')

        if not is_admin(user_id):
            return 'OK', 200

        if not is_config_complete():
            return 'OK', 200

        handle_edit_message(msg)
        return 'OK', 200

    # 处理回调查询（按钮点击）
    if 'callback_query' in data:
        callback = data['callback_query']
        user_id = callback['from']['id']

        if not is_admin(user_id):
            return 'OK', 200

        # 处理"我已完成配置"按钮
        if callback['data'] == 'check_config':
            handle_check_config_callback(callback)
            return 'OK', 200

    return 'OK', 200


@app.route('/setup', methods=['GET'])
def setup():
    """初始化 Webhook 和命令"""
    missing = get_missing_config()
    if missing:
        return f'配置不完整，缺少：{", ".join(missing)}', 500

    webhook_url = f'https://{request.host}/webhook'

    # 设置 Webhook
    webhook_resp = req.post(f'{TG_API}/setWebhook', json={
        'url': webhook_url,
        'secret_token': TG_WEBHOOK_SECRET,
        'allowed_updates': ['message', 'edited_message', 'callback_query']
    }, timeout=10)

    if not webhook_resp.ok:
        return f'Webhook 设置失败：{webhook_resp.text}', 500

    # 先删除所有旧命令
    delete_resp = req.post(f'{TG_API}/deleteMyCommands', timeout=10)
    if not delete_resp.ok:
        logger.warning(f'删除旧命令失败：{delete_resp.text}')

    # 设置新命令
    commands = [
        {'command': 'start', 'description': '显示欢迎消息'},
        {'command': 'delete', 'description': '删除已发布的消息（回复消息后使用）'}
    ]

    cmd_resp = req.post(f'{TG_API}/setMyCommands', json={'commands': commands}, timeout=10)

    if not cmd_resp.ok:
        return f'命令设置失败：{cmd_resp.text}', 500

    return f'✅ Webhook 已设置为 {webhook_url}，命令已注册（旧命令已清除）', 200


@app.route('/', methods=['GET'])
def index():
    """健康检查"""
    redis_status = 'disabled'
    if redis:
        try:
            redis.ping()
            redis_status = 'connected'
        except Exception:
            redis_status = 'error'

    config_status = 'complete' if is_config_complete() else 'incomplete'
    missing_config = get_missing_config() if not is_config_complete() else []

    health_data = {
        'status': 'ok',
        'service': 'SyncPost',
        'version': '1.0.0',
        'redis': redis_status,
        'config': config_status,
        'missing_config': missing_config,
        'timestamp': datetime.now().isoformat()
    }

    status_code = 200 if config_status == 'complete' and redis_status in ['connected', 'disabled'] else 503

    return jsonify(health_data), status_code
