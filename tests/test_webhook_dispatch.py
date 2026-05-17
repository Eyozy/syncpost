from api import index


def test_handle_incoming_message_rejects_unauthorized_user(monkeypatch):
    sent = []

    monkeypatch.setattr(index, 'is_admin', lambda user_id: False)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))

    handled = index.handle_incoming_message({'from': {'id': 777}, 'text': 'hello'})

    assert handled is True
    assert sent == [
        (777, '🚫 访问被拒绝\n\n此机器人仅供授权用户使用。\n如需使用，请联系管理员。', None),
    ]


def test_handle_incoming_message_routes_delete_command(monkeypatch):
    deleted = []

    monkeypatch.setattr(index, 'is_admin', lambda user_id: True)
    monkeypatch.setattr(index, 'check_rate_limit', lambda user_id: True)
    monkeypatch.setattr(index, 'is_config_complete', lambda: True)
    monkeypatch.setattr(index, 'is_supported_message', lambda msg: True)
    monkeypatch.setattr(index, 'handle_delete_command', lambda msg: deleted.append(msg))

    handled = index.handle_incoming_message({
        'from': {'id': 123},
        'text': '/delete',
        'reply_to_message': {'message_id': 1},
    })

    assert handled is True
    assert deleted == [{
        'from': {'id': 123},
        'text': '/delete',
        'reply_to_message': {'message_id': 1},
    }]


def test_handle_callback_routes_check_config(monkeypatch):
    handled_callbacks = []

    monkeypatch.setattr(index, 'is_admin', lambda user_id: True)
    monkeypatch.setattr(index, 'handle_check_config_callback', lambda callback: handled_callbacks.append(callback))

    handled = index.handle_callback({
        'from': {'id': 123},
        'data': 'check_config',
        'id': 'cb-1',
        'message': {'message_id': 1},
    })

    assert handled is True
    assert handled_callbacks == [{
        'from': {'id': 123},
        'data': 'check_config',
        'id': 'cb-1',
        'message': {'message_id': 1},
    }]


def test_webhook_returns_ok_when_json_payload_is_missing(monkeypatch):
    monkeypatch.setattr(index, 'verify_webhook', lambda req: True)

    with index.app.test_client() as client:
        response = client.post(
            '/webhook',
            data='not-json',
            headers={'X-Telegram-Bot-Api-Secret-Token': 'secret'},
            content_type='application/json',
        )

    assert response.status_code == 200
    assert response.data == b'OK'


def test_webhook_returns_ok_when_payload_is_not_an_object(monkeypatch):
    monkeypatch.setattr(index, 'verify_webhook', lambda req: True)

    with index.app.test_client() as client:
        response = client.post(
            '/webhook',
            json=['unexpected'],
            headers={'X-Telegram-Bot-Api-Secret-Token': 'secret'},
        )

    assert response.status_code == 200
    assert response.data == b'OK'


def test_handle_incoming_message_stops_when_config_is_incomplete(monkeypatch):
    sent = []

    monkeypatch.setattr(index, 'is_admin', lambda user_id: True)
    monkeypatch.setattr(index, 'check_rate_limit', lambda user_id: True)
    monkeypatch.setattr(index, 'is_config_complete', lambda: False)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))

    handled = index.handle_incoming_message({
        'from': {'id': 123},
        'text': 'hello',
    })

    assert handled is True
    assert sent == []


def test_handle_incoming_message_routes_media_groups(monkeypatch):
    handled_groups = []

    monkeypatch.setattr(index, 'is_admin', lambda user_id: True)
    monkeypatch.setattr(index, 'check_rate_limit', lambda user_id: True)
    monkeypatch.setattr(index, 'is_config_complete', lambda: True)
    monkeypatch.setattr(index, 'handle_media_group', lambda msg, base_url: handled_groups.append((msg, base_url)))

    with index.app.test_request_context('/webhook', base_url='https://example.com'):
        handled = index.handle_incoming_message({
            'from': {'id': 123},
            'message_id': 10,
            'media_group_id': 'album-1',
            'photo': [{'file_id': 'photo-1'}],
        })

    assert handled is True
    assert handled_groups == [({
        'from': {'id': 123},
        'message_id': 10,
        'media_group_id': 'album-1',
        'photo': [{'file_id': 'photo-1'}],
    }, 'https://example.com/')]


def test_handle_media_group_enqueues_internal_processing(monkeypatch):
    saved = []
    triggered = []

    monkeypatch.setattr(
        index,
        'handle_media_group_message',
        lambda *args: saved.append(args[0]),
    )
    monkeypatch.setattr(
        index,
        'enqueue_media_group_processing',
        lambda msg, base_url: triggered.append((msg, base_url)),
    )

    msg = {
        'from': {'id': 123},
        'message_id': 11,
        'media_group_id': 'album-2',
        'photo': [{'file_id': 'photo-2'}],
    }

    index.handle_media_group(msg, 'https://example.com/')

    assert saved == [msg]
    assert triggered == [(msg, 'https://example.com/')]


def test_unsupported_message_text_rejects_animation_messages():
    warning = index.unsupported_message_text({
        'animation': {'file_id': 'gif-1', 'mime_type': 'image/gif'},
    })

    assert warning == (
        '❌ 不支持的内容类型\n\n'
        '此机器人目前仅支持纯文本和静态图片。\n'
        '暂不支持视频、语音等其他媒体。'
    )


def test_internal_process_media_group_routes_to_service(monkeypatch):
    processed = []
    monkeypatch.setattr(index, 'TG_WEBHOOK_SECRET', 'secret')

    monkeypatch.setattr(
        index,
        'process_pending_media_group',
        lambda msg, send_tg_message, edit_message_text, telegram_request, post_to_mastodon, save_mapping, pop_ready_pending_media_group_items, logger: processed.append(msg),
    )

    with index.app.test_client() as client:
        response = client.post(
            '/internal/process-media-group',
            json={'message': {'message_id': 99, 'media_group_id': 'album-x'}},
            headers={'X-Internal-Token': 'secret'},
        )

    assert response.status_code == 200
    assert response.data == b'OK'
    assert processed == [{'message_id': 99, 'media_group_id': 'album-x'}]
