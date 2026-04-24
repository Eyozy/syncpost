import sys
import types

fake_upstash_redis = types.ModuleType('upstash_redis')
setattr(fake_upstash_redis, 'Redis', lambda *args, **kwargs: None)
sys.modules.setdefault('upstash_redis', fake_upstash_redis)

from api import index


class FakeRedis:
    def __init__(self):
        self.store = {}

    def setex(self, key, ttl, value):
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)


def test_get_mapping_falls_back_to_channel_message_id(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(index, 'redis', fake_redis)
    monkeypatch.setattr(index, 'TG_CHANNEL_ID', '@channel')

    index.save_mapping(100, 200, 'masto-1')

    mapping = index.get_mapping(200)

    assert mapping == {
        'source': 100,
        'tg_channel': 200,
        'masto': 'masto-1',
        'timestamp': mapping['timestamp'],
    }


def test_delete_mapping_removes_source_and_channel_keys(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(index, 'redis', fake_redis)
    monkeypatch.setattr(index, 'TG_CHANNEL_ID', '@channel')

    index.save_mapping(101, 201, 'masto-2')

    index.delete_mapping(201)

    assert fake_redis.store == {}


def test_get_mapping_supports_channel_scoped_reply_ids(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(index, 'redis', fake_redis)
    monkeypatch.setattr(index, 'TG_CHANNEL_ID', '@channel')

    index.save_mapping(103, 203, 'masto-3')

    mapping = index.get_mapping(203)

    assert mapping['source'] == 103
    assert fake_redis.get('msg:@channel:203') is not None


def test_handle_delete_command_uses_source_mapping_for_private_chat_cleanup(monkeypatch):
    deleted = []
    sent = []

    monkeypatch.setattr(index, 'get_mapping', lambda message_id: {
        'source': 101,
        'tg_channel': 201,
        'masto': 'masto-3',
    } if message_id == 201 else None)
    mapping_deleted = []

    monkeypatch.setattr(index, 'delete_mastodon_status', lambda status_id: status_id == 'masto-3')
    monkeypatch.setattr(index, 'delete_mapping', lambda source_msg_id: mapping_deleted.append(source_msg_id))
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))

    def fake_delete_tg_message(chat_id, message_id):
        deleted.append((chat_id, message_id))
        return True

    monkeypatch.setattr(index, 'delete_tg_message', fake_delete_tg_message)

    index.handle_delete_command({
        'message_id': 301,
        'reply_to_message': {'message_id': 201},
    })

    assert deleted == [
        (None, 201),
        (index.ADMIN_ID, 101),
        (index.ADMIN_ID, 301),
    ]
    assert mapping_deleted == [101]
    assert sent == [
        (index.ADMIN_ID, '✅ <b>删除成功</b>\n\n已从以下平台删除此消息：\n• Telegram、Mastodon', None),
    ]


def test_handle_delete_command_skips_missing_platform_targets(monkeypatch):
    deleted = []
    sent = []
    masto_calls = []

    monkeypatch.setattr(index, 'get_mapping', lambda message_id: {
        'source': 102,
        'tg_channel': 202,
        'masto': None,
    } if message_id == 102 else None)
    mapping_deleted = []
    monkeypatch.setattr(index, 'delete_mapping', lambda source_msg_id: mapping_deleted.append(source_msg_id))
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))
    monkeypatch.setattr(index, 'delete_mastodon_status', lambda status_id: masto_calls.append(status_id) or True)

    def fake_delete_tg_message(chat_id, message_id):
        deleted.append((chat_id, message_id))
        return True

    monkeypatch.setattr(index, 'delete_tg_message', fake_delete_tg_message)

    index.handle_delete_command({
        'message_id': 302,
        'reply_to_message': {'message_id': 102},
    })

    assert deleted == [
        (None, 202),
        (index.ADMIN_ID, 102),
        (index.ADMIN_ID, 302),
    ]
    assert masto_calls == []
    assert mapping_deleted == [102]
    assert sent == [
        (index.ADMIN_ID, '✅ <b>删除成功</b>\n\n已从以下平台删除此消息：\n• Telegram', None),
    ]


def test_handle_delete_command_keeps_mapping_when_platform_delete_fails(monkeypatch):
    deleted = []
    sent = []
    mapping_deleted = []

    monkeypatch.setattr(index, 'get_mapping', lambda message_id: {
        'source': 103,
        'tg_channel': 203,
        'masto': 'masto-4',
    } if message_id == 103 else None)
    monkeypatch.setattr(index, 'delete_mapping', lambda source_msg_id: mapping_deleted.append(source_msg_id))
    monkeypatch.setattr(index, 'delete_mastodon_status', lambda status_id: True)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))

    def fake_delete_tg_message(chat_id, message_id):
        deleted.append((chat_id, message_id))
        if chat_id is None:
            return False
        return True

    monkeypatch.setattr(index, 'delete_tg_message', fake_delete_tg_message)

    index.handle_delete_command({
        'message_id': 303,
        'reply_to_message': {'message_id': 103},
    })

    assert deleted == [
        (None, 203),
        (index.ADMIN_ID, 103),
        (index.ADMIN_ID, 303),
    ]
    assert mapping_deleted == []
    assert sent == [
        (index.ADMIN_ID, '⚠️ 部分删除失败：Telegram', None),
    ]
