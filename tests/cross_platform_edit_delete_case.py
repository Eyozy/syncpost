import importlib.util
import json
import os
import sys
import types
import unittest
from unittest import mock

API_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "api", "index.py"))


class FakeRedis:
    def __init__(self, *args, **kwargs):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


class FakeMastodon:
    updates = []
    deletes = []

    def __init__(self, *args, **kwargs):
        pass

    def status_update(self, status_id, status=None):
        FakeMastodon.updates.append((status_id, status))
        return {"id": status_id}

    def status_delete(self, status_id):
        FakeMastodon.deletes.append(status_id)
        return {"id": status_id}


def load_api_module():
    fake_upstash = types.ModuleType("upstash_redis")
    fake_upstash.Redis = FakeRedis

    env = {
        "ADMIN_ID": "1001",
        "TG_TOKEN": "token",
        "TG_CHANNEL_ID": "@channel",
        "MASTO_TOKEN": "masto-token",
        "MASTO_INSTANCE": "https://m.example",
        "KV_REST_API_URL": "https://redis.example",
        "KV_REST_API_TOKEN": "redis-token",
    }

    with mock.patch.dict(os.environ, env, clear=False):
        with mock.patch.dict(sys.modules, {"upstash_redis": fake_upstash}):
            module_name = f"syncpost_api_test_{os.urandom(4).hex()}"
            spec = importlib.util.spec_from_file_location(module_name, API_FILE)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module


class SyncProcessTests(unittest.TestCase):
    def setUp(self):
        FakeMastodon.updates = []
        FakeMastodon.deletes = []
        fake_mastodon = types.ModuleType("mastodon")
        fake_mastodon.Mastodon = FakeMastodon
        self.mastodon_patcher = mock.patch.dict(sys.modules, {"mastodon": fake_mastodon})
        self.mastodon_patcher.start()
        self.api = load_api_module()
        self.calls = []

        def fake_tg(method, **kwargs):
            self.calls.append((method, kwargs))
            if method == "sendMessage":
                return {"message_id": 9001}
            return {"ok": True}

        self.api.tg = fake_tg

    def tearDown(self):
        self.mastodon_patcher.stop()

    def test_edited_message_updates_telegram_and_mastodon(self):
        mapping = json.dumps({"tg_chan": 321, "masto": 654})
        self.api.redis.set("tg_77", mapping)

        payload = {
            "update_id": 11,
            "edited_message": {
                "message_id": 77,
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "edited body",
            },
        }

        self.api.sync_process(payload)

        channel_edits = [
            c
            for c in self.calls
            if c[0] == "editMessageText" and c[1].get("json", {}).get("message_id") == 321
        ]
        self.assertEqual(1, len(channel_edits))
        self.assertEqual("edited body", channel_edits[0][1]["json"]["text"])
        self.assertEqual([("654", "edited body")], [(str(sid), text) for sid, text in FakeMastodon.updates])

    def test_delete_command_deletes_remote_posts(self):
        mapping = json.dumps({"tg_chan": 222, "masto": 333})
        self.api.redis.set("tg_88", mapping)
        self.api.redis.set("chan_222", mapping)

        payload = {
            "update_id": 22,
            "message": {
                "message_id": 99,
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/delete",
                "reply_to_message": {"message_id": 88},
            },
        }

        self.api.sync_process(payload)

        channel_deletes = [
            c
            for c in self.calls
            if c[0] == "deleteMessage" and c[1].get("json", {}).get("message_id") == 222
        ]
        self.assertEqual(1, len(channel_deletes))
        self.assertEqual([333], FakeMastodon.deletes)
        self.assertIsNone(self.api.redis.get("tg_88"))
        self.assertIsNone(self.api.redis.get("chan_222"))


if __name__ == "__main__":
    unittest.main()
