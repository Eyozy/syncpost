# SyncPost Product Requirements Document (PRD)

## 1. Product Overview
SyncPost acts as an intelligent cross-platform synchronizer. It forwards messages, images, videos, and GIFs sent to the Bot by the Admin directly to a designated Telegram Channel and Mastodon account.

This PRD outlines 5 epic features to be developed to enhance media support, UX reliability, and platform parity.

---

## 2. Media Compression Prevention Protocol
**If you want lossless media: ALWAY use "Send as File" in the Telegram chat.**

| Media Type | How you send to Bot (Lossless) | How Bot sends to Channel | How Bot sends to Mastodon | Why? |
| :--- | :--- | :--- | :--- | :--- |
| **Photo** | 🗂 **Send as File (Uncheck Compress)** | 🖼 Native Photo (`sendPhoto`) | 🖼 Native Photo | Telegram won't compress the file upload. We serve as a lossless proxy. |
| **GIF** | 🗂 **Send as File (.mp4 / .gif)** | 🔁 Autoplay GIF (`sendAnimation`) | 🔁 Autoplay GIF | Telegram ruins direct GIF uploads into low-res MP4s. File transfer prevents this. |
| **Video** | 🗂 **Send as File (.mp4)** | 🎬 Native Video (`sendVideo`) | 🎬 Native Video | Same logic. Avoids Telegram's forced 480p/720p client-side downscaling. |

---

## 3. UI Mockups (English Version)

### Phase 1: Video & GIF Supported

<div style="max-width: 400px; background-color: #1c242d; border-radius: 12px; padding: 12px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <div style="color: #ffffff; font-size: 15px; line-height: 1.4;">
    <b>✅ 🎬 Post Video · All succeeded</b><br>
    <blockquote style="margin: 4px 0 8px 0; padding-left: 8px; border-left: 3px solid #4caf50; color: #a4b4c1; font-size: 14px;">
      Check out this amazing seaside view...
    </blockquote>
    <b>📊 Sync result (2/2)</b><br><br>
    📱 <b>Telegram</b> ✓<br>
    🐘 <b>Mastodon</b> ✓
  </div>
</div>

### Phase 3: Gallery (1-4 items) & Over-limit Rejection (>4)

**A. Sent 3 Media Items (Grouped)**

<div style="max-width: 400px; background-color: #1c242d; border-radius: 12px; padding: 12px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <div style="color: #ffffff; font-size: 15px; line-height: 1.4;">
    <b>✅ 🖼 Post Gallery · All succeeded</b><br>
    <blockquote style="margin: 4px 0 8px 0; padding-left: 8px; border-left: 3px solid #4caf50; color: #a4b4c1; font-size: 14px;">
      [3 media items] Visited an art exhibition today...
    </blockquote>
  </div>
</div>

**B. Sent 5 Media Items (Blocked & Rejected)**

<div style="max-width: 400px; background-color: #1c242d; border-radius: 12px; padding: 12px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <div style="color: #ffffff; font-size: 15px; line-height: 1.4;">
    <b>⚠️ Sync Blocked · Media Limit Exceeded</b><br>
    <blockquote style="margin: 4px 0 8px 0; padding-left: 8px; border-left: 3px solid #f44336; color: #a4b4c1; font-size: 14px;">
      [Request contains 5 media files]
    </blockquote>
    Mastodon natively supports a maximum of <b>4</b> media attachments per post.<br><br>
    <i>💡 To ensure cross-platform consistency, this sync has been cancelled. Please select up to 4 items and try again.</i>
  </div>
</div>

### Phase 4: Edit & Delete Sync (Inline Buttons)

**A. Initial Success State (with Delete Button)**

<div style="max-width: 400px; background-color: #1c242d; border-radius: 12px; padding: 12px 12px 0 12px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <div style="color: #ffffff; font-size: 15px; line-height: 1.4; padding-bottom: 12px;">
    <b>✅ 📝 Post · All succeeded</b><br>
    <blockquote style="margin: 4px 0 8px 0; padding-left: 8px; border-left: 3px solid #4caf50; color: #a4b4c1; font-size: 14px;">
      This sentence feels a bit off...
    </blockquote>
  </div>
  <div style="border-top: 1px solid #10161b; margin: 0 -12px; padding: 6px;">
    <div style="background-color: rgba(244,67,54,0.1); color: #f44336; text-align: center; padding: 10px; border-radius: 8px; font-weight: 500; font-size: 15px; cursor: pointer; border: 1px solid rgba(244,67,54,0.3);">
      🗑 Withdraw from all platforms
    </div>
  </div>
</div>

**B. You "Edited" the original message in chat**

<div style="max-width: 400px; background-color: #1c242d; border-radius: 12px; padding: 12px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <div style="color: #ffffff; font-size: 15px; line-height: 1.4;">
    <b>✏️ Content Updated Globally</b><br>
    <blockquote style="margin: 4px 0 8px 0; padding-left: 8px; border-left: 3px solid #2196f3; color: #a4b4c1; font-size: 14px;">
      This sentence has been corrected. <span style="background-color: rgba(33,150,243,0.2); padding: 0 4px; border-radius: 3px; font-size: 12px; color: #2196f3;">(edited)</span>
    </blockquote>
    <b>📊 Update result (2/2)</b><br><br>
    📱 <b>Telegram</b> Updated ✓<br>
    🐘 <b>Mastodon</b> Updated ✓
  </div>
</div>

**C. You clicked the [🗑 Withdraw] button**

<div style="max-width: 400px; background-color: #1c242d; border-radius: 12px; padding: 12px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; opacity: 0.65;">
  <div style="color: #ffffff; font-size: 15px; line-height: 1.4;">
    <b>🗑 Post Removed Globally</b><br>
    <blockquote style="margin: 4px 0 8px 0; padding-left: 8px; border-left: 3px solid #757575; color: #8899a6; font-size: 14px; text-decoration: line-through;">
      This sentence feels a bit off...
    </blockquote>
    <b>📊 Delete result (2/2)</b><br><br>
    📱 <b>Telegram</b> Deleted ✓<br>
    🐘 <b>Mastodon</b> Deleted ✓
  </div>
</div>

### Phase 5: Long Text Split (Auto Threads)

<div style="max-width: 400px; background-color: #1c242d; border-radius: 12px; padding: 12px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <div style="color: #ffffff; font-size: 15px; line-height: 1.4;">
    <b>✅ 📝 Post Thread · All succeeded</b><br>
    <blockquote style="margin: 4px 0 8px 0; padding-left: 8px; border-left: 3px solid #4caf50; color: #a4b4c1; font-size: 14px;">
      Today I want to talk about open source...
    </blockquote>
    <b>📊 Sync result (4/4)</b><br><br>
    📱 <b>Telegram</b> (Full text) ✓<br>
    🐘 <b>Mastodon</b> Part 1/3 (Cover included) ✓<br>
    🐘 <b>Mastodon</b> Part 2/3 (Reply) ✓<br>
    🐘 <b>Mastodon</b> Part 3/3 (Reply) ✓
  </div>
</div>

---

## 4. Epic Features Code Logic Implementation Guides

### Epic 1: Comprehensive Media Support (Videos & GIFs)
**Implementation Logic**:
- Parse `msg.get("video")` and `msg.get("animation")` in the Telegram webhook payload.
- Create new API wrappers: `tg_send_video(chat_id, video, caption)` and `tg_send_animation(chat_id, animation, caption)`.
- Use `sendVideo` and `sendAnimation` Telegram methods.
- Upload to Mastodon using the correct MIME types (`video/mp4` or `image/gif`).

### Epic 2: Async Webhook & Timeout Prevention
**Implementation Logic**:
- Telegram APIs demand a 200 OK response within 10 seconds, otherwise it retries multiple times, leading to duplicate posts.
```python
import threading

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    # Execute sync in background to prevent webhook timeout
    thread = threading.Thread(target=sync_process, args=(data,))
    thread.start()
    return "ok", 200
```

### Epic 3: Media Gallery Logic & 4-Item Limit (MVP)
**Implementation Logic**:
- Intercept messages containing a `media_group_id`.
- Buffer these IDs in Redis for a ~2-second debounce mechanism to aggregate the album list.
- Check the parsed list: If `len > 4`, block execution and send the RED Error UI Card instead.
- If `1 <= len <= 4`, aggregate Media IDs and use `sendMediaGroup` on Telegram instead of individual sync calls.

### Epic 4: Cross-Platform Edit/Delete Synchronization
**Implementation Logic - Edit:**
- Listen for `edited_message` in the webhook payload.
```python
# Pseudo code
if data.get("edited_message"):
    msg = data["edited_message"]
    # 1. Fetch mapping and original tg_cid/ma_id from Redis
    # 2. tg_edit(TG_CHANNEL_ID, tg_cid, new_text)
    # 3. masto.status_update(ma_id, new_text)
    # 4. Morph original Bot card to Blue: "✏️ Content Updated Globally ... (edited)"
```
**Implementation Logic - Delete:**
- Attach an Inline Keyboard Button payload to successful syncs:
  `reply_markup={"inline_keyboard": [[{"text": "🗑 Withdraw from all platforms", "callback_data": f"del_{msg_id}"}]]}`
- Intercept webhook events starting with `callback_query`.
- Retrieve post identifiers from Redis.
- Call `deleteMessage` on Telegram and `status_delete` on Mastodon.
- Call `editMessageText` to morph the successful card into the GRAY Strikethrough UI Card.

### Epic 5: Long-Text Auto-Splitting (Mastodon Threads)
**Implementation Logic**:
- Trigger condition: `len(content) > 400`
- **Telegram**: Send as one single, uninterrupted message (up to 4096 chars).
- **Mastodon**: 
```python
# Pseudo code
parts = split_text_by_paragraphs(content, max_len=450)
prev_id = None
for i, part in enumerate(parts):
    flag = f" ({i+1}/{len(parts)})"
    prev_id = masto.status_post(part + flag, in_reply_to_id=prev_id)
# Return array of success statements for UI render
```

---
**Execution Plan Summary**:
Implementation will follow Epic 1 through Epic 5 sequentially. Each Epic boundary requires local branch functional verification and an atomic Git commit based on Conventional Commits structure before merging.
