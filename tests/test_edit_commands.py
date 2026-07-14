from api import clients, services


def mapping(_message_id):
    return {
        "source": 1,
        "tg_channel": 10,
        "masto": "status-1",
        "source_text": "old text",
        "source_media": {
            "file_id": "old",
            "file_size": 100,
            "mime_type": "video/mp4",
            "source_kind": "video",
            "original_filename": None,
        },
    }


def send_collector(messages):
    return lambda chat_id, text, reply_to=None: messages.append((text, reply_to))


def test_edit_command_recognizes_explicit_operations():
    commands = {
        "/edit text": "edit",
        "/edit_image_text text": "edit_image_text",
        "/replace_image": "replace_image",
        "/replace_image_text text": "replace_image_text",
        "/edit_video_text text": "edit_video_text",
        "/replace_video": "replace_video",
        "/replace_video_text text": "replace_video_text",
    }

    for text, expected in commands.items():
        assert services.edit_command({"text": text}) == expected


def test_edit_rejects_image_post():
    messages = []
    services.edit_replied_message(
        {
            "text": "/edit new text",
            "reply_to_message": {"message_id": 1, "photo": [{"file_id": "old"}]},
        },
        send_collector(messages),
        mapping,
        None,
        bool,
    )

    assert "只适用于纯文本帖子" in messages[0][0]


def test_edit_image_text_keeps_image(monkeypatch):
    calls = []
    messages = []
    monkeypatch.setattr(
        clients,
        "edit_tg_message_caption",
        lambda chat_id, message_id, text: calls.append(("telegram", text)) or True,
    )
    monkeypatch.setattr(
        services,
        "edit_mastodon_media_text_from_telegram",
        lambda status_id, text, media: calls.append(("mastodon", text, media.source_kind)) or True,
    )

    services.edit_replied_message(
        {
            "text": "/edit_image_text new text",
            "reply_to_message": {"message_id": 1, "photo": [{"file_id": "old"}]},
        },
        send_collector(messages),
        mapping,
        None,
        bool,
    )

    assert calls == [("mastodon", "new text", "photo"), ("telegram", "new text")]
    assert messages == [("✅ <b>文字编辑成功</b>", 1)]


def test_replace_image_keeps_original_text(monkeypatch):
    calls = []
    messages = []
    monkeypatch.setattr(
        services,
        "download_media_file",
        lambda *args: {"content": b"image", "filename": "new.jpg"},
    )
    monkeypatch.setattr(clients, "upload_mastodon_media", lambda *args: {"id": "media-2"})
    monkeypatch.setattr(
        clients,
        "edit_tg_media_message",
        lambda *args: calls.append(("telegram", args[5], args[6])) or True,
    )
    monkeypatch.setattr(
        clients,
        "edit_mastodon_status_media",
        lambda status_id, text, media_id: calls.append(("mastodon", text, media_id)) or True,
    )

    services.edit_replied_message(
        {
            "caption": "/replace_image",
            "photo": [{"file_id": "new"}],
            "reply_to_message": {
                "message_id": 1,
                "caption": "old text",
                "photo": [{"file_id": "old"}],
            },
        },
        send_collector(messages),
        mapping,
        None,
        bool,
    )

    assert calls == [
        ("telegram", "old text", "photo"),
        ("mastodon", "old text", "media-2"),
    ]
    assert messages == [("✅ <b>图片替换成功</b>", 1)]


def test_replace_image_text_requires_new_text():
    messages = []
    services.edit_replied_message(
        {
            "caption": "/replace_image_text",
            "photo": [{"file_id": "new"}],
            "reply_to_message": {"message_id": 1, "photo": [{"file_id": "old"}]},
        },
        send_collector(messages),
        mapping,
        None,
        bool,
    )

    assert "填写新的文字内容" in messages[0][0]


def test_replace_image_requires_new_image():
    messages = []
    services.edit_replied_message(
        {
            "text": "/replace_image",
            "reply_to_message": {"message_id": 1, "photo": [{"file_id": "old"}]},
        },
        send_collector(messages),
        mapping,
        None,
        bool,
    )

    assert "发送一个新的图片" in messages[0][0]


def test_edit_video_text_keeps_video(monkeypatch):
    calls = []
    monkeypatch.setattr(
        clients,
        "edit_tg_message_caption",
        lambda chat_id, message_id, text: calls.append(("telegram", text)) or True,
    )
    monkeypatch.setattr(
        services,
        "edit_mastodon_media_text_from_telegram",
        lambda status_id, text, media: calls.append(("mastodon", text, media.source_kind)) or True,
    )

    services.edit_replied_message(
        {
            "text": "/edit_video_text new text",
            "reply_to_message": {
                "message_id": 1,
                "video": {"file_id": "old", "file_size": 100},
            },
        },
        lambda *args, **kwargs: None,
        mapping,
        None,
        bool,
    )

    assert calls == [("mastodon", "new text", "video"), ("telegram", "new text")]


def test_edit_video_text_uses_saved_source_media_for_status_alias(monkeypatch):
    calls = []
    monkeypatch.setattr(
        services,
        "edit_mastodon_media_text_from_telegram",
        lambda status_id, text, media: calls.append(("mastodon", text, media.file_id)) or True,
    )
    monkeypatch.setattr(
        clients,
        "edit_tg_message_caption",
        lambda chat_id, message_id, text: calls.append(("telegram", text)) or True,
    )

    services.edit_replied_message(
        {
            "text": "/edit_video_text new text",
            "reply_to_message": {"message_id": 1, "text": "✅ 发布成功"},
        },
        lambda *args, **kwargs: None,
        mapping,
        None,
        bool,
    )

    assert calls == [("mastodon", "new text", "old"), ("telegram", "new text")]


def test_replace_video_text_updates_media_and_text(monkeypatch):
    calls = []
    monkeypatch.setattr(services, "mastodon_video_size_limit", lambda: 20 * 1024 * 1024)
    monkeypatch.setattr(
        services,
        "download_media_file",
        lambda *args: {"content": b"video", "filename": "new.mp4"},
    )
    monkeypatch.setattr(clients, "upload_mastodon_media", lambda *args: {"id": "media-2"})
    monkeypatch.setattr(
        clients,
        "edit_tg_media_message",
        lambda *args: calls.append(("telegram", args[5], args[6])) or True,
    )
    monkeypatch.setattr(
        clients,
        "edit_mastodon_status_media",
        lambda status_id, text, media_id: calls.append(("mastodon", text, media_id)) or True,
    )

    services.edit_replied_message(
        {
            "caption": "/replace_video_text new text",
            "video": {"file_id": "new", "file_size": 100},
            "reply_to_message": {
                "message_id": 1,
                "caption": "old text",
                "video": {"file_id": "old", "file_size": 100},
            },
        },
        lambda *args, **kwargs: None,
        mapping,
        None,
        bool,
    )

    assert calls == [
        ("telegram", "new text", "video"),
        ("mastodon", "new text", "media-2"),
    ]


class MastodonResponse:
    ok = True

    def json(self):
        return {"media_attachments": [{"id": "media-1"}]}


def test_mastodon_plain_text_edit_sends_only_status(monkeypatch):
    payloads = []
    monkeypatch.setattr(
        clients,
        "mastodon_put_form",
        lambda path, payload: payloads.append(payload) or MastodonResponse(),
    )

    assert clients.edit_mastodon_status("status-1", "new text")
    assert payloads == [[("status", "new text")]]


def test_mastodon_media_edit_uses_form_data(monkeypatch):
    payloads = []
    monkeypatch.setattr(clients, "wait_for_mastodon_media", lambda media_id: True)
    monkeypatch.setattr(
        clients,
        "mastodon_put_form",
        lambda path, payload: payloads.append(payload) or MastodonResponse(),
    )

    assert clients.edit_mastodon_status_media("status-1", "new text", "media-2")
    assert payloads == [[("status", "new text"), ("media_ids[]", "media-2")]]


def test_mastodon_media_text_edit_reuploads_telegram_media(monkeypatch):
    calls = []
    media = services.MediaPayload(
        file_id="video-1",
        file_size=100,
        mime_type="video/mp4",
        source_kind="video",
        original_filename="old.mp4",
    )
    monkeypatch.setattr(
        services,
        "download_media_file",
        lambda *args: {"content": b"video", "filename": "old.mp4"},
    )
    monkeypatch.setattr(
        clients,
        "upload_mastodon_media",
        lambda content, filename, mime_type: calls.append(("upload", filename, mime_type)) or {"id": "media-new"},
    )
    monkeypatch.setattr(
        clients,
        "edit_mastodon_status_media",
        lambda status_id, text, media_id: calls.append(("edit", status_id, text, media_id)) or True,
    )

    assert services.edit_mastodon_media_text_from_telegram("status-1", "new text", media)
    assert calls == [
        ("upload", "old.mp4", "video/mp4"),
        ("edit", "status-1", "new text", "media-new"),
    ]
