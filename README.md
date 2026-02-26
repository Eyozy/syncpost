# SyncPost

A lightweight Telegram bot that syncs your messages to Telegram channels and Mastodon.

## Features

- **Dual-platform sync**: Publish simultaneously to Telegram channel and Mastodon
- **Multimedia support**: Text and images (with captions)
- **Thread association**: Reply to history messages to create cross-platform threads
- **Forward reblog**: Forward channel messages to trigger Mastodon boost
- **Real-time feedback**: HTML cards showing sync results
- **Deduplication**: Redis-based 5-minute lock
- **Access control**: Admin-only
- **Serverless**: Zero maintenance on Vercel

## Project Structure

```
syncpost/
├── api/index.py      # Main app (Flask + Webhook)
├── requirements.txt  # Python dependencies
├── vercel.json       # Vercel routing config
└── README.md
```

## Quick Start

### 1. Get Credentials

#### Telegram

| Variable        | How to get                                                                                     |
|-----------------|------------------------------------------------------------------------------------------------|
| `TG_TOKEN`      | Message [@BotFather](https://t.me/BotFather) `/newbot`, follow prompts, copy the token         |
| `ADMIN_ID`      | Message [@userinfobot](https://t.me/userinfobot) and copy the number after `Id`                |
| `TG_CHANNEL_ID` | Create a public channel → note the link (e.g., `t.me/mychannel`) → add your bot as admin       |

#### Mastodon

| Variable         | How to get                                                                                      |
|------------------|-------------------------------------------------------------------------------------------------|
| `MASTO_INSTANCE` | Copy your instance URL from the browser (e.g., `https://mastodon.social`)                       |
| `MASTO_TOKEN`    | Preferences → Development → New Application → fill name → check scopes below → copy access token |

**Required scopes:**
- [x] `write`
- [x] `write:statuses`
- [x] `write:media`

### 2. Deploy

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/Eyozy/syncpost)

Or manually:

```bash
git clone https://github.com/Eyozy/syncpost.git
cd syncpost
```

### 3. Configure Redis

1. Go to [Vercel Dashboard](https://vercel.com) → select your project
2. Click **Storage** → **Create Database**
3. Select **Upstash Redis**
4. Choose region (recommended: **Washington, D.C.**) → **Create**
5. Click **Connect** — Vercel will auto-inject Redis env vars

### 4. Configure Environment Variables

1. Project page → **Settings** → **Environment Variables**
2. Add these 5 variables:

| Variable         | Value                     | Example                                        |
|------------------|---------------------------|------------------------------------------------|
| `ADMIN_ID`       | Your Telegram user ID     | `123456789`                                    |
| `TG_TOKEN`       | Token from BotFather      | `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`         |
| `TG_CHANNEL_ID`  | Channel username (with @) | `@mychannel`                                   |
| `MASTO_TOKEN`    | Mastodon access token     | `abc123def456ghi789`                           |
| `MASTO_INSTANCE` | Mastodon instance URL     | `https://mastodon.social`                      |

3. Click **Save**
4. Go to **Deployments** → latest deployment → three dots → **Redeploy** (env vars require redeploy)

### 5. Set Webhook

Visit this URL in your browser (replace `<TG_TOKEN>` and `<DOMAIN>`):

```
https://api.telegram.org/bot<TG_TOKEN>/setWebhook?url=https://<DOMAIN>/webhook
```

Success response:
```json
{"ok":true,"result":true}
```

## Usage

| Action        | Steps                                                     |
|---------------|-----------------------------------------------------------|
| Send message  | DM the bot with text or image                             |
| Create thread | Long-press a message in bot chat → Reply → send           |
| Reblog        | Long-press a channel message → Forward to bot DM          |

**Sync result example:**
```
✅ 📝 Post · All succeeded
> This is a test message…

📊 Sync result (2/2)
📱 Telegram ✓
🐘 Mastodon ✓
```

## License

MIT License - See [LICENSE](LICENSE) file