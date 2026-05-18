from api import index
from api import repositories
from api import services
from api.config import TG_CHANNEL_ID


class FakeConnection:
    def __init__(self):
        self.mappings = {}
        self.aliases = {}
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
            source_id, tg_channel_id, tg_channel_ids, masto_id, media_group_id = params
            self.mappings[source_id] = {
                'source': source_id,
                'tg_channel': tg_channel_id,
                'tg_channels': tg_channel_ids,
                'tg_channel_messages': [int(msg_id) for msg_id in tg_channel_ids.split(',')] if tg_channel_ids else [],
                'masto': masto_id,
                'media_group_id': media_group_id,
                'timestamp': 'now',
            }
            return

        if normalized.startswith("insert into private_message_aliases"):
            alias_message_id, source_message_id = params
            self.aliases[alias_message_id] = source_message_id
            return

        if "from private_message_aliases" in normalized:
            alias_message_id = params[0]
            source_message_id = self.aliases.get(alias_message_id)
            self._fetchone = (
                {'source_message_id': source_message_id}
                if source_message_id is not None
                else None
            )
            return

        if "from message_mappings" in normalized:
            lookup_id = params[0]
            preferred = None
            fallback = None
            for mapping in self.mappings.values():
                if mapping['source'] == lookup_id:
                    preferred = mapping
                    break
                if mapping['tg_channel'] == lookup_id and fallback is None:
                    fallback = mapping
            self._fetchone = preferred or fallback
            if self._fetchone:
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
        'tg_channels': None,
        'tg_channel_messages': [],
        'masto': 'masto-1',
        'media_group_id': None,
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


def test_get_mapping_prefers_source_message_id_over_channel_message_id(monkeypatch):
    fake_connection = FakeConnection()
    monkeypatch.setattr(repositories, 'is_database_configured', lambda: True)
    monkeypatch.setattr(repositories, 'get_db_connection', lambda: FakeDbContext(fake_connection))

    repositories.save_mapping(101, 500, 'masto-channel-match')
    repositories.save_mapping(500, 900, 'masto-source-match')

    mapping = repositories.get_mapping(500)

    assert mapping['source'] == 500
    assert mapping['tg_channel'] == 900
    assert mapping['masto'] == 'masto-source-match'


def test_resolve_source_message_id_reads_private_alias(monkeypatch):
    fake_connection = FakeConnection()
    fake_connection.aliases[9100] = 533
    monkeypatch.setattr(repositories, 'is_database_configured', lambda: True)
    monkeypatch.setattr(repositories, 'get_db_connection', lambda: FakeDbContext(fake_connection))

    assert repositories.resolve_source_message_id(9100) == 533
    assert repositories.resolve_source_message_id(9101) == 9101


def test_handle_delete_command_deletes_directly(monkeypatch):
    deleted = []

    monkeypatch.setattr(
        index,
        'delete_message',
        lambda *args, **kwargs: deleted.append(args[0]),
    )

    msg = {
        'message_id': 301,
        'reply_to_message': {'message_id': 201},
    }

    index.handle_delete_command(msg)

    assert deleted == [msg]


def test_delete_message_uses_source_mapping_for_private_chat_cleanup(monkeypatch):
    deleted = []
    deleted_batches = []
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
    monkeypatch.setattr(index, 'delete_tg_messages', lambda chat_id, message_ids: deleted_batches.append((chat_id, message_ids)) or True)

    services.delete_message(
        {
            'message_id': 301,
            'reply_to_message': {'message_id': 201},
        },
        index.send_tg_message,
        index.get_mapping,
        (lambda media_group_id: None),
        lambda media_group_id: [],
        lambda source_message_id: 0,
        lambda media_group_id: 0,
        lambda media_group_id: [],
        lambda media_group_id: None,
        index.has_target,
        index.delete_tg_message,
        index.delete_tg_messages,
        index.delete_mastodon_status,
        index.delete_mapping,
        lambda message_id: message_id,
    )

    assert deleted == [
        (None, 201),
        (index.ADMIN_ID, 101),
        (index.ADMIN_ID, 301),
    ]
    assert mapping_deleted == [101]
    assert sent == [
        (index.ADMIN_ID, '✅ <b>删除成功</b>\n\n已从以下平台删除此消息：\n• Telegram、Mastodon', None),
    ]
    assert deleted_batches == []


def test_delete_message_accepts_reply_to_status_alias(monkeypatch):
    deleted = []
    sent = []

    monkeypatch.setattr(index, 'get_mapping', lambda message_id: {
        'source': 533,
        'tg_channel': 333,
        'masto': '116592049305306695',
    } if message_id == 533 else None)
    monkeypatch.setattr(index, 'delete_mastodon_status', lambda status_id: True)
    monkeypatch.setattr(index, 'delete_mapping', lambda source_msg_id: None)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))
    monkeypatch.setattr(index, 'delete_tg_messages', lambda chat_id, message_ids: True)

    def fake_delete_tg_message(chat_id, message_id):
        deleted.append((chat_id, message_id))
        return True

    monkeypatch.setattr(index, 'delete_tg_message', fake_delete_tg_message)

    services.delete_message(
        {
            'message_id': 9200,
            'reply_to_message': {'message_id': 9100},
        },
        index.send_tg_message,
        index.get_mapping,
        (lambda media_group_id: None),
        lambda media_group_id: [],
        lambda source_message_id: 0,
        lambda media_group_id: 0,
        lambda media_group_id: [],
        lambda media_group_id: None,
        index.has_target,
        index.delete_tg_message,
        index.delete_tg_messages,
        index.delete_mastodon_status,
        index.delete_mapping,
        lambda message_id: 533 if message_id == 9100 else message_id,
    )

    assert deleted == [
        (None, 333),
        (index.ADMIN_ID, 533),
        (index.ADMIN_ID, 9200),
    ]


def test_delete_message_removes_entire_private_media_group(monkeypatch):
    deleted = []
    deleted_batches = []
    sent = []

    monkeypatch.setattr(index, 'get_mapping', lambda message_id: {
        'source': 501,
        'tg_channel': 601,
        'tg_channel_messages': [601, 602, 603, 604],
        'masto': 'masto-group-1',
        'media_group_id': 'group-delete-1',
    } if message_id == 501 else None)
    monkeypatch.setattr(index, 'get_media_group_source_message_ids', lambda media_group_id: [501, 502, 503, 504])
    monkeypatch.setattr(index, 'delete_mastodon_status', lambda status_id: True)
    monkeypatch.setattr(index, 'delete_mapping', lambda source_msg_id: None)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))
    monkeypatch.setattr(index, 'delete_tg_messages', lambda chat_id, message_ids: deleted_batches.append((chat_id, message_ids)) or True)

    def fake_delete_tg_message(chat_id, message_id):
        deleted.append((chat_id, message_id))
        return True

    monkeypatch.setattr(index, 'delete_tg_message', fake_delete_tg_message)

    services.delete_message(
        {
            'message_id': 701,
            'reply_to_message': {'message_id': 501, 'media_group_id': 'group-delete-1'},
        },
        index.send_tg_message,
        index.get_mapping,
        (lambda media_group_id: None),
        index.get_media_group_source_message_ids,
        lambda source_message_id: 0,
        lambda media_group_id: 0,
        lambda media_group_id: [],
        lambda media_group_id: None,
        index.has_target,
        index.delete_tg_message,
        index.delete_tg_messages,
        index.delete_mastodon_status,
        index.delete_mapping,
    )

    assert deleted_batches == [
        (index.ADMIN_ID, [501, 502, 503, 504]),
    ]
    assert deleted == [
        (TG_CHANNEL_ID, 601),
        (TG_CHANNEL_ID, 602),
        (TG_CHANNEL_ID, 603),
        (TG_CHANNEL_ID, 604),
        (index.ADMIN_ID, 701),
    ]


def test_delete_message_removes_pending_tail_items_for_partial_album_mapping(monkeypatch):
    deleted = []
    deleted_batches = []

    monkeypatch.setattr(index, 'get_mapping', lambda message_id: {
        'source': 801,
        'tg_channel': 901,
        'tg_channel_messages': [901, 902, 903],
        'masto': 'masto-group-2',
        'media_group_id': 'group-delete-2',
    } if message_id == 801 else None)
    monkeypatch.setattr(index, 'get_media_group_source_message_ids', lambda media_group_id: [801, 802, 803])
    monkeypatch.setattr(index, 'delete_mastodon_status', lambda status_id: True)
    monkeypatch.setattr(index, 'delete_mapping', lambda source_msg_id: None)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: None)
    monkeypatch.setattr(index, 'delete_tg_messages', lambda chat_id, message_ids: deleted_batches.append((chat_id, message_ids)) or True)

    cleared_groups = []

    def fake_delete_tg_message(chat_id, message_id):
        deleted.append((chat_id, message_id))
        return True

    monkeypatch.setattr(index, 'delete_tg_message', fake_delete_tg_message)

    services.delete_message(
        {
            'message_id': 999,
            'reply_to_message': {'message_id': 801, 'media_group_id': 'group-delete-2'},
        },
        index.send_tg_message,
        index.get_mapping,
        (lambda media_group_id: None),
        index.get_media_group_source_message_ids,
        lambda source_message_id: 0,
        lambda media_group_id: 0,
        lambda media_group_id: [{'message_id': 804}] if media_group_id == 'group-delete-2' else [],
        lambda media_group_id: cleared_groups.append(media_group_id),
        index.has_target,
        index.delete_tg_message,
        index.delete_tg_messages,
        index.delete_mastodon_status,
        index.delete_mapping,
    )

    assert deleted_batches == [
        (index.ADMIN_ID, [801, 802, 803, 804]),
    ]
    assert cleared_groups == ['group-delete-2']


def test_delete_message_finds_album_mapping_when_replying_to_unmapped_tail_item(monkeypatch):
    deleted_batches = []
    sent = []

    monkeypatch.setattr(index, 'get_mapping', lambda message_id: None)
    monkeypatch.setattr(index, 'get_media_group_source_message_ids', lambda media_group_id: [901, 902, 903])
    monkeypatch.setattr(index, 'delete_mastodon_status', lambda status_id: True)
    monkeypatch.setattr(index, 'delete_mapping', lambda source_msg_id: None)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))
    monkeypatch.setattr(index, 'delete_tg_message', lambda chat_id, message_id: True)
    monkeypatch.setattr(index, 'delete_tg_messages', lambda chat_id, message_ids: deleted_batches.append((chat_id, message_ids)) or True)

    services.delete_message(
        {
            'message_id': 999,
            'reply_to_message': {'message_id': 904, 'media_group_id': 'group-delete-3'},
        },
        index.send_tg_message,
        index.get_mapping,
        lambda media_group_id: {
            'source': 901,
            'tg_channel': 1001,
            'tg_channel_messages': [1001, 1002, 1003],
            'masto': 'masto-group-3',
            'media_group_id': 'group-delete-3',
        },
        index.get_media_group_source_message_ids,
        lambda source_message_id: 0,
        lambda media_group_id: 0,
        lambda media_group_id: [{'message_id': 904}] if media_group_id == 'group-delete-3' else [],
        lambda media_group_id: None,
        index.has_target,
        index.delete_tg_message,
        index.delete_tg_messages,
        index.delete_mastodon_status,
        index.delete_mapping,
        lambda message_id: message_id,
    )

    assert deleted_batches == [
        (index.ADMIN_ID, [901, 902, 903, 904]),
    ]
    assert sent == [
        (index.ADMIN_ID, '✅ <b>删除成功</b>\n\n已从以下平台删除此消息：\n• Telegram、Mastodon', None),
    ]


def test_delete_message_aggregates_split_album_channel_message_ids(monkeypatch):
    deleted = []
    deleted_batches = []

    monkeypatch.setattr(index, 'get_mapping', lambda message_id: {
        'source': 901,
        'tg_channel': 1001,
        'tg_channel_messages': [1001, 1002],
        'masto': 'masto-group-split',
        'media_group_id': 'group-split-1',
    } if message_id == 901 else None)
    monkeypatch.setattr(index, 'get_media_group_source_message_ids', lambda media_group_id: [901, 902, 903, 904])
    monkeypatch.setattr(index, 'delete_mastodon_status', lambda status_id: True)
    monkeypatch.setattr(index, 'delete_mapping', lambda source_msg_id: None)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: None)
    monkeypatch.setattr(index, 'delete_tg_messages', lambda chat_id, message_ids: deleted_batches.append((chat_id, message_ids)) or True)

    def fake_delete_tg_message(chat_id, message_id):
        deleted.append((chat_id, message_id))
        return True

    monkeypatch.setattr(index, 'delete_tg_message', fake_delete_tg_message)

    services.delete_message(
        {
            'message_id': 9999,
            'reply_to_message': {'message_id': 901, 'media_group_id': 'group-split-1'},
        },
        index.send_tg_message,
        index.get_mapping,
        lambda media_group_id: None,
        index.get_media_group_source_message_ids,
        lambda source_message_id: 0,
        lambda media_group_id: 0,
        lambda media_group_id: [],
        lambda media_group_id: None,
        index.has_target,
        index.delete_tg_message,
        index.delete_tg_messages,
        index.delete_mastodon_status,
        index.delete_mapping,
        get_mappings_by_media_group_id=lambda media_group_id: [
            {
                'source': 901,
                'tg_channel': 1001,
                'tg_channel_messages': [1001, 1002],
                'masto': 'masto-group-split',
                'media_group_id': 'group-split-1',
            },
            {
                'source': 903,
                'tg_channel': 1003,
                'tg_channel_messages': [1003, 1004],
                'masto': 'masto-group-split',
                'media_group_id': 'group-split-1',
            },
        ],
    )

    assert deleted == [
        (TG_CHANNEL_ID, 1001),
        (TG_CHANNEL_ID, 1002),
        (TG_CHANNEL_ID, 1003),
        (TG_CHANNEL_ID, 1004),
        (index.ADMIN_ID, 9999),
    ]
    assert deleted_batches == [
        (index.ADMIN_ID, [901, 902, 903, 904]),
    ]


def test_delete_message_skips_missing_platform_targets(monkeypatch):
    deleted = []
    deleted_batches = []
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
    monkeypatch.setattr(index, 'delete_tg_messages', lambda chat_id, message_ids: deleted_batches.append((chat_id, message_ids)) or True)

    services.delete_message(
        {
            'message_id': 302,
            'reply_to_message': {'message_id': 102},
        },
        index.send_tg_message,
        index.get_mapping,
        (lambda media_group_id: None),
        lambda media_group_id: [],
        lambda source_message_id: 0,
        lambda media_group_id: 0,
        lambda media_group_id: [],
        lambda media_group_id: None,
        index.has_target,
        index.delete_tg_message,
        index.delete_tg_messages,
        index.delete_mastodon_status,
        index.delete_mapping,
    )

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
    assert deleted_batches == []


def test_delete_message_keeps_mapping_when_platform_delete_fails(monkeypatch):
    deleted = []
    deleted_batches = []
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
    monkeypatch.setattr(index, 'delete_tg_messages', lambda chat_id, message_ids: deleted_batches.append((chat_id, message_ids)) or True)

    services.delete_message(
        {
            'message_id': 303,
            'reply_to_message': {'message_id': 103},
        },
        index.send_tg_message,
        index.get_mapping,
        (lambda media_group_id: None),
        lambda media_group_id: [],
        lambda source_message_id: 0,
        lambda media_group_id: 0,
        lambda media_group_id: [],
        lambda media_group_id: None,
        index.has_target,
        index.delete_tg_message,
        index.delete_tg_messages,
        index.delete_mastodon_status,
        index.delete_mapping,
    )

    assert deleted == [
        (None, 203),
        (index.ADMIN_ID, 103),
        (index.ADMIN_ID, 303),
    ]
    assert mapping_deleted == []
    assert sent == [
        (index.ADMIN_ID, '⚠️ 部分删除失败：Telegram', None),
    ]
    assert deleted_batches == []


def test_delete_message_deletes_all_album_messages_without_false_failure(monkeypatch):
    deleted = []
    deleted_batches = []
    sent = []
    mapping_deleted = []

    monkeypatch.setattr(index, 'get_mapping', lambda message_id: {
        'source': 104,
        'tg_channel': 204,
        'tg_channel_messages': [204, 205],
        'masto': 'masto-5',
        'media_group_id': 'album-5',
    } if message_id == 104 else None)
    monkeypatch.setattr(index, 'get_media_group_source_message_ids', lambda media_group_id: [104, 105])
    monkeypatch.setattr(index, 'delete_mapping', lambda source_msg_id: mapping_deleted.append(source_msg_id))
    monkeypatch.setattr(index, 'delete_mastodon_status', lambda status_id: True)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))

    def fake_delete_tg_message(chat_id, message_id):
        deleted.append((chat_id, message_id))
        return True

    monkeypatch.setattr(index, 'delete_tg_message', fake_delete_tg_message)
    monkeypatch.setattr(index, 'delete_tg_messages', lambda chat_id, message_ids: deleted_batches.append((chat_id, message_ids)) or True)

    services.delete_message(
        {
            'message_id': 304,
            'reply_to_message': {'message_id': 104},
        },
        index.send_tg_message,
        index.get_mapping,
        (lambda media_group_id: None),
        index.get_media_group_source_message_ids,
        lambda source_message_id: 0,
        lambda media_group_id: 0,
        lambda media_group_id: [],
        lambda media_group_id: None,
        index.has_target,
        index.delete_tg_message,
        index.delete_tg_messages,
        index.delete_mastodon_status,
        index.delete_mapping,
    )

    assert deleted_batches == [
        (index.ADMIN_ID, [104, 105]),
    ]
    assert deleted == [
        (None, 204),
        (None, 205),
        (index.ADMIN_ID, 304),
    ]
    assert mapping_deleted == [104]
    assert sent == [
        (index.ADMIN_ID, '✅ <b>删除成功</b>\n\n已从以下平台删除此消息：\n• Telegram、Mastodon', None),
    ]


def test_delete_message_cancels_pending_publish_when_mapping_not_ready(monkeypatch):
    deleted = []
    deleted_batches = []
    sent = []

    monkeypatch.setattr(index, 'get_mapping', lambda message_id: None)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))

    def fake_delete_tg_message(chat_id, message_id):
        deleted.append((chat_id, message_id))
        return True

    monkeypatch.setattr(index, 'delete_tg_message', fake_delete_tg_message)
    monkeypatch.setattr(index, 'delete_tg_messages', lambda chat_id, message_ids: deleted_batches.append((chat_id, message_ids)) or True)

    services.delete_message(
        {
            'message_id': 401,
            'reply_to_message': {'message_id': 400},
        },
        index.send_tg_message,
        index.get_mapping,
        (lambda media_group_id: None),
        lambda media_group_id: [],
        lambda source_message_id: 1 if source_message_id == 400 else 0,
        lambda media_group_id: 0,
        lambda media_group_id: [],
        lambda media_group_id: None,
        index.has_target,
        index.delete_tg_message,
        index.delete_tg_messages,
        index.delete_mastodon_status,
        index.delete_mapping,
    )

    assert deleted == [
        (index.ADMIN_ID, 400),
        (index.ADMIN_ID, 401),
    ]
    assert sent == [
        (index.ADMIN_ID, '✅ <b>删除成功</b>\n\n已取消尚未同步完成的消息任务。', None),
    ]
    assert deleted_batches == []


def test_delete_message_cancels_pending_media_group_when_mapping_not_ready(monkeypatch):
    deleted = []
    deleted_batches = []
    sent = []
    cleared_groups = []

    monkeypatch.setattr(index, 'get_mapping', lambda message_id: None)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))

    def fake_delete_tg_message(chat_id, message_id):
        deleted.append((chat_id, message_id))
        return True

    monkeypatch.setattr(index, 'delete_tg_message', fake_delete_tg_message)
    monkeypatch.setattr(index, 'delete_tg_messages', lambda chat_id, message_ids: deleted_batches.append((chat_id, message_ids)) or True)

    services.delete_message(
        {
            'message_id': 501,
            'reply_to_message': {'message_id': 500, 'media_group_id': 'album-pending'},
        },
        index.send_tg_message,
        index.get_mapping,
        (lambda media_group_id: None),
        lambda media_group_id: [],
        lambda source_message_id: 0,
        lambda media_group_id: 1 if media_group_id == 'album-pending' else 0,
        lambda media_group_id: [{'message_id': 500}] if media_group_id == 'album-pending' else [],
        lambda media_group_id: cleared_groups.append(media_group_id),
        index.has_target,
        index.delete_tg_message,
        index.delete_tg_messages,
        index.delete_mastodon_status,
        index.delete_mapping,
    )

    assert cleared_groups == ['album-pending']
    assert deleted == [
        (index.ADMIN_ID, 500),
        (index.ADMIN_ID, 501),
    ]
    assert sent == [
        (index.ADMIN_ID, '✅ <b>删除成功</b>\n\n已取消尚未同步完成的消息任务。', None),
    ]
    assert deleted_batches == []


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
