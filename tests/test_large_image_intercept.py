import io
from datetime import datetime, timedelta, timezone

import requests
from PIL import Image

from api import index
from api import services


class FakeResponse:
    def __init__(self, ok=True, payload=None, text=''):
        self.ok = ok
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _jpeg_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (width, height), 'red').save(buf, 'JPEG')
    return buf.getvalue()


def _document_message(message_id: int = 790, filename: str = 'big.jpg') -> dict:
    return {
        'message_id': message_id,
        'caption': 'doc caption',
        'document': {
            'file_id': 'doc-file',
            'file_size': 2048,
            'mime_type': 'application/octet-stream',
            'file_name': filename,
        },
    }


def test_image_dimensions_returns_size():
    assert services.image_dimensions(_jpeg_bytes(3840, 1916)) == (3840, 1916)
    png_header = (
        b'\x89PNG\r\n\x1a\n'
        + (13).to_bytes(4, 'big')
        + b'IHDR'
        + (3840).to_bytes(4, 'big')
        + (1916).to_bytes(4, 'big')
        + b'\x00' * 8
    )
    assert services.image_dimensions(png_header) == (3840, 1916)


def test_image_dimensions_returns_none_for_invalid_data():
    assert services.image_dimensions(b'not-an-image') is None


def test_publish_intercepts_oversize_document_image(monkeypatch):
    tg_calls = []
    masto_uploads = []
    saved_pending = []
    keyboard_calls = []

    monkeypatch.setattr(
        index,
        'telegram_request',
        lambda method, payload: tg_calls.append((method, payload))
        or FakeResponse(ok=True, payload={'result': {'message_id': 778}}),
    )
    monkeypatch.setattr(index, 'post_to_mastodon', lambda text: {'id': 'masto'})
    monkeypatch.setattr('api.clients.get_tg_file_path', lambda file_id: 'docs/path')
    monkeypatch.setattr('api.clients.download_tg_file', lambda file_path: _jpeg_bytes(3840, 1916))
    monkeypatch.setattr(
        'api.clients.upload_mastodon_media',
        lambda content, filename, mime_type: masto_uploads.append((filename, mime_type))
        or {'id': 'media'},
    )
    monkeypatch.setattr(
        'api.repositories.save_pending_large_image',
        lambda sid, msg, width, height: saved_pending.append((sid, width, height)),
    )
    monkeypatch.setattr(
        'api.clients.send_inline_keyboard',
        lambda cid, text, buttons: keyboard_calls.append((cid, text, buttons)),
    )

    services.publish_message(
        _document_message(),
        lambda chat_id, text, reply_to=None: None,
        lambda chat_id, message_id, text: True,
        index.telegram_request,
        index.post_to_mastodon,
        lambda *args, **kwargs: None,
        index.logger,
    )

    assert saved_pending == [(790, 3840, 1916)]
    assert keyboard_calls
    text, buttons = keyboard_calls[0][1], keyboard_calls[0][2]
    assert '3840×1916' in text
    assert '2560px' in text
    assert '发布后图片会被压缩，清晰度下降。请确认是否发送。' in text
    assert buttons[0][0]['text'] == '继续发送'
    assert buttons[0][1]['text'] == '取消'
    assert buttons[0][0]['callback_data'] == 'confirm_large:790'
    assert buttons[0][1]['callback_data'] == 'cancel_large:790'
    assert tg_calls == []
    assert masto_uploads == []


def _install_publish_mocks(monkeypatch, content_bytes):
    tg_calls = []
    masto_uploads = []
    saved_mappings = []

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
        return FakeRequestsResponse(ok=True, payload={'id': 'masto-1'})

    monkeypatch.setattr(index, 'telegram_request', fake_telegram_request)
    monkeypatch.setattr(index, 'post_to_mastodon', lambda text: {'id': 'should-not-be-used'})
    monkeypatch.setattr('api.clients.get_tg_file_path', lambda file_id: 'docs/path')
    monkeypatch.setattr('api.clients.download_tg_file', lambda file_path: content_bytes)
    monkeypatch.setattr(
        'api.clients.upload_mastodon_media',
        lambda file_content, filename, mime_type: masto_uploads.append((filename, mime_type))
        or {'id': 'media-1'},
    )
    monkeypatch.setattr('api.clients.req.post', fake_requests_post)
    monkeypatch.setattr(requests, 'post', fake_requests_post)

    return tg_calls, masto_uploads, saved_mappings


def _publish(msg, send_tg_message, **kwargs):
    services.publish_message(
        msg,
        send_tg_message,
        lambda chat_id, message_id, text: True,
        index.telegram_request,
        index.post_to_mastodon,
        lambda *args, **kwargs_: None,
        index.logger,
        **kwargs,
    )


def test_publish_allows_small_document_image(monkeypatch):
    tg_calls, masto_uploads, _ = _install_publish_mocks(monkeypatch, _jpeg_bytes(1000, 800))

    _publish(_document_message(), lambda chat_id, text, reply_to=None: {'result': {'message_id': 9004}})

    assert tg_calls == []
    assert masto_uploads == [('big.jpg', 'image/jpeg')]


def test_publish_confirmed_oversize_bypasses_intercept(monkeypatch):
    tg_calls, masto_uploads, _ = _install_publish_mocks(monkeypatch, _jpeg_bytes(3840, 1916))

    _publish(
        _document_message(),
        lambda chat_id, text, reply_to=None: {'result': {'message_id': 9004}},
        confirm_oversize=True,
    )

    assert tg_calls == []
    assert masto_uploads == [('big.jpg', 'image/jpeg')]


def test_confirm_oversize_callback_publishes(monkeypatch):
    published = []
    monkeypatch.setattr(index, 'publish_message', lambda *args, **kwargs: published.append(kwargs))
    monkeypatch.setattr(
        'api.repositories.get_pending_large_image',
        lambda sid: {
            'message_json': {'message_id': sid, 'caption': 'x'},
            'created_at': datetime.now(timezone.utc),
        },
    )
    monkeypatch.setattr('api.repositories.delete_pending_large_image', lambda sid: None)
    monkeypatch.setattr(index, 'answer_callback_query', lambda cid, text=None, show_alert=False: True)

    index.confirm_oversize_image(
        {'id': 'cb', 'from': {'id': 1}, 'data': 'confirm_large:123', 'message': {'message_id': 999}},
        '123',
    )

    assert len(published) == 1
    assert published[0]['confirm_oversize'] is True
    assert published[0]['prompt_message_id'] == 999


def test_confirm_oversize_callback_expired_does_not_publish(monkeypatch):
    published = []
    monkeypatch.setattr(index, 'publish_message', lambda *args, **kwargs: published.append(kwargs))
    monkeypatch.setattr(
        'api.repositories.get_pending_large_image',
        lambda sid: {
            'message_json': {'message_id': sid, 'caption': 'x'},
            'created_at': datetime.now(timezone.utc) - timedelta(minutes=11),
        },
    )
    monkeypatch.setattr('api.repositories.delete_pending_large_image', lambda sid: None)
    monkeypatch.setattr(index, 'answer_callback_query', lambda cid, text=None, show_alert=False: True)

    index.confirm_oversize_image(
        {'id': 'cb', 'from': {'id': 1}, 'data': 'confirm_large:123', 'message': {'message_id': 999}},
        '123',
    )

    assert published == []


def test_cancel_oversize_callback(monkeypatch):
    deleted = []
    answered = []
    edited = []
    monkeypatch.setattr('api.repositories.delete_pending_large_image', lambda sid: deleted.append(sid))
    monkeypatch.setattr(
        index, 'answer_callback_query', lambda cid, text=None, show_alert=False: answered.append(text)
    )
    monkeypatch.setattr(
        index, 'edit_message_text', lambda cid, message_id, text: edited.append((message_id, text))
    )

    index.cancel_oversize_image(
        {'id': 'cb', 'from': {'id': 1}, 'data': 'cancel_large:123', 'message': {'message_id': 888}},
        '123',
    )

    assert deleted == [123]
    assert answered == ['已取消发布']
    assert edited == [(888, '🚫 <b>已取消发布</b>')]
