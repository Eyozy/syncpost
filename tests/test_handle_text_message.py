import sys
import types

fake_upstash_redis = types.ModuleType('upstash_redis')
setattr(fake_upstash_redis, 'Redis', lambda *args, **kwargs: None)
sys.modules.setdefault('upstash_redis', fake_upstash_redis)

from api import index


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
