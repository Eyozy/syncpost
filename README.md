# SyncPost

SyncPost 是一个面向单人运营场景的轻量级 Telegram 同步机器人。你只需要向你的同步 Bot 发送一条消息，它就会把内容同步发布到 Telegram 频道和 Mastodon。

## 适合谁用

- 需要同时维护 Telegram 频道和 Mastodon 的个人创作者
- 想用 Telegram 私聊当作统一发布后台的用户
- 希望部署简单、依赖少、行为可预测的小型同步工具使用者

## 功能概览

- 纯文本消息同步发布到 Telegram 频道和 Mastodon
- 编辑私聊原消息时，同步更新已发布的平台内容
- 回复原消息并发送 `/delete`，删除已同步的平台内容
- 支持部分成功场景
  - 某个平台发布失败时，成功的平台仍可继续编辑和删除
  - 删除失败时保留映射，便于后续重试
- 管理员鉴权
- Postgres 映射存储与速率限制
- 健康检查接口
- 一键初始化 Webhook 和机器人命令

## 行为说明

### 发布

你在机器人私聊中发送纯文本消息后，机器人会：

1. 先回复一条“正在同步”的状态消息
2. 发布到 Telegram 频道
3. 发布到 Mastodon
4. 保存消息映射关系
5. 原地更新状态消息为最终结果

### 编辑

你直接编辑私聊里的原消息，机器人会：

- 更新所有已成功发布的平台内容
- 自动跳过当时未发布成功的平台

### 删除

你回复私聊中的原消息并发送 `/delete`，机器人会：

- 删除所有已成功同步的平台内容
- 删除你的私聊原消息和 `/delete` 命令消息
- 只有在平台删除成功后才清理映射
- 如果某个平台删除失败，会保留映射，方便后续继续重试

## 项目结构

```text
syncpost/
├── api/
│   ├── __init__.py
│   ├── clients.py
│   ├── config.py
│   ├── db.py
│   ├── index.py
│   └── messages.py
├── tests/
├── .env.example
├── requirements.txt
├── vercel.json
└── README.md
```

## 快速开始

### 1. 克隆并部署

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/Eyozy/syncpost)

或手动部署：

```bash
git clone https://github.com/Eyozy/syncpost.git
cd syncpost
```

### 2. 准备 Telegram 凭证

| 变量 | 说明 |
| --- | --- |
| `TG_TOKEN` | 从 [@BotFather](https://t.me/BotFather) 创建机器人后获取 |
| `ADMIN_ID` | 从 [@userinfobot](https://t.me/userinfobot) 获取你的 Telegram 用户 ID |
| `TG_CHANNEL_ID` | 你的频道用户名，如 `@mychannel`，并确保机器人已是管理员 |
| `TG_WEBHOOK_SECRET` | 自定义随机字符串，用于校验 Telegram Webhook |

生成 `TG_WEBHOOK_SECRET` 示例：

```bash
openssl rand -hex 32
```

### 3. 准备 Mastodon 凭证

| 变量 | 说明 |
| --- | --- |
| `MASTO_INSTANCE` | 你的 Mastodon 实例地址，例如 `https://mastodon.social` |
| `MASTO_TOKEN` | 在 Mastodon 中进入“设置 -> 开发”，创建应用后复制访问令牌 |

建议授权范围：

- `write`
- `write:statuses`

### 4. 配置 Neon Postgres

1. 打开 Vercel 项目
2. 进入 **Storage**
3. 创建 **Neon**
4. 连接到当前项目

Vercel 会自动注入 `DATABASE_URL`。

### 5. 配置环境变量

在 Vercel 项目的 **Settings -> Environment Variables** 中配置：

| 变量 | 示例 |
| --- | --- |
| `ADMIN_ID` | `123456789` |
| `TG_TOKEN` | `123456:ABC...` |
| `TG_CHANNEL_ID` | `@mychannel` |
| `TG_WEBHOOK_SECRET` | `9f4b8f7c...` |
| `MASTO_INSTANCE` | `https://mastodon.social` |
| `MASTO_TOKEN` | `abc123def456...` |
| `DATABASE_URL` | `postgresql://neondb_owner:your-password@ep-cool-darkness-123456.ap-southeast-1.aws.neon.tech/neondb?sslmode=require` |

配置完成后重新部署。

### 6. 初始化机器人

部署完成后，访问：

```text
https://<YOUR_DOMAIN>/setup
```

成功后会：

- 初始化数据库表
- 注册 Telegram Webhook
- 清理旧命令
- 注册 `/start` 和 `/delete`

## 使用方式

### `/start`

显示欢迎信息；如果配置未完成，会提示缺失的环境变量，并展示检测按钮。

### 发布消息

直接向机器人发送纯文本：

```text
Hello, world
```

返回结果：

```text
✅ 发布成功

已同步到：
• Telegram 频道
• Mastodon
```


```text
⚠️ 部分发布成功

已同步到：
• Telegram 频道

未同步到：
• Mastodon
```

### 编辑消息

直接编辑你发给机器人的原消息。如果只有 Telegram 发布成功，那么编辑时只会更新 Telegram，不会因为 Mastodon 失败而中断。

示例返回：

```text
✅ 编辑成功

已同步更新到：
• Telegram
```

### 删除消息

回复原消息发送：

```text
/delete
```

如果两边都存在：

```text
✅ 删除成功

已从以下平台删除此消息：
• Telegram、Mastodon
```

如果只有 Telegram 成功：

```text
✅ 删除成功

已从以下平台删除此消息：
• Telegram
```

如果某个平台删除失败：

```text
⚠️ 部分删除失败：Telegram
```

这时映射会被保留，后续可以继续尝试删除。

## 限制说明

- 仅支持纯文本
- 不支持图片、视频、文件、贴纸、语音等多媒体
- 不支持转发消息
- 默认每分钟最多 10 条操作
- 消息映射依赖 Postgres；如果数据库不可用，旧消息将无法继续编辑或删除

## 接口说明

### `GET /`

健康检查接口。

示例：

```bash
curl "https://your-domain.vercel.app/"
```

返回示例：

```json
{
  "status": "ok",
  "service": "SyncPost",
  "version": "1.0.0",
  "database": "connected",
  "config": "complete",
  "missing_config": [],
  "timestamp": "2026-03-07T12:00:00"
}
```

### `POST /webhook`

Telegram Webhook 入口。

这个接口通常由 Telegram 自动调用，不需要手动长期访问。如果要本地或线上排查，可以用一个最小示例请求验证服务是否正常：

```bash
curl -X POST "https://your-domain.vercel.app/webhook" \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: your-webhook-secret" \
  -d '{
    "update_id": 10001,
    "message": {
      "message_id": 1,
      "text": "/start",
      "from": {
        "id": 123456789
      }
    }
  }'
```

说明：

- `X-Telegram-Bot-Api-Secret-Token` 必须与 `TG_WEBHOOK_SECRET` 一致
- `from.id` 必须是你的 `ADMIN_ID`
- 正常情况下返回 `OK`

### `GET /setup`

初始化 Webhook 和机器人命令。

示例：

```bash
curl "https://your-domain.vercel.app/setup"
```

成功响应示例：

```text
✅ Webhook 已设置为 https://your-domain.vercel.app/webhook，命令已注册（旧命令已清除）
```

## 本地测试

运行测试：

```bash
python3 -m pytest tests
```

## 许可证

MIT，详见 [LICENSE](LICENSE)。
