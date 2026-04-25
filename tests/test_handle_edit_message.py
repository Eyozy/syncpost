from api import index


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
