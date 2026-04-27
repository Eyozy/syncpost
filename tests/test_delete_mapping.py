from api import index
from api import repositories


class FakeConnection:
    def __init__(self):
        self.mappings = {}
        self.rate_limits = {}
        self.last_query = None
        self.last_params = None

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.last_query = query
        self.last_params = params
        normalized = " ".join(query.split()).lower()

        if normalized.startswith("insert into message_mappings"):
            source_id, tg_channel_id, masto_id = params
            self.mappings[source_id] = {
                'source': source_id,
                'tg_channel': tg_channel_id,
                'masto': masto_id,
                'timestamp': 'now',
            }
            return

        if "from message_mappings" in normalized:
            lookup_id = params[0]
            for mapping in self.mappings.values():
                if mapping['source'] == lookup_id or mapping['tg_channel'] == lookup_id:
                    self._fetchone = mapping
                    return
            self._fetchone = None
            return

        if normalized.startswith("delete from message_mappings"):
            source_id = params[0]
            for key, mapping in list(self.mappings.items()):
                if mapping['source'] == source_id or mapping['tg_channel'] == source_id:
                    self.mappings.pop(key, None)
            return

    def fetchone(self):
        return getattr(self, '_fetchone', None)

    def commit(self):
        return None


class FakeDbContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        return False


def test_get_mapping_falls_back_to_channel_message_id(monkeypatch):
    fake_connection = FakeConnection()
    monkeypatch.setattr(repositories, 'is_database_configured', lambda: True)
    monkeypatch.setattr(repositories, 'get_db_connection', lambda: FakeDbContext(fake_connection))

    repositories.save_mapping(100, 200, 'masto-1')

    mapping = repositories.get_mapping(200)

    assert mapping == {
        'source': 100,
        'tg_channel': 200,
        'masto': 'masto-1',
        'timestamp': mapping['timestamp'],
    }


def test_delete_mapping_removes_source_and_channel_keys(monkeypatch):
    fake_connection = FakeConnection()
    monkeypatch.setattr(repositories, 'is_database_configured', lambda: True)
    monkeypatch.setattr(repositories, 'get_db_connection', lambda: FakeDbContext(fake_connection))
    deleted = []

    monkeypatch.setattr(repositories, 'get_mapping', lambda message_id: {
        'source': 101,
        'tg_channel': 201,
        'masto': 'masto-2',
        'timestamp': 'now',
    } if message_id == 201 else None)

    original_execute = fake_connection.execute

    def tracking_execute(query, params=None):
        deleted.append((query, params))
        return original_execute(query, params)

    fake_connection.execute = tracking_execute

    repositories.delete_mapping(201)

    assert deleted[-1][1] == (101,)


def test_get_mapping_supports_channel_scoped_reply_ids(monkeypatch):
    fake_connection = FakeConnection()
    monkeypatch.setattr(repositories, 'is_database_configured', lambda: True)
    monkeypatch.setattr(repositories, 'get_db_connection', lambda: FakeDbContext(fake_connection))

    repositories.save_mapping(103, 203, 'masto-3')

    mapping = repositories.get_mapping(203)

    assert mapping['source'] == 103


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


class FakeRateLimitConnection:
    def __init__(self, count=1, should_fail=False):
        self.count = count
        self.should_fail = should_fail

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        if self.should_fail:
            raise RuntimeError('db failed')

    def fetchone(self):
        return {'request_count': self.count}

    def commit(self):
        return None


def test_check_rate_limit_returns_false_when_limit_exceeded(monkeypatch):
    fake_connection = FakeRateLimitConnection(count=11)
    monkeypatch.setattr(repositories, 'is_database_configured', lambda: True)
    monkeypatch.setattr(repositories, 'get_db_connection', lambda: FakeDbContext(fake_connection))

    assert repositories.check_rate_limit(123) is False


def test_check_rate_limit_fails_open_on_database_error(monkeypatch):
    fake_connection = FakeRateLimitConnection(should_fail=True)
    monkeypatch.setattr(repositories, 'is_database_configured', lambda: True)
    monkeypatch.setattr(repositories, 'get_db_connection', lambda: FakeDbContext(fake_connection))

    assert repositories.check_rate_limit(123) is True
