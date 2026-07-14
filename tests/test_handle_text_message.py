from api import index
from api import services
import requests


class FakeResponse:
    def __init__(self, ok=True, payload=None, text=''):
        self.ok = ok
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_handle_text_message_publishes_directly(monkeypatch):
    published = []

    monkeypatch.setattr(
        index,
        'publish_message',
        lambda *args, **kwargs: published.append(args[0]),
    )

    msg = {'message_id': 123, 'text': 'hello world'}

    index.handle_text_message(msg)

    assert published == [msg]


def test_publish_message_edits_status_message_on_success(monkeypatch):
    send_calls = []
    edit_calls = []
    saved_mappings = []

    def fake_send(chat_id, text, reply_to=None):
        send_calls.append((chat_id, text, reply_to))
        return {'result': {'message_id': 9001}}

    def fake_edit(chat_id, message_id, text):
        edit_calls.append((chat_id, message_id, text))
        return True

    def fake_save_mapping(source_msg_id, tg_channel_msg_id, masto_status_id):
        saved_mappings.append((source_msg_id, tg_channel_msg_id, masto_status_id))

    monkeypatch.setattr(index, 'post_to_mastodon', lambda text: {'id': 'masto-1'})
    monkeypatch.setattr(
        index,
        'telegram_request',
        lambda *args, **kwargs: FakeResponse(ok=True, payload={'result': {'message_id': 321}}),
    )

    services.publish_message(
        {'message_id': 123, 'text': 'hello world'},
        fake_send,
        fake_edit,
        index.telegram_request,
        index.post_to_mastodon,
        fake_save_mapping,
        index.logger,
    )

    assert len(send_calls) == 1
    assert send_calls[0][2] == 123
    assert saved_mappings == [(123, 321, 'masto-1')]
    assert edit_calls == [
        (
            index.ADMIN_ID,
            9001,
            '✅ <b>发布成功</b>\n\n已同步到：\n• Telegram 频道\n• Mastodon',
        )
    ]


def test_publish_message_removes_sync_status_when_edit_fails(monkeypatch):
    sent = []
    deleted = []

    monkeypatch.setattr(
        "api.clients.delete_tg_message",
        lambda chat_id, message_id: deleted.append((chat_id, message_id)) or True,
    )

    services.publish_message(
        {"message_id": 123, "text": "hello world"},
        lambda chat_id, text, reply_to=None: sent.append((text, reply_to))
        or {"result": {"message_id": 9001}},
        lambda *args: False,
        lambda *args: FakeResponse(ok=True, payload={"result": {"message_id": 321}}),
        lambda text: {"id": "masto-1"},
        lambda *args: None,
        index.logger,
    )

    assert deleted == [(index.ADMIN_ID, 9001)]
    assert len(sent) == 2
    assert sent[-1][0] == services.PUBLISH_SUCCESS_TEXT


def test_publish_message_replies_to_existing_mapping(monkeypatch):
    tg_calls = []
    masto_calls = []

    monkeypatch.setattr(
        index,
        'telegram_request',
        lambda method, payload: tg_calls.append((method, payload)) or FakeResponse(
            ok=True,
            payload={'result': {'message_id': 6543}},
        ),
    )

    def fake_post_to_mastodon(text, in_reply_to_id=None):
        masto_calls.append((text, in_reply_to_id))
        return {'id': 'masto-reply-1'}

    services.publish_message(
        {
            'message_id': 124,
            'text': 'reply body',
            'reply_to_message': {'message_id': 1000},
        },
        lambda chat_id, text, reply_to=None: {'result': {'message_id': 9007}},
        lambda chat_id, message_id, text: True,
        index.telegram_request,
        fake_post_to_mastodon,
        lambda *args: None,
        index.logger,
        get_mapping=lambda source_msg_id: {
            'source': 1000,
            'tg_channel': 2000,
            'masto': 'masto-parent-1',
        } if source_msg_id == 1000 else None,
    )

    assert tg_calls == [
        (
            'sendMessage',
            {
                'chat_id': services.TG_CHANNEL_ID,
                'text': 'reply body',
                'parse_mode': 'HTML',
                'reply_parameters': {'message_id': 2000},
            },
        )
    ]
    assert masto_calls == [('reply body', 'masto-parent-1')]


def test_publish_message_reply_uses_source_mapping_when_ids_overlap(monkeypatch):
    tg_calls = []
    masto_calls = []

    monkeypatch.setattr(
        index,
        'telegram_request',
        lambda method, payload: tg_calls.append((method, payload)) or FakeResponse(
            ok=True,
            payload={'result': {'message_id': 7001}},
        ),
    )

    def fake_post_to_mastodon(text, in_reply_to_id=None):
        masto_calls.append((text, in_reply_to_id))
        return {'id': 'masto-overlap-1'}

    services.publish_message(
        {
            'message_id': 777,
            'text': 'overlap reply',
            'reply_to_message': {'message_id': 500},
        },
        lambda chat_id, text, reply_to=None: {'result': {'message_id': 9008}},
        lambda chat_id, message_id, text: True,
        index.telegram_request,
        fake_post_to_mastodon,
        lambda *args: None,
        index.logger,
        get_mapping=lambda source_msg_id: {
            'source': 500,
            'tg_channel': 901,
            'masto': 'masto-source-500',
        } if source_msg_id == 500 else None,
    )

    assert tg_calls == [
        (
            'sendMessage',
            {
                'chat_id': services.TG_CHANNEL_ID,
                'text': 'overlap reply',
                'parse_mode': 'HTML',
                'reply_parameters': {'message_id': 901},
            },
        )
    ]
    assert masto_calls == [('overlap reply', 'masto-source-500')]


def test_publish_media_group_reply_uses_reply_parameters(monkeypatch):
    tg_calls = []

    monkeypatch.setattr(
        services,
        'publish_album_to_mastodon',
        lambda messages, post_to_mastodon, in_reply_to_id=None: {'id': 'masto-reply-album-1'},
    )

    assert services.process_pending_media_group(
        {
            'message_id': 130,
            'media_group_id': 'reply-group-1',
            'reply_to_message': {'message_id': 1000},
        },
        lambda chat_id, text, reply_to=None: {'result': {'message_id': 9601}},
        lambda chat_id, message_id, text: True,
        lambda method, payload: tg_calls.append((method, payload)) or FakeResponse(
            ok=True,
            payload={'result': [{'message_id': 2001}, {'message_id': 2002}]},
        ),
        lambda text, in_reply_to_id=None: {'id': 'unused'},
        lambda *args, **kwargs: None,
        lambda media_group_id: [
            {
                'message_id': 130,
                'media_group_id': 'reply-group-1',
                'caption': 'reply album',
                'photo': [{'file_id': 'p1', 'file_size': 1024}],
                'reply_to_message': {'message_id': 1000},
            },
            {
                'message_id': 131,
                'media_group_id': 'reply-group-1',
                'photo': [{'file_id': 'p2', 'file_size': 1024}],
            },
        ],
        lambda media_group_id, min_age_seconds: [
            {
                'message_id': 130,
                'media_group_id': 'reply-group-1',
                'caption': 'reply album',
                'photo': [{'file_id': 'p1', 'file_size': 1024}],
                'reply_to_message': {'message_id': 1000},
            },
            {
                'message_id': 131,
                'media_group_id': 'reply-group-1',
                'photo': [{'file_id': 'p2', 'file_size': 1024}],
            },
        ],
        index.logger,
        None,
        lambda media_group_id: {'latest_source_message_id': 131},
        lambda media_group_id: 2,
        lambda media_group_id: None,
        lambda media_group_id: None,
        lambda source_msg_id: {
            'source': 1000,
            'tg_channel': 3000,
            'masto': 'masto-parent-2',
        } if source_msg_id == 1000 else None,
        None,
    ) is True

    assert tg_calls == [
        (
            'sendMediaGroup',
            {
                'chat_id': services.TG_CHANNEL_ID,
                'media': [
                    {
                        'type': 'photo',
                        'media': 'p1',
                        'caption': 'reply album',
                        'parse_mode': 'HTML',
                    },
                    {
                        'type': 'photo',
                        'media': 'p2',
                    },
                ],
                'reply_parameters': {'message_id': 3000},
            },
        )
    ]


def test_publish_message_edits_status_message_on_mastodon_failure(monkeypatch):
    send_calls = []
    edit_calls = []
    saved_mappings = []

    def fake_send(chat_id, text, reply_to=None):
        send_calls.append((chat_id, text, reply_to))
        return {'result': {'message_id': 9002}}

    def fake_edit(chat_id, message_id, text):
        edit_calls.append((chat_id, message_id, text))
        return True

    monkeypatch.setattr(index, 'post_to_mastodon', lambda text: None)
    monkeypatch.setattr(
        index,
        'telegram_request',
        lambda *args, **kwargs: FakeResponse(ok=True, payload={'result': {'message_id': 654}}),
    )

    services.publish_message(
        {'message_id': 456, 'text': 'hello world'},
        fake_send,
        fake_edit,
        index.telegram_request,
        index.post_to_mastodon,
        lambda *args: saved_mappings.append(args),
        index.logger,
    )

    assert len(send_calls) == 1
    assert saved_mappings == [(456, 654, None)]
    assert edit_calls == [
        (
            index.ADMIN_ID,
            9002,
            '⚠️ <b>部分发布成功</b>\n\n已同步到：\n• Telegram 频道\n\n未同步到：\n• Mastodon',
        )
    ]


def test_publish_message_publishes_photo_with_caption(monkeypatch):
    send_calls = []
    edit_calls = []
    saved_mappings = []
    tg_calls = []

    class FakeRequestsResponse:
        def __init__(self, ok=True, payload=None, text=''):
            self.ok = ok
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    def fake_send(chat_id, text, reply_to=None):
        send_calls.append((chat_id, text, reply_to))
        return {'result': {'message_id': 9003}}

    def fake_edit(chat_id, message_id, text):
        edit_calls.append((chat_id, message_id, text))
        return True

    def fake_telegram_request(method, payload):
        tg_calls.append((method, payload))
        return FakeResponse(ok=True, payload={'result': {'message_id': 777}})

    def fake_requests_post(url, headers=None, data=None, timeout=None):
        assert url == f"{services.MASTO_INSTANCE}/api/v1/statuses"
        assert ("status", "new caption") in data
        assert ("media_ids[]", "media-1") in data
        return FakeRequestsResponse(ok=True, payload={'id': 'masto-photo-1'})

    monkeypatch.setattr(index, 'telegram_request', fake_telegram_request)
    monkeypatch.setattr(index, 'post_to_mastodon', lambda text: {'id': 'should-not-be-used'})
    monkeypatch.setattr('api.clients.get_tg_file_path', lambda file_id: 'photos/image.jpg')
    monkeypatch.setattr('api.clients.download_tg_file', lambda file_path: b'image-bytes')
    monkeypatch.setattr('api.clients.upload_mastodon_media', lambda file_content, filename, mime_type: {'id': 'media-1'})
    monkeypatch.setattr(requests, 'post', fake_requests_post)

    services.publish_message(
        {
            'message_id': 789,
            'caption': 'new caption',
            'photo': [{'file_id': 'small'}, {'file_id': 'big', 'file_size': 1024}],
        },
        fake_send,
        fake_edit,
        index.telegram_request,
        index.post_to_mastodon,
        lambda *args: saved_mappings.append(args),
        index.logger,
    )

    assert tg_calls == [
        (
            'sendPhoto',
            {
                'chat_id': services.TG_CHANNEL_ID,
                'photo': 'big',
                'caption': 'new caption',
                'parse_mode': 'HTML',
            },
        )
    ]
    assert saved_mappings == [(789, 777, 'masto-photo-1')]
    assert len(send_calls) == 1
    assert edit_calls == [
        (
            index.ADMIN_ID,
            9003,
            '✅ <b>发布成功</b>\n\n已同步到：\n• Telegram 频道\n• Mastodon',
        )
    ]


def test_publish_message_publishes_document_image_by_file_extension(monkeypatch):
    send_calls = []
    saved_mappings = []
    tg_calls = []
    masto_uploads = []

    class FakeRequestsResponse:
        def __init__(self, ok=True, payload=None, text=''):
            self.ok = ok
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    def fake_telegram_request(method, payload):
        tg_calls.append((method, payload))
        return FakeResponse(ok=True, payload={'result': {'message_id': 778}})

    def fake_requests_post(url, headers=None, data=None, files=None, timeout=None):
        if url.endswith('/sendPhoto'):
            return FakeRequestsResponse(ok=True, payload={'result': {'message_id': 778}})
        return FakeRequestsResponse(ok=True, payload={'id': 'masto-doc-1'})

    monkeypatch.setattr(index, 'telegram_request', fake_telegram_request)
    monkeypatch.setattr(index, 'post_to_mastodon', lambda text: {'id': 'should-not-be-used'})
    monkeypatch.setattr('api.clients.get_tg_file_path', lambda file_id: 'docs/original-upload.bin')
    monkeypatch.setattr('api.clients.download_tg_file', lambda file_path: b'image-bytes')
    monkeypatch.setattr(
        'api.clients.upload_mastodon_media',
        lambda file_content, filename, mime_type: masto_uploads.append((filename, mime_type)) or {'id': 'media-doc-1'},
    )
    monkeypatch.setattr('api.clients.req.post', fake_requests_post)
    monkeypatch.setattr(requests, 'post', fake_requests_post)

    services.publish_message(
        {
            'message_id': 790,
            'caption': 'doc caption',
            'document': {
                'file_id': 'doc-file',
                'file_size': 2048,
                'mime_type': 'application/octet-stream',
                'file_name': 'cover.webp',
            },
        },
        lambda chat_id, text, reply_to=None: send_calls.append((chat_id, text, reply_to)) or {'result': {'message_id': 9004}},
        lambda chat_id, message_id, text: True,
        index.telegram_request,
        index.post_to_mastodon,
        lambda *args: saved_mappings.append(args),
        index.logger,
    )

    assert tg_calls == []
    assert masto_uploads == [('cover.webp', 'image/webp')]


def test_publish_message_preserves_document_filename_for_mastodon_upload(monkeypatch):
    uploaded = []

    class FakeRequestsResponse:
        def __init__(self, ok=True, payload=None, text=''):
            self.ok = ok
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    monkeypatch.setattr(
        index,
        'telegram_request',
        lambda method, payload: FakeResponse(ok=True, payload={'result': {'message_id': 779}}),
    )
    monkeypatch.setattr(index, 'post_to_mastodon', lambda text: {'id': 'should-not-be-used'})
    monkeypatch.setattr('api.clients.get_tg_file_path', lambda file_id: 'documents/file_123')
    monkeypatch.setattr('api.clients.download_tg_file', lambda file_path: b'image-bytes')
    monkeypatch.setattr(
        'api.clients.upload_mastodon_media',
        lambda file_content, filename, mime_type: uploaded.append((filename, mime_type)) or {'id': 'media-doc-2'},
    )
    monkeypatch.setattr(
        requests,
        'post',
        lambda url, headers=None, data=None, files=None, timeout=None: FakeRequestsResponse(
            ok=True,
            payload={'result': {'message_id': 779}} if url.endswith('/sendPhoto') else {'id': 'masto-doc-2'},
        ),
    )

    services.publish_message(
        {
            'message_id': 791,
            'caption': 'sharp text',
            'document': {
                'file_id': 'doc-file-2',
                'file_size': 1024,
                'mime_type': 'image/png',
                'file_name': 'sharp-text.png',
            },
        },
        lambda chat_id, text, reply_to=None: {'result': {'message_id': 9005}},
        lambda chat_id, message_id, text: True,
        index.telegram_request,
        index.post_to_mastodon,
        lambda *args: None,
        index.logger,
    )

    assert uploaded == [('sharp-text.png', 'image/png')]


def test_publish_media_group_uploads_document_images_as_photos(monkeypatch):
    request_calls = []

    class FakeRequestsResponse:
        def __init__(self, ok=True, payload=None, text=''):
            self.ok = ok
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    monkeypatch.setattr('api.clients.get_tg_file_path', lambda file_id: f'documents/{file_id}.bin')
    monkeypatch.setattr('api.clients.download_tg_file', lambda file_path: b'image-bytes')
    monkeypatch.setattr(
        'api.clients.req.post',
        lambda url, data=None, files=None, timeout=None: request_calls.append((url, data, files, timeout)) or FakeRequestsResponse(
            ok=True,
            payload={'result': [{'message_id': 1}, {'message_id': 2}]},
        ),
    )

    services.publish_media_group_to_telegram_channel(
        [
            {
                'message_id': 801,
                'caption': 'album doc caption',
                'document': {
                    'file_id': 'doc-1',
                    'file_size': 1024,
                    'mime_type': 'image/png',
                    'file_name': 'first.png',
                },
            },
            {
                'message_id': 802,
                'document': {
                    'file_id': 'doc-2',
                    'file_size': 1024,
                    'mime_type': 'image/png',
                    'file_name': 'second.png',
                },
            },
        ],
        lambda method, payload: None,
    )

    assert len(request_calls) == 1
    url, data, files, timeout = request_calls[0]
    assert url.endswith('/sendMediaGroup')
    assert data['chat_id'] == services.TG_CHANNEL_ID
    assert '"type": "photo"' in data['media']
    assert '"media": "attach://file0"' in data['media']
    assert '"media": "attach://file1"' in data['media']
    assert files['file0'][0] == 'first.png'
    assert files['file1'][0] == 'second.png'
    assert timeout == 30


def test_publish_media_group_uses_caption_from_non_first_item(monkeypatch):
    request_calls = []

    class FakeRequestsResponse:
        def __init__(self, ok=True, payload=None, text=''):
            self.ok = ok
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    monkeypatch.setattr(
        'api.clients.req.post',
        lambda url, data=None, files=None, timeout=None: request_calls.append((url, data, files, timeout)) or FakeRequestsResponse(
            ok=True,
            payload={'result': [{'message_id': 1}, {'message_id': 2}]},
        ),
    )
    monkeypatch.setattr('api.clients.get_tg_file_path', lambda file_id: f'documents/{file_id}.png')
    monkeypatch.setattr('api.clients.download_tg_file', lambda file_path: b'image-bytes')

    services.publish_media_group_to_telegram_channel(
        [
            {
                'message_id': 901,
                'document': {
                    'file_id': 'doc-a',
                    'file_size': 1024,
                    'mime_type': 'image/png',
                    'file_name': 'a.png',
                },
            },
            {
                'message_id': 902,
                'caption': 'late caption',
                'document': {
                    'file_id': 'doc-b',
                    'file_size': 1024,
                    'mime_type': 'image/png',
                    'file_name': 'b.png',
                },
            },
        ],
        lambda method, payload: None,
    )

    assert len(request_calls) == 1
    _, data, _, _ = request_calls[0]
    assert '"caption": "late caption"' in data['media']


def test_publish_media_group_sends_four_document_images_with_single_caption(monkeypatch):
    request_calls = []

    class FakeRequestsResponse:
        def __init__(self, ok=True, payload=None, text=''):
            self.ok = ok
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    monkeypatch.setattr('api.clients.get_tg_file_path', lambda file_id: f'documents/{file_id}.bin')
    monkeypatch.setattr('api.clients.download_tg_file', lambda file_path: b'image-bytes')
    monkeypatch.setattr(
        'api.clients.req.post',
        lambda url, data=None, files=None, timeout=None: request_calls.append((url, data, files, timeout)) or FakeRequestsResponse(
            ok=True,
            payload={'result': [{'message_id': 11}, {'message_id': 12}, {'message_id': 13}, {'message_id': 14}]},
        ),
    )

    services.publish_media_group_to_telegram_channel(
        [
            {
                'message_id': 1001,
                'caption': 'four files caption',
                'document': {
                    'file_id': 'doc-1',
                    'file_size': 1024,
                    'mime_type': 'image/png',
                    'file_name': '1.png',
                },
            },
            {
                'message_id': 1002,
                'document': {
                    'file_id': 'doc-2',
                    'file_size': 1024,
                    'mime_type': 'image/png',
                    'file_name': '2.png',
                },
            },
            {
                'message_id': 1003,
                'document': {
                    'file_id': 'doc-3',
                    'file_size': 1024,
                    'mime_type': 'image/png',
                    'file_name': '3.png',
                },
            },
            {
                'message_id': 1004,
                'document': {
                    'file_id': 'doc-4',
                    'file_size': 1024,
                    'mime_type': 'image/png',
                    'file_name': '4.png',
                },
            },
        ],
        lambda method, payload: None,
    )

    assert len(request_calls) == 1
    _, data, files, _ = request_calls[0]
    assert '"caption": "four files caption"' in data['media']
    assert '"media": "attach://file0"' in data['media']
    assert '"media": "attach://file1"' in data['media']
    assert '"media": "attach://file2"' in data['media']
    assert '"media": "attach://file3"' in data['media']
    assert sorted(files) == ['file0', 'file1', 'file2', 'file3']


def test_publish_message_does_not_fallback_to_text_only_when_media_upload_fails(monkeypatch):
    send_calls = []
    edit_calls = []
    saved_mappings = []
    mastodon_calls = []

    def fake_send(chat_id, text, reply_to=None):
        send_calls.append((chat_id, text, reply_to))
        return {'result': {'message_id': 9006}}

    def fake_edit(chat_id, message_id, text):
        edit_calls.append((chat_id, message_id, text))
        return True

    monkeypatch.setattr(
        index,
        'telegram_request',
        lambda method, payload: FakeResponse(ok=True, payload={'result': {'message_id': 780}}),
    )
    monkeypatch.setattr(
        index,
        'post_to_mastodon',
        lambda text: mastodon_calls.append(text) or {'id': 'unexpected-text-only-post'},
    )
    monkeypatch.setattr('api.clients.get_tg_file_path', lambda file_id: None)

    services.publish_message(
        {
            'message_id': 792,
            'caption': 'image caption',
            'photo': [{'file_id': 'photo-file', 'file_size': 1024}],
        },
        fake_send,
        fake_edit,
        index.telegram_request,
        index.post_to_mastodon,
        lambda *args: saved_mappings.append(args),
        index.logger,
    )

    assert mastodon_calls == []
    assert saved_mappings == [(792, 780, None)]
    assert edit_calls == [
        (
            index.ADMIN_ID,
            9006,
            '⚠️ <b>部分发布成功</b>\n\n已同步到：\n• Telegram 频道\n\n未同步到：\n• Mastodon',
        )
    ]


def test_handle_media_group_message_rejects_more_than_four_items(monkeypatch):
    sent = []
    edited = []
    saved_pending = []
    group_messages = [
        {
            'message_id': message_id,
            'media_group_id': 'group-1',
            'photo': [{'file_id': f'photo-{message_id}', 'file_size': 1024}],
        }
        for message_id in range(1, 6)
    ]
    pending_items = {
        item['message_id']: item
        for item in group_messages[:-1]
    }

    def fake_send(chat_id, text, reply_to=None):
        sent.append((chat_id, text, reply_to))
        return {'result': {'message_id': 9100}}

    def fake_save_pending(media_group_id, source_message_id, payload_json):
        saved_pending.append((media_group_id, source_message_id, payload_json))
        pending_items[source_message_id] = payload_json
        return True

    def fake_get_pending(media_group_id):
        return [pending_items[key] for key in sorted(pending_items)]

    def fake_delete_pending(media_group_id):
        pending_items.clear()

    # Step 1: save the pending item
    services.handle_media_group_message(
        group_messages[-1],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: None,
        fake_save_pending,
        fake_get_pending,
        fake_delete_pending,
        index.logger,
    )

    # Step 2: process (called by index.py after sleep)
    services.process_pending_media_group(
        group_messages[-1],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: None,
        fake_get_pending,
        lambda media_group_id, min_age_seconds: fake_get_pending(media_group_id),
        index.logger,
    )

    assert saved_pending == [
        ('group-1', 5, group_messages[4]),
    ]
    assert sent == [
        (
            index.ADMIN_ID,
            '❌ 不支持超过 4 张图片的相册消息\n\nMastodon 最多只支持 4 张图片，请减少到 4 张或更少后再发送。',
            1,
        ),
    ]
    assert edited == []


def test_handle_media_group_message_publishes_up_to_four_items(monkeypatch):
    sent = []
    edited = []
    saved_mappings = []
    group_messages = [
        {
            'message_id': 11,
            'media_group_id': 'group-2',
            'caption': 'album caption',
            'photo': [{'file_id': 'photo-11', 'file_size': 1024}],
        },
        {
            'message_id': 12,
            'media_group_id': 'group-2',
            'photo': [{'file_id': 'photo-12', 'file_size': 1024}],
        },
    ]
    pending_items = {
        group_messages[0]['message_id']: group_messages[0],
    }

    monkeypatch.setattr(
        services,
        'publish_media_group_to_telegram_channel',
        lambda messages, telegram_request: FakeResponse(
            ok=True,
            payload={'result': [{'message_id': 801}, {'message_id': 802}]},
        ),
    )
    monkeypatch.setattr(
        services,
        'publish_album_to_mastodon',
        lambda messages, post_to_mastodon: {'id': 'masto-album-1'},
    )

    def fake_send(chat_id, text, reply_to=None):
        sent.append((chat_id, text, reply_to))
        return {'result': {'message_id': 9101}}

    def fake_save_pending(media_group_id, source_message_id, payload_json):
        pending_items[source_message_id] = payload_json
        return True

    def fake_get_pending(media_group_id):
        return [pending_items[key] for key in sorted(pending_items)]

    def fake_delete_pending(media_group_id):
        pending_items.clear()

    # Step 1: save the pending item
    services.handle_media_group_message(
        group_messages[-1],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: saved_mappings.append((args, kwargs)),
        fake_save_pending,
        fake_get_pending,
        fake_delete_pending,
        index.logger,
    )

    # Step 2: process (called by index.py after sleep)
    services.process_pending_media_group(
        group_messages[-1],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: saved_mappings.append((args, kwargs)),
        fake_get_pending,
        lambda media_group_id, min_age_seconds: fake_get_pending(media_group_id),
        index.logger,
    )

    assert saved_mappings == [
        (((11, 801, 'masto-album-1'), {'tg_channel_message_ids': [801, 802], 'media_group_id': 'group-2'})),
        (((12, 802, 'masto-album-1'), {'tg_channel_message_ids': [801, 802], 'media_group_id': 'group-2'})),
    ]
    assert edited == [
        (
            index.ADMIN_ID,
            9101,
            '✅ <b>发布成功</b>\n\n已同步到：\n• Telegram 频道\n• Mastodon',
        )
    ]

    sent = []
    edited = []
    saved_pending = []
    group_messages = [
        {
            'message_id': message_id,
            'media_group_id': 'group-1',
            'photo': [{'file_id': f'photo-{message_id}', 'file_size': 1024}],
        }
        for message_id in range(1, 6)
    ]
    pending_items = {
        item['message_id']: item
        for item in group_messages[:-1]
    }


def test_process_pending_media_group_waits_until_group_is_ready(monkeypatch):
    sent = []
    edited = []
    saved_mappings = []
    group_messages = [
        {
            'message_id': 21,
            'media_group_id': 'group-3',
            'caption': 'album caption',
            'photo': [{'file_id': 'photo-21', 'file_size': 1024}],
        },
        {
            'message_id': 22,
            'media_group_id': 'group-3',
            'photo': [{'file_id': 'photo-22', 'file_size': 1024}],
        },
    ]
    pop_calls = []
    ready_batches = [
        [],
        group_messages,
    ]

    monkeypatch.setattr(services, 'MEDIA_GROUP_SETTLE_SECONDS', 0)
    monkeypatch.setattr(
        services,
        'publish_media_group_to_telegram_channel',
        lambda messages, telegram_request: FakeResponse(
            ok=True,
            payload={'result': [{'message_id': 901}, {'message_id': 902}]},
        ),
    )
    monkeypatch.setattr(
        services,
        'publish_album_to_mastodon',
        lambda messages, post_to_mastodon: {'id': 'masto-album-2'},
    )

    def fake_send(chat_id, text, reply_to=None):
        sent.append((chat_id, text, reply_to))
        return {'result': {'message_id': 9201}}

    def fake_pop_ready(media_group_id, min_age_seconds):
        pop_calls.append((media_group_id, min_age_seconds))
        return ready_batches.pop(0)

    assert services.process_pending_media_group(
        group_messages[-1],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: saved_mappings.append((args, kwargs)),
        lambda media_group_id: group_messages if ready_batches else [],
        fake_pop_ready,
        index.logger,
    ) is False

    assert services.process_pending_media_group(
        group_messages[-1],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: saved_mappings.append((args, kwargs)),
        lambda media_group_id: group_messages,
        fake_pop_ready,
        index.logger,
    ) is True

    assert pop_calls == [
        ('group-3', services.MEDIA_GROUP_READY_AGE_SECONDS),
        ('group-3', services.MEDIA_GROUP_READY_AGE_SECONDS),
    ]
    assert saved_mappings == [
        (((21, 901, 'masto-album-2'), {'tg_channel_message_ids': [901, 902], 'media_group_id': 'group-3'})),
        (((22, 902, 'masto-album-2'), {'tg_channel_message_ids': [901, 902], 'media_group_id': 'group-3'})),
    ]
    assert edited == [
        (
            index.ADMIN_ID,
            9201,
            '✅ <b>发布成功</b>\n\n已同步到：\n• Telegram 频道\n• Mastodon',
        )
    ]
    assert sent == [
        (
            index.ADMIN_ID,
            '⏳ <b>已收到</b>\n\n正在同步到 Telegram 频道和 Mastodon...',
            21,
        )
    ]


def test_handle_media_group_message_reports_pending_storage_failure():
    sent = []

    services.handle_media_group_message(
        {
            'message_id': 31,
            'media_group_id': 'group-4',
            'photo': [{'file_id': 'photo-31', 'file_size': 1024}],
        },
        lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)),
        lambda chat_id, message_id, text: True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: None,
        lambda media_group_id, source_message_id, payload_json: False,
        lambda media_group_id: [],
        lambda media_group_id: None,
        index.logger,
    )

    assert sent == [
        (
            index.ADMIN_ID,
            '❌ 相册暂存失败\n\n多图同步依赖数据库暂存图片分组，当前数据库不可用或写入失败。',
            31,
        )
    ]


def test_handle_media_group_message_reports_unsupported_media_group_content():
    sent = []

    services.handle_media_group_message(
        {
            'message_id': 32,
            'media_group_id': 'group-unsupported-1',
            'document': {
                'file_id': 'doc-unsupported',
                'mime_type': 'application/pdf',
                'file_name': 'report.pdf',
            },
        },
        lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)),
        lambda chat_id, message_id, text: True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: None,
        lambda media_group_id, source_message_id, payload_json: True,
        lambda media_group_id: [],
        lambda media_group_id: None,
        index.logger,
    )

    assert sent == [
        (
            index.ADMIN_ID,
            '❌ 不支持的文件类型\n\n仅支持作为文件发送的静态图片 (JPG, PNG, WebP, HEIC, HEIF等) 或常见视频文件 (MP4, MOV, WebM等)。',
            None,
        )
    ]


def test_process_pending_media_group_waits_for_latest_message_before_publishing(monkeypatch):
    sent = []
    edited = []
    saved_mappings = []
    group_messages = [
        {
            'message_id': 41,
            'media_group_id': 'group-5',
            'caption': 'album caption',
            'photo': [{'file_id': 'photo-41', 'file_size': 1024}],
        },
        {
            'message_id': 42,
            'media_group_id': 'group-5',
            'photo': [{'file_id': 'photo-42', 'file_size': 1024}],
        },
        {
            'message_id': 43,
            'media_group_id': 'group-5',
            'photo': [{'file_id': 'photo-43', 'file_size': 1024}],
        },
        {
            'message_id': 44,
            'media_group_id': 'group-5',
            'photo': [{'file_id': 'photo-44', 'file_size': 1024}],
        },
    ]
    pop_calls = []

    monkeypatch.setattr(services, 'MEDIA_GROUP_SETTLE_SECONDS', 0)
    monkeypatch.setattr(
        services,
        'publish_media_group_to_telegram_channel',
        lambda messages, telegram_request: FakeResponse(
            ok=True,
            payload={
                'result': [
                    {'message_id': 911},
                    {'message_id': 912},
                    {'message_id': 913},
                    {'message_id': 914},
                ]
            },
        ),
    )
    monkeypatch.setattr(
        services,
        'publish_album_to_mastodon',
        lambda messages, post_to_mastodon: {'id': 'masto-album-3'},
    )

    def fake_send(chat_id, text, reply_to=None):
        sent.append((chat_id, text, reply_to))
        return {'result': {'message_id': 9301}}

    def fake_pop_ready(media_group_id, min_age_seconds):
        pop_calls.append((media_group_id, min_age_seconds))
        return group_messages

    assert services.process_pending_media_group(
        group_messages[1],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: saved_mappings.append((args, kwargs)),
        lambda media_group_id: group_messages,
        fake_pop_ready,
        index.logger,
    ) is False

    assert services.process_pending_media_group(
        group_messages[-1],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: saved_mappings.append((args, kwargs)),
        lambda media_group_id: group_messages,
        fake_pop_ready,
        index.logger,
    ) is True

    assert pop_calls == [('group-5', services.MEDIA_GROUP_READY_AGE_SECONDS)]
    assert len(saved_mappings) == 4
    assert edited == [
        (
            index.ADMIN_ID,
            9301,
            '✅ <b>发布成功</b>\n\n已同步到：\n• Telegram 频道\n• Mastodon',
        )
    ]
    assert sent == [
        (
            index.ADMIN_ID,
            '⏳ <b>已收到</b>\n\n正在同步到 Telegram 频道和 Mastodon...',
            41,
        )
    ]


def test_process_pending_media_group_retries_when_newer_album_item_arrived(monkeypatch):
    group_messages = [
        {
            'message_id': 61,
            'media_group_id': 'group-7',
            'caption': 'album caption',
            'photo': [{'file_id': 'photo-61', 'file_size': 1024}],
        },
        {
            'message_id': 62,
            'media_group_id': 'group-7',
            'photo': [{'file_id': 'photo-62', 'file_size': 1024}],
        },
        {
            'message_id': 63,
            'media_group_id': 'group-7',
            'photo': [{'file_id': 'photo-63', 'file_size': 1024}],
        },
        {
            'message_id': 64,
            'media_group_id': 'group-7',
            'photo': [{'file_id': 'photo-64', 'file_size': 1024}],
        },
    ]

    assert services.process_pending_media_group(
        group_messages[1],
        lambda chat_id, text, reply_to=None: None,
        lambda chat_id, message_id, text: True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: None,
        lambda media_group_id: group_messages,
        lambda media_group_id, min_age_seconds: (_ for _ in ()).throw(AssertionError('must not pop unready album')),
        index.logger,
        expected_latest_message_id=62,
    ) is False


def test_process_pending_media_group_requires_two_stable_checks_before_publish(monkeypatch):
    sent = []
    edited = []
    saved_mappings = []
    group_messages = [
        {
            'message_id': 71,
            'media_group_id': 'group-stable-1',
            'caption': 'album caption',
            'document': {
                'file_id': 'doc-71',
                'file_size': 1024,
                'mime_type': 'image/png',
                'file_name': '1.png',
            },
        },
        {
            'message_id': 72,
            'media_group_id': 'group-stable-1',
            'document': {
                'file_id': 'doc-72',
                'file_size': 1024,
                'mime_type': 'image/png',
                'file_name': '2.png',
            },
        },
        {
            'message_id': 73,
            'media_group_id': 'group-stable-1',
            'document': {
                'file_id': 'doc-73',
                'file_size': 1024,
                'mime_type': 'image/png',
                'file_name': '3.png',
            },
        },
        {
            'message_id': 74,
            'media_group_id': 'group-stable-1',
            'document': {
                'file_id': 'doc-74',
                'file_size': 1024,
                'mime_type': 'image/png',
                'file_name': '4.png',
            },
        },
    ]
    stable_counts = []
    pop_calls = []

    monkeypatch.setattr(
        services,
        'publish_media_group_to_telegram_channel',
        lambda messages, telegram_request, reply_to_message_id=None: FakeResponse(
            ok=True,
            payload={
                'result': [
                    {'message_id': 1001},
                    {'message_id': 1002},
                    {'message_id': 1003},
                    {'message_id': 1004},
                ]
            },
        ),
    )
    monkeypatch.setattr(
        services,
        'publish_album_to_mastodon',
        lambda messages, post_to_mastodon, in_reply_to_id=None: {'id': 'masto-stable-1'},
    )

    def fake_send(chat_id, text, reply_to=None):
        sent.append((chat_id, text, reply_to))
        return {'result': {'message_id': 9501}}

    def fake_pop_ready(media_group_id, min_age_seconds):
        pop_calls.append((media_group_id, min_age_seconds))
        return group_messages

    def fake_bump(media_group_id):
        value = len(stable_counts) + 1
        stable_counts.append(value)
        return value

    assert services.process_pending_media_group(
        group_messages[-1],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: saved_mappings.append((args, kwargs)),
        lambda media_group_id: group_messages,
        fake_pop_ready,
        index.logger,
        None,
        lambda media_group_id: {'latest_source_message_id': 74},
        fake_bump,
        lambda media_group_id: None,
        lambda media_group_id: None,
        None,
    ) is False

    assert services.process_pending_media_group(
        group_messages[-1],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: saved_mappings.append((args, kwargs)),
        lambda media_group_id: group_messages,
        fake_pop_ready,
        index.logger,
        None,
        lambda media_group_id: {'latest_source_message_id': 74},
        fake_bump,
        lambda media_group_id: None,
        lambda media_group_id: None,
        None,
    ) is True

    assert stable_counts == [1, 2]
    assert pop_calls == [('group-stable-1', services.MEDIA_GROUP_READY_AGE_SECONDS)]
    assert len(saved_mappings) == 4


def test_process_pending_media_group_fails_when_telegram_returns_fewer_items(monkeypatch):
    sent = []
    edited = []
    group_messages = [
        {
            'message_id': 51,
            'media_group_id': 'group-6',
            'caption': 'album caption',
            'photo': [{'file_id': 'photo-51', 'file_size': 1024}],
        },
        {
            'message_id': 52,
            'media_group_id': 'group-6',
            'photo': [{'file_id': 'photo-52', 'file_size': 1024}],
        },
        {
            'message_id': 53,
            'media_group_id': 'group-6',
            'photo': [{'file_id': 'photo-53', 'file_size': 1024}],
        },
        {
            'message_id': 54,
            'media_group_id': 'group-6',
            'photo': [{'file_id': 'photo-54', 'file_size': 1024}],
        },
    ]

    monkeypatch.setattr(
        services,
        'publish_media_group_to_telegram_channel',
        lambda messages, telegram_request: FakeResponse(
            ok=True,
            payload={'result': [{'message_id': 951}, {'message_id': 952}, {'message_id': 953}]},
        ),
    )

    def fake_send(chat_id, text, reply_to=None):
        sent.append((chat_id, text, reply_to))
        return {'result': {'message_id': 9401}}

    assert services.process_pending_media_group(
        group_messages[-1],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: None,
        lambda media_group_id: group_messages,
        lambda media_group_id, min_age_seconds: group_messages,
        index.logger,
    ) is True

    assert edited == [
        (
            index.ADMIN_ID,
            9401,
            '❌ <b>发布失败</b>\n\nTelegram 相册返回数量异常，请重试',
        )
    ]


def test_process_pending_media_group_publishes_four_document_images_with_single_mapping_batch(monkeypatch):
    sent = []
    edited = []
    saved_mappings = []
    group_messages = [
        {
            'message_id': 151,
            'media_group_id': 'group-doc-4',
            'caption': 'doc album caption',
            'document': {
                'file_id': 'doc-151',
                'file_size': 1024,
                'mime_type': 'image/png',
                'file_name': '151.png',
            },
        },
        {
            'message_id': 152,
            'media_group_id': 'group-doc-4',
            'document': {
                'file_id': 'doc-152',
                'file_size': 1024,
                'mime_type': 'image/png',
                'file_name': '152.png',
            },
        },
        {
            'message_id': 153,
            'media_group_id': 'group-doc-4',
            'document': {
                'file_id': 'doc-153',
                'file_size': 1024,
                'mime_type': 'image/png',
                'file_name': '153.png',
            },
        },
        {
            'message_id': 154,
            'media_group_id': 'group-doc-4',
            'document': {
                'file_id': 'doc-154',
                'file_size': 1024,
                'mime_type': 'image/png',
                'file_name': '154.png',
            },
        },
    ]

    monkeypatch.setattr(
        services,
        'publish_media_group_to_telegram_channel',
        lambda messages, telegram_request, reply_to_message_id=None: FakeResponse(
            ok=True,
            payload={
                'result': [
                    {'message_id': 2001},
                    {'message_id': 2002},
                    {'message_id': 2003},
                    {'message_id': 2004},
                ]
            },
        ),
    )
    monkeypatch.setattr(
        services,
        'publish_album_to_mastodon',
        lambda messages, post_to_mastodon, in_reply_to_id=None: {'id': 'masto-doc-group-4'},
    )

    def fake_send(chat_id, text, reply_to=None):
        sent.append((chat_id, text, reply_to))
        return {'result': {'message_id': 9504}}

    assert services.process_pending_media_group(
        group_messages[-1],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text, in_reply_to_id=None: None,
        lambda *args, **kwargs: saved_mappings.append((args, kwargs)),
        lambda media_group_id: group_messages,
        lambda media_group_id, min_age_seconds: group_messages,
        index.logger,
        None,
        lambda media_group_id: {'latest_source_message_id': 154},
        lambda media_group_id: 2,
        lambda media_group_id: None,
        lambda media_group_id: None,
        None,
    ) is True

    assert len(saved_mappings) == 4
    assert saved_mappings == [
        (((151, 2001, 'masto-doc-group-4'), {'tg_channel_message_ids': [2001, 2002, 2003, 2004], 'media_group_id': 'group-doc-4'})),
        (((152, 2002, 'masto-doc-group-4'), {'tg_channel_message_ids': [2001, 2002, 2003, 2004], 'media_group_id': 'group-doc-4'})),
        (((153, 2003, 'masto-doc-group-4'), {'tg_channel_message_ids': [2001, 2002, 2003, 2004], 'media_group_id': 'group-doc-4'})),
        (((154, 2004, 'masto-doc-group-4'), {'tg_channel_message_ids': [2001, 2002, 2003, 2004], 'media_group_id': 'group-doc-4'})),
    ]
    assert edited == [
        (
            index.ADMIN_ID,
            9504,
            '✅ <b>发布成功</b>\n\n已同步到：\n• Telegram 频道\n• Mastodon',
        )
    ]


def test_media_group_ready_age_tracks_settle_seconds():
    assert services.MEDIA_GROUP_READY_AGE_SECONDS >= 3
    assert services.MEDIA_GROUP_READY_AGE_SECONDS == int(services.MEDIA_GROUP_SETTLE_SECONDS)
def test_publish_message_reply_via_status_alias(monkeypatch):
    tg_calls = []
    masto_calls = []

    monkeypatch.setattr(
        index,
        'telegram_request',
        lambda method, payload: tg_calls.append((method, payload)) or FakeResponse(
            ok=True,
            payload={'result': {'message_id': 7101}},
        ),
    )

    def fake_post_to_mastodon(text, in_reply_to_id=None):
        masto_calls.append((text, in_reply_to_id))
        return {'id': 'masto-alias-reply-1'}

    services.publish_message(
        {
            'message_id': 535,
            'text': 'comment reply',
            'reply_to_message': {'message_id': 9100},
        },
        lambda chat_id, text, reply_to=None: {'result': {'message_id': 9101}},
        lambda chat_id, message_id, text: True,
        index.telegram_request,
        fake_post_to_mastodon,
        lambda *args: None,
        index.logger,
        get_mapping=lambda source_msg_id: {
            'source': 533,
            'tg_channel': 333,
            'masto': '116592049305306695',
        } if source_msg_id == 533 else None,
        resolve_source_message_id=lambda message_id: 533 if message_id == 9100 else message_id,
    )

    assert tg_calls == [
        (
            'sendMessage',
            {
                'chat_id': services.TG_CHANNEL_ID,
                'text': 'comment reply',
                'parse_mode': 'HTML',
                'reply_parameters': {'message_id': 333},
            },
        )
    ]
    assert masto_calls == [('comment reply', '116592049305306695')]


def test_publish_message_status_alias_is_saved_before_reply_chain(monkeypatch):
    tg_calls = []
    masto_calls = []
    saved_aliases = {}
    saved_mappings = {}

    monkeypatch.setattr(
        index,
        'telegram_request',
        lambda method, payload: tg_calls.append((method, payload)) or FakeResponse(
            ok=True,
            payload={'result': {'message_id': 333 if payload.get('text') == 'hello parent' else 334}},
        ),
    )

    def fake_post_to_mastodon(text, in_reply_to_id=None):
        masto_calls.append((text, in_reply_to_id))
        return {'id': 'masto-parent' if text == 'hello parent' else 'masto-child'}

    def fake_save_mapping(source_msg_id, tg_channel_msg_id, masto_status_id):
        saved_mappings[source_msg_id] = {
            'source': source_msg_id,
            'tg_channel': tg_channel_msg_id,
            'masto': masto_status_id,
        }

    def fake_send(chat_id, text, reply_to=None):
        status_message_id = 9100 if reply_to == 533 else 9101
        return {'result': {'message_id': status_message_id}}

    services.publish_message(
        {'message_id': 533, 'text': 'hello parent'},
        fake_send,
        lambda chat_id, message_id, text: True,
        index.telegram_request,
        fake_post_to_mastodon,
        fake_save_mapping,
        index.logger,
        get_mapping=lambda source_msg_id: saved_mappings.get(source_msg_id),
        resolve_source_message_id=lambda message_id: saved_aliases.get(message_id, message_id),
        save_private_message_alias=lambda alias_message_id, source_message_id: saved_aliases.__setitem__(alias_message_id, source_message_id),
    )

    services.publish_message(
        {
            'message_id': 535,
            'text': 'hello child',
            'reply_to_message': {'message_id': 9100},
        },
        fake_send,
        lambda chat_id, message_id, text: True,
        index.telegram_request,
        fake_post_to_mastodon,
        fake_save_mapping,
        index.logger,
        get_mapping=lambda source_msg_id: saved_mappings.get(source_msg_id),
        resolve_source_message_id=lambda message_id: saved_aliases.get(message_id, message_id),
        save_private_message_alias=lambda alias_message_id, source_message_id: saved_aliases.__setitem__(alias_message_id, source_message_id),
    )

    assert saved_aliases[9100] == 533
    assert tg_calls == [
        (
            'sendMessage',
            {
                'chat_id': services.TG_CHANNEL_ID,
                'text': 'hello parent',
                'parse_mode': 'HTML',
            },
        ),
        (
            'sendMessage',
            {
                'chat_id': services.TG_CHANNEL_ID,
                'text': 'hello child',
                'parse_mode': 'HTML',
                'reply_parameters': {'message_id': 333},
            },
        ),
    ]
    assert masto_calls == [
        ('hello parent', None),
        ('hello child', 'masto-parent'),
    ]
