WELCOME_TEXT = (
    "👋 <b>欢迎使用 SyncPost！</b>\n\n"
    "这是一个轻量级 Telegram 机器人，"
    "把你发给它的消息同步发布到 Telegram 频道和 Mastodon。\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "📝 <b>使用方法</b>\n\n"
    "• 发送纯文本消息 → 自动同步到两端\n\n"
    "• 编辑已发送的消息 → 同步更新到两端\n\n"
    "• 回复消息后发送 /delete → 删除两端的内容\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "📋 <b>可用命令</b>\n\n"
    "/start - 显示此帮助信息\n"
    "/delete - 删除已发布的消息 (回复消息后使用)\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "⚠️ <b>注意事项</b>\n\n"
    "• 仅支持纯文本消息\n"
    "• 不支持图片、视频等多媒体\n"
    "• 不支持转发消息"
)

SYNCING_TEXT = "⏳ <b>已收到</b>\n\n正在同步到 Telegram 频道和 Mastodon..."
PUBLISH_SUCCESS_TEXT = "✅ <b>发布成功</b>\n\n已同步到：\n• Telegram 频道\n• Mastodon"
PARTIAL_PUBLISH_TEXT = (
    "⚠️ <b>部分发布成功</b>\n\n已同步到：\n• Telegram 频道\n\n未同步到：\n• Mastodon"
)
