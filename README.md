# SyncPost

一个轻量级 Telegram 机器人，把你发给它的消息同步发布到 Telegram 频道和 Mastodon。

## 核心功能

- **纯文本同步**：发送文本消息自动同步到 Telegram 频道和 Mastodon
- **编辑同步**：编辑已发送的消息会同步更新到两端
- **跨平台删除**：回复消息并发送 `/delete` 可删除两端的内容

## 特点

- ✅ 极简设计，只做文本同步
- ✅ 不保存历史记录，不占用存储空间
- ✅ 智能配置检测，首次使用自动引导
- ✅ 仅授权用户可访问，非管理员自动拒绝
- ✅ 速率限制保护，防止滥用 (10 条/分钟)
- ✅ 健康检查端点，便于监控服务状态

## 项目结构

```text
syncpost/
├── api/
│   ├── __init__.py
│   ├── config.py
│   └── index.py
├── requirements.txt
├── vercel.json
└── README.md
```

## 快速开始

### 1. 部署

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/Eyozy/syncpost)

或手动部署：

```bash
git clone https://github.com/Eyozy/syncpost.git
cd syncpost
```

### 2. 获取凭证

**Telegram**

| 变量                | 获取方式                                                          |
| ------------------- | ----------------------------------------------------------------- |
| `TG_TOKEN`          | 向 [@BotFather](https://t.me/BotFather) 发送 `/newbot`，复制 token |
| `ADMIN_ID`          | 向 [@userinfobot](https://t.me/userinfobot) 发送消息，复制 `Id` 后的数字 |
| `TG_CHANNEL_ID`     | 创建公开频道（如 `@mychannel`），将机器人添加为管理员 |
| `TG_WEBHOOK_SECRET` | 随机字符串，用于 Webhook 安全验证 |

*提示：运行 `openssl rand -hex 32` 或使用[在线生成器](https://generate.plus/en/hex)创建 `TG_WEBHOOK_SECRET`*

**Mastodon**

| 变量             | 获取方式                                                            |
| ---------------- | ------------------------------------------------------------------- |
| `MASTO_INSTANCE` | 从浏览器复制实例 URL，如 `https://mastodon.social` |
| `MASTO_TOKEN`    | 设置 → 开发 → 新建应用 → 复制访问令牌 |

所需 Mastodon 权限：`write`, `write:statuses`, `write:media`

### 3. 配置 Redis

1. 打开 [Vercel Dashboard](https://vercel.com) 并选择你的项目
2. 点击 **Storage** → **Create Database** → 选择 **Upstash Redis**
3. 选择区域，创建数据库，然后点击 **Connect**

Vercel 会自动注入 Redis 连接变量，无需手动配置。

### 4. 配置环境变量

打开 **Settings** → **Environment Variables** 并添加：

| 变量                | 示例                      |
| ------------------- | ------------------------- |
| `ADMIN_ID`          | `123456789`               |
| `TG_TOKEN`          | `123456789:ABCdef...`     |
| `TG_CHANNEL_ID`     | `@mychannel`              |
| `TG_WEBHOOK_SECRET` | `9f4b8f7c...`             |
| `MASTO_INSTANCE`    | `https://mastodon.social` |
| `MASTO_TOKEN`       | `abc123def456...`         |

保存后重新部署。

### 5. 初始化机器人

部署完成后，在浏览器中访问以下 URL 注册 Webhook 和命令：

```text
https://<DOMAIN>/setup
```

成功响应：`✅ Webhook 已设置为 https://<DOMAIN>/webhook，命令已注册`

## 使用指南

### 首次使用

1. 向机器人发送 `/start` 命令
2. 如果配置未完成，会显示缺失的环境变量和配置指引
3. 完成配置后，点击"✅ 我已完成配置"按钮
4. 系统自动检测配置，通过后显示欢迎消息

### 发布内容

直接向机器人发送纯文本消息，内容会自动同步到 Telegram 频道和 Mastodon。

**成功提示：**
```
✅ 发布成功

已同步到：
• Telegram 频道
• Mastodon
```

### 编辑内容

在机器人聊天中，长按已发送的消息 → 编辑。编辑后的内容会同步更新到两端。

**成功提示：**
```
✅ 编辑成功

已同步更新到两端
```

### 删除内容

在机器人聊天中，长按已同步的源消息 → 回复 → 发送 `/delete`。两端的消息都会被删除。

**成功提示：**
```
✅ 删除成功

已从两端删除此消息
```

### 命令列表

| 命令       | 说明                               |
| --------- | ----------------------------------|
| `/start`  | 显示欢迎消息和使用说明                |
| `/delete` | 删除已同步的消息（需回复消息后使用）    |

### 错误提示

**非管理员访问：**
```
🚫 访问被拒绝

此机器人仅供授权用户使用。
如需使用，请联系管理员。
```

**速率限制：**
```
⚠️ 速率限制

您的操作过于频繁，请稍后再试。
限制：每分钟最多 10 条消息
```

**发送图片/视频：**
```
❌ 不支持的内容类型

此机器人仅支持纯文本消息。
不支持图片、视频、文件等多媒体内容。
```

**转发消息：**
```
❌ 不支持转发消息

请直接发送原创内容，不要转发其他聊天中的消息。
```

## 监控与维护

### 健康检查

访问根路径可获取服务状态：

```bash
curl https://<DOMAIN>/
```

**响应示例（配置完整）：**
```json
{
  "status": "ok",
  "service": "SyncPost",
  "version": "1.0.0",
  "redis": "connected",
  "config": "complete",
  "missing_config": [],
  "timestamp": "2026-03-07T12:00:00"
}
```

**响应示例（配置不完整）：**
```json
{
  "status": "ok",
  "service": "SyncPost",
  "version": "1.0.0",
  "redis": "disabled",
  "config": "incomplete",
  "missing_config": ["TG_TOKEN", "MASTO_TOKEN"],
  "timestamp": "2026-03-07T12:00:00"
}
```

### 状态码说明

| 状态码 | 说明 |
|--------|------|
| `200` | 服务正常，配置完整 |
| `503` | 服务异常或配置不完整 |

### 速率限制

为防止滥用，每个用户每分钟最多发送 **10 条消息**。触发限制后需等待 1 分钟才能继续使用。

**重要**: 速率限制功能依赖 Redis 存储。请确保已在 Vercel 项目中配置 Upstash Redis (参见上方"配置 Redis"章节)。Redis 配置完成后,速率限制会自动生效。

## 许可证

MIT License - 查看 [LICENSE](LICENSE) 文件。
