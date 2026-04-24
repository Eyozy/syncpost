import sys
import types

fake_upstash_redis = types.ModuleType('upstash_redis')
setattr(fake_upstash_redis, 'Redis', lambda *args, **kwargs: None)
sys.modules.setdefault('upstash_redis', fake_upstash_redis)

from api import index


class FakeResponse:
    def __init__(self, ok=True, text='ok'):
        self.ok = ok
        self.text = text


def test_setup_registers_webhook_and_commands(monkeypatch):
    calls = []

    monkeypatch.setattr(index, 'get_missing_config', lambda: [])

    def fake_telegram_request(method, payload):
        calls.append((method, payload))
        return FakeResponse(ok=True)

    monkeypatch.setattr(index, 'telegram_request', fake_telegram_request)
    with index.app.test_request_context('/setup', base_url='https://example.com'):
        message, status = index.setup()

    assert status == 200
    assert calls == [
        ('setWebhook', {
            'url': 'https://example.com/webhook',
            'secret_token': index.TG_WEBHOOK_SECRET,
            'allowed_updates': ['message', 'edited_message', 'callback_query'],
        }),
        ('deleteMyCommands', {}),
        ('setMyCommands', {
            'commands': [
                {'command': 'start', 'description': '显示欢迎消息'},
                {'command': 'delete', 'description': '删除已发布的消息（回复消息后使用）'},
            ]
        }),
    ]
    assert 'Webhook 已设置为 https://example.com/webhook' in message


def test_setup_returns_error_when_set_webhook_fails(monkeypatch):
    monkeypatch.setattr(index, 'get_missing_config', lambda: [])

    def fake_telegram_request(method, payload):
        if method == 'setWebhook':
            return FakeResponse(ok=False, text='bad webhook')
        return FakeResponse(ok=True)

    monkeypatch.setattr(index, 'telegram_request', fake_telegram_request)

    with index.app.test_request_context('/setup', base_url='https://example.com'):
        message, status = index.setup()

    assert status == 500
    assert message == 'Webhook 设置失败：bad webhook'
