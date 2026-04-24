import sys
import types

fake_upstash_redis = types.ModuleType('upstash_redis')
setattr(fake_upstash_redis, 'Redis', lambda *args, **kwargs: None)
sys.modules.setdefault('upstash_redis', fake_upstash_redis)

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
