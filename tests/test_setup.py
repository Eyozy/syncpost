from api import index


class FakeResponse:
    def __init__(self, ok=True, text='ok'):
        self.ok = ok
        self.text = text


class FakeConnection:
    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        return None

    def commit(self):
        return None


class FakeDbContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        return False


def test_setup_registers_webhook_and_commands(monkeypatch):
    calls = []

    monkeypatch.setattr(index, 'SETUP_TOKEN', 'setup-secret')
    monkeypatch.setattr(index, 'get_missing_config', lambda: [])
    monkeypatch.setattr(index, 'init_db', lambda: None)

    def fake_telegram_request(method, payload):
        calls.append((method, payload))
        return FakeResponse(ok=True)

    monkeypatch.setattr(index, 'telegram_request', fake_telegram_request)
    with index.app.test_request_context('/setup?token=setup-secret', base_url='https://example.com'):
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
                {'command': 'edit', 'description': '仅编辑纯文本帖子'},
                {'command': 'edit_image_text', 'description': '新增或修改图片文字'},
                {'command': 'replace_image', 'description': '只替换图片'},
                {'command': 'replace_image_text', 'description': '替换图片和文字'},
                {'command': 'edit_video_text', 'description': '新增或修改视频文字'},
                {'command': 'replace_video', 'description': '只替换视频'},
                {'command': 'replace_video_text', 'description': '替换视频和文字'},
            ]
        }),
    ]
    assert 'Webhook 已设置为 https://example.com/webhook' in message


def test_setup_returns_error_when_set_webhook_fails(monkeypatch):
    monkeypatch.setattr(index, 'SETUP_TOKEN', 'setup-secret')
    monkeypatch.setattr(index, 'get_missing_config', lambda: [])
    monkeypatch.setattr(index, 'init_db', lambda: None)

    def fake_telegram_request(method, payload):
        if method == 'setWebhook':
            return FakeResponse(ok=False, text='bad webhook')
        return FakeResponse(ok=True)

    monkeypatch.setattr(index, 'telegram_request', fake_telegram_request)

    with index.app.test_request_context('/setup?token=setup-secret', base_url='https://example.com'):
        message, status = index.setup()

    assert status == 500
    assert message == 'Webhook 设置失败：bad webhook'


def test_setup_rejects_request_without_valid_token(monkeypatch):
    monkeypatch.setattr(index, 'SETUP_TOKEN', 'setup-secret')

    with index.app.test_request_context('/setup', base_url='https://example.com'):
        message, status = index.setup()

    assert status == 401
    assert message == 'Unauthorized'
