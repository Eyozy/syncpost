from api import index
from api import services


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
    monkeypatch.setattr(index, 'handle_media_group', lambda msg: handled_groups.append(msg))

    handled = index.handle_incoming_message({
        'from': {'id': 123},
        'message_id': 10,
        'media_group_id': 'album-1',
        'photo': [{'file_id': 'photo-1'}],
    })

    assert handled is True
    assert handled_groups == [{
        'from': {'id': 123},
        'message_id': 10,
        'media_group_id': 'album-1',
        'photo': [{'file_id': 'photo-1'}],
    }]


def test_handle_incoming_message_rejects_unsupported_document_media_group(monkeypatch):
    sent = []
    handled_groups = []

    monkeypatch.setattr(index, 'is_admin', lambda user_id: True)
    monkeypatch.setattr(index, 'check_rate_limit', lambda user_id: True)
    monkeypatch.setattr(index, 'is_config_complete', lambda: True)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))
    monkeypatch.setattr(index, 'handle_media_group', lambda msg: handled_groups.append(msg))

    handled = index.handle_incoming_message({
        'from': {'id': 123},
        'message_id': 11,
        'media_group_id': 'album-bad-1',
        'document': {
            'file_id': 'doc-1',
            'mime_type': 'application/pdf',
            'file_name': 'report.pdf',
        },
    })

    assert handled is True
    assert handled_groups == []
    assert sent == [
        (
            index.ADMIN_ID,
            '❌ 不支持的文件类型\n\n仅支持作为文件发送的静态图片 (JPG, PNG, WebP, HEIC, HEIF等)。',
            None,
        ),
    ]


def test_handle_text_message_publishes_directly(monkeypatch):
    published = []

    monkeypatch.setattr(index, 'publish_message', lambda *args, **kwargs: published.append(args[0]))

    msg = {
        'from': {'id': 123},
        'message_id': 77,
        'text': 'hello',
    }

    index.handle_text_message(msg)

    assert published == [msg]


def test_handle_media_group_processes_inline_after_wait(monkeypatch):
    handled = []
    processed = []
    deleted_states = []

    monkeypatch.setattr(index, 'handle_media_group_message', lambda *args: handled.append(args[0]))
    monkeypatch.setattr(index.time, 'sleep', lambda seconds: processed.append(('slept', seconds)))
    monkeypatch.setattr(index, 'get_pending_media_group_items', lambda media_group_id: [])
    monkeypatch.setattr(index, 'delete_pending_media_group_items', lambda media_group_id: None)
    monkeypatch.setattr(index, 'delete_media_group_state', lambda media_group_id: deleted_states.append(media_group_id))

    def fake_process(*args, **kwargs):
        processed.append(('processed', args[0], kwargs))
        return True

    monkeypatch.setattr(index, 'process_pending_media_group', fake_process)

    msg = {
        'from': {'id': 123},
        'message_id': 88,
        'media_group_id': 'album-5',
        'photo': [{'file_id': 'photo-5'}],
    }

    index.handle_media_group(msg)

    assert handled == [msg]
    assert processed == [
        ('slept', 5),
        (
            'processed',
            msg,
            {
                'get_media_group_state': index.get_media_group_state,
                'delete_media_group_state': index.delete_media_group_state,
                'get_mapping': index.get_mapping,
                'resolve_source_message_id': index.resolve_source_message_id,
                'save_private_message_alias': index.save_private_message_alias,
            },
        ),
    ]
    assert deleted_states == ['album-5']


def test_handle_delete_command_deletes_directly(monkeypatch):
    deleted = []

    monkeypatch.setattr(index, 'delete_message', lambda *args, **kwargs: deleted.append(args[0]))

    msg = {
        'from': {'id': 123},
        'message_id': 90,
        'text': '/delete',
        'reply_to_message': {'message_id': 1},
    }

    index.handle_delete_command(msg)

    assert deleted == [msg]


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
        lambda msg, send_tg_message, edit_message_text, telegram_request, post_to_mastodon, save_mapping, get_pending_media_group_items, pop_ready_pending_media_group_items, logger: processed.append(msg),
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


def test_run_worker_once_claims_and_completes_job(monkeypatch):
    completed = []
    retried = []
    processed = []

    monkeypatch.setattr(
        index,
        'claim_next_job',
        lambda: {
            'id': 7,
            'job_type': 'publish_message',
            'payload_json': {'message_id': 101, 'text': 'hello'},
        },
    )
    monkeypatch.setattr(
        index,
        'process_job',
        lambda job_type, payload, *args, **kwargs: processed.append((job_type, payload)) or True,
    )
    monkeypatch.setattr(index, 'complete_job', lambda job_id: completed.append(job_id))
    monkeypatch.setattr(index, 'retry_job', lambda job_id: retried.append(job_id))

    assert index.run_worker_once() is True
    assert processed == [('publish_message', {'message_id': 101, 'text': 'hello'})]
    assert completed == [7]
    assert retried == []


def test_run_worker_once_fallback_does_not_publish_unqueued_groups(monkeypatch):
    monkeypatch.setattr(index, 'claim_next_job', lambda: None)
    monkeypatch.setattr(index, 'get_ready_pending_media_group_ids', lambda min_age_seconds=1: ['orphan-group'])
    monkeypatch.setattr(index, 'has_pending_media_group_job', lambda media_group_id: False)
    monkeypatch.setattr(index, 'has_media_group_mapping', lambda media_group_id: False)
    monkeypatch.setattr(
        index,
        'process_pending_media_group',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('fallback must not publish albums')),
    )

    assert index.run_worker_once() is False


def test_run_worker_once_fallback_keeps_late_items_for_published_group(monkeypatch):
    deleted = []

    monkeypatch.setattr(index, 'claim_next_job', lambda: None)
    monkeypatch.setattr(index, 'get_ready_pending_media_group_ids', lambda min_age_seconds=1: ['published-group'])
    monkeypatch.setattr(index, 'has_media_group_mapping', lambda media_group_id: media_group_id == 'published-group')
    monkeypatch.setattr(index, 'delete_pending_media_group_items', lambda media_group_id: deleted.append(media_group_id))

    assert index.run_worker_once() is False
    assert deleted == []


def test_run_worker_once_fallback_skips_group_with_pending_job(monkeypatch):
    monkeypatch.setattr(index, 'claim_next_job', lambda: None)
    monkeypatch.setattr(index, 'get_ready_pending_media_group_ids', lambda min_age_seconds=1: ['queued-group', 'ready-group'])
    monkeypatch.setattr(index, 'has_pending_media_group_job', lambda media_group_id: media_group_id == 'queued-group')
    monkeypatch.setattr(index, 'has_media_group_mapping', lambda media_group_id: False)
    monkeypatch.setattr(
        index,
        'process_pending_media_group',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('fallback must not publish albums')),
    )

    assert index.run_worker_once() is False
