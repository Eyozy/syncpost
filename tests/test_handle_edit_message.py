from api import clients, index, services


def test_handle_edit_message_updates_only_existing_target(monkeypatch):
    sent = []
    tg_edits = []
    masto_edits = []

    monkeypatch.setattr(index, 'get_mapping', lambda message_id: {
        'source': 401,
        'tg_channel': 501,
        'masto': None,
    } if message_id == 401 else None)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))
    monkeypatch.setattr(index, 'edit_tg_message', lambda chat_id, message_id, text: tg_edits.append((chat_id, message_id, text)) or True)
    monkeypatch.setattr(index, 'edit_mastodon_status', lambda status_id, text: masto_edits.append((status_id, text)) or True)

    index.handle_edit_message({'message_id': 401, 'text': 'updated text'})

    assert tg_edits == [(None, 501, 'updated text')]
    assert masto_edits == []
    assert sent == [
        (index.ADMIN_ID, '✅ <b>编辑成功</b>\n\n已同步更新到：\n• Telegram', 401),
    ]


def test_handle_edit_media_message_preserves_mastodon_media(monkeypatch):
    sent = []
    calls = []

    monkeypatch.setattr(index, 'get_mapping', lambda message_id: {
        'source': 402,
        'tg_channel': 502,
        'masto': 'status-402',
        'mastodon_media_id_list': ['media-402'],
    } if message_id == 402 else None)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))
    monkeypatch.setattr(
        clients,
        'edit_mastodon_status_with_existing_media',
        lambda status_id, text, media_ids=None: calls.append(('mastodon', status_id, text, media_ids)) or True,
    )
    monkeypatch.setattr(
        clients,
        'edit_tg_message_caption',
        lambda chat_id, message_id, text: calls.append(('telegram', chat_id, message_id, text)) or True,
    )

    index.handle_edit_message({
        'message_id': 402,
        'caption': 'updated caption',
        'video': {'file_id': 'video-1', 'file_size': 100},
    })

    assert calls == [
        ('mastodon', 'status-402', 'updated caption', ['media-402']),
        ('telegram', services.TG_CHANNEL_ID, 502, 'updated caption'),
    ]
    assert sent == [
        (index.ADMIN_ID, '✅ <b>编辑成功</b>\n\n已同步更新到：\n• Telegram、Mastodon', 402),
    ]
