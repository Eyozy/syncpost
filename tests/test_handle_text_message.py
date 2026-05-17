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


def test_handle_text_message_edits_status_message_on_success(monkeypatch):
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

    monkeypatch.setattr(index, 'send_tg_message', fake_send)
    monkeypatch.setattr(index, 'edit_message_text', fake_edit)
    monkeypatch.setattr(index, 'save_mapping', fake_save_mapping)
    monkeypatch.setattr(index, 'post_to_mastodon', lambda text: {'id': 'masto-1'})
    monkeypatch.setattr(
        index,
        'telegram_request',
        lambda *args, **kwargs: FakeResponse(ok=True, payload={'result': {'message_id': 321}}),
    )

    index.handle_text_message({'message_id': 123, 'text': 'hello world'})

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


def test_handle_text_message_edits_status_message_on_mastodon_failure(monkeypatch):
    send_calls = []
    edit_calls = []
    saved_mappings = []

    def fake_send(chat_id, text, reply_to=None):
        send_calls.append((chat_id, text, reply_to))
        return {'result': {'message_id': 9002}}

    def fake_edit(chat_id, message_id, text):
        edit_calls.append((chat_id, message_id, text))
        return True

    monkeypatch.setattr(index, 'send_tg_message', fake_send)
    monkeypatch.setattr(index, 'edit_message_text', fake_edit)
    monkeypatch.setattr(index, 'save_mapping', lambda *args: saved_mappings.append(args))
    monkeypatch.setattr(index, 'post_to_mastodon', lambda text: None)
    monkeypatch.setattr(
        index,
        'telegram_request',
        lambda *args, **kwargs: FakeResponse(ok=True, payload={'result': {'message_id': 654}}),
    )

    index.handle_text_message({'message_id': 456, 'text': 'hello world'})

    assert len(send_calls) == 1
    assert saved_mappings == [(456, 654, None)]
    assert edit_calls == [
        (
            index.ADMIN_ID,
            9002,
            '⚠️ <b>部分发布成功</b>\n\n已同步到：\n• Telegram 频道\n\n未同步到：\n• Mastodon',
        )
    ]


def test_handle_text_message_publishes_photo_with_caption(monkeypatch):
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

    monkeypatch.setattr(index, 'send_tg_message', fake_send)
    monkeypatch.setattr(index, 'edit_message_text', fake_edit)
    monkeypatch.setattr(index, 'save_mapping', lambda *args: saved_mappings.append(args))
    monkeypatch.setattr(index, 'telegram_request', fake_telegram_request)
    monkeypatch.setattr(index, 'post_to_mastodon', lambda text: {'id': 'should-not-be-used'})
    monkeypatch.setattr('api.clients.get_tg_file_path', lambda file_id: 'photos/image.jpg')
    monkeypatch.setattr('api.clients.download_tg_file', lambda file_path: b'image-bytes')
    monkeypatch.setattr('api.clients.upload_mastodon_media', lambda file_content, filename, mime_type: {'id': 'media-1'})
    monkeypatch.setattr(requests, 'post', fake_requests_post)

    index.handle_text_message({
        'message_id': 789,
        'caption': 'new caption',
        'photo': [{'file_id': 'small'}, {'file_id': 'big', 'file_size': 1024}],
    })

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


def test_handle_text_message_publishes_document_image_by_file_extension(monkeypatch):
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

    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: send_calls.append((chat_id, text, reply_to)) or {'result': {'message_id': 9004}})
    monkeypatch.setattr(index, 'edit_message_text', lambda chat_id, message_id, text: True)
    monkeypatch.setattr(index, 'save_mapping', lambda *args: saved_mappings.append(args))

    def fake_telegram_request(method, payload):
        tg_calls.append((method, payload))
        return FakeResponse(ok=True, payload={'result': {'message_id': 778}})

    def fake_requests_post(url, headers=None, data=None, timeout=None):
        return FakeRequestsResponse(ok=True, payload={'id': 'masto-doc-1'})

    monkeypatch.setattr(index, 'telegram_request', fake_telegram_request)
    monkeypatch.setattr(index, 'post_to_mastodon', lambda text: {'id': 'should-not-be-used'})
    monkeypatch.setattr('api.clients.get_tg_file_path', lambda file_id: 'docs/original-upload.bin')
    monkeypatch.setattr('api.clients.download_tg_file', lambda file_path: b'image-bytes')
    monkeypatch.setattr(
        'api.clients.upload_mastodon_media',
        lambda file_content, filename, mime_type: masto_uploads.append((filename, mime_type)) or {'id': 'media-doc-1'},
    )
    monkeypatch.setattr(requests, 'post', fake_requests_post)

    index.handle_text_message({
        'message_id': 790,
        'caption': 'doc caption',
        'document': {
            'file_id': 'doc-file',
            'file_size': 2048,
            'mime_type': 'application/octet-stream',
            'file_name': 'cover.webp',
        },
    })

    assert tg_calls == [
        (
            'sendPhoto',
            {
                'chat_id': services.TG_CHANNEL_ID,
                'photo': 'doc-file',
                'caption': 'doc caption',
                'parse_mode': 'HTML',
            },
        )
    ]
    assert masto_uploads == [('cover.webp', 'image/webp')]


def test_handle_text_message_preserves_document_filename_for_mastodon_upload(monkeypatch):
    uploaded = []

    class FakeRequestsResponse:
        def __init__(self, ok=True, payload=None, text=''):
            self.ok = ok
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: {'result': {'message_id': 9005}})
    monkeypatch.setattr(index, 'edit_message_text', lambda chat_id, message_id, text: True)
    monkeypatch.setattr(index, 'save_mapping', lambda *args: None)
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
        lambda url, headers=None, data=None, timeout=None: FakeRequestsResponse(ok=True, payload={'id': 'masto-doc-2'}),
    )

    index.handle_text_message({
        'message_id': 791,
        'caption': 'sharp text',
        'document': {
            'file_id': 'doc-file-2',
            'file_size': 1024,
            'mime_type': 'image/png',
            'file_name': 'sharp-text.png',
        },
    })

    assert uploaded == [('sharp-text.png', 'image/png')]


def test_handle_text_message_does_not_fallback_to_text_only_when_media_upload_fails(monkeypatch):
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

    monkeypatch.setattr(index, 'send_tg_message', fake_send)
    monkeypatch.setattr(index, 'edit_message_text', fake_edit)
    monkeypatch.setattr(index, 'save_mapping', lambda *args: saved_mappings.append(args))
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

    index.handle_text_message({
        'message_id': 792,
        'caption': 'image caption',
        'photo': [{'file_id': 'photo-file', 'file_size': 1024}],
    })

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

    class FakeTime:
        current = 0.0

        @staticmethod
        def sleep(seconds):
            FakeTime.current += seconds

        @staticmethod
        def time():
            return FakeTime.current

    monkeypatch.setattr(services, 'time', FakeTime)

    def fake_send(chat_id, text, reply_to=None):
        sent.append((chat_id, text, reply_to))
        return {'result': {'message_id': 9100}}

    group_messages = [
        {
            'message_id': message_id,
            'media_group_id': 'group-1',
            'photo': [{'file_id': f'photo-{message_id}', 'file_size': 1024}],
        }
        for message_id in range(1, 6)
    ]

    services.handle_media_group_message(
        group_messages[0],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: None,
        lambda media_group_id, source_message_id, payload_json: saved_pending.append((media_group_id, source_message_id, payload_json)),
        lambda media_group_id: group_messages,
        lambda media_group_id: None,
        index.logger,
    )

    assert saved_pending == [('group-1', 1, group_messages[0])]
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

    class FakeTime:
        current = 0.0

        @staticmethod
        def sleep(seconds):
            FakeTime.current += seconds

        @staticmethod
        def time():
            return FakeTime.current

    monkeypatch.setattr(services, 'time', FakeTime)
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

    services.handle_media_group_message(
        group_messages[0],
        fake_send,
        lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True,
        lambda method, payload: None,
        lambda text: None,
        lambda *args, **kwargs: saved_mappings.append((args, kwargs)),
        lambda media_group_id, source_message_id, payload_json: None,
        lambda media_group_id: group_messages,
        lambda media_group_id: None,
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
