from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import fnmatch
import unittest

from app.services.scouting_rooms.realtime import ScoutingRoomRealtimeHub, _ConnectionMeta


class _SharedRedis:
    # The few Redis calls presence uses, shared by hubs standing in for workers.
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def ping(self) -> bool:
        return True

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def mget(self, keys):
        return [self.data.get(key) for key in keys]

    async def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)

    async def scan_iter(self, match=None, count=None):
        for key in list(self.data):
            if match is None or fnmatch.fnmatchcase(key, match):
                yield key


def _hub(redis: _SharedRedis) -> ScoutingRoomRealtimeHub:
    hub = ScoutingRoomRealtimeHub()
    hub._redis_presence_enabled = True
    hub._redis = redis
    return hub


def _connections(presence, profile):
    return sum(int(member.get("connections") or 0) for member in presence if member.get("scout_profile") == profile)


class ScoutingRoomPresenceTests(unittest.TestCase):
    def test_join_on_one_worker_does_not_block_socket_on_another(self):
        async def scenario():
            redis = _SharedRedis()
            worker_a, worker_b = _hub(redis), _hub(redis)
            await worker_a.touch_http_presence("room-1", scout_profile="Scout B", client_id=None)
            await worker_a.touch_http_presence("room-1", scout_profile="Scout C", client_id=None)
            self.assertEqual(_connections(await worker_b.presence_snapshot("room-1"), "Scout B"), 1)
            # Scout B's socket lands on the other worker, which has no local copy.
            presence = await worker_b.clear_http_presence("room-1", "Scout B")
            self.assertEqual(_connections(presence, "Scout B"), 0)
            # Other scouts' presence is left alone.
            self.assertEqual(_connections(presence, "Scout C"), 1)

        asyncio.run(scenario())

    def test_clearing_keeps_live_socket_presence(self):
        async def scenario():
            redis = _SharedRedis()
            worker_a, worker_b = _hub(redis), _hub(redis)
            await worker_a.touch_http_presence("room-1", scout_profile="Scout B", client_id="tab-1")
            redis.data["scouting:rooms:presence:room-1:tab-2-abc123"] = (
                '{"room_key": "room-1", "scout_profile": "Scout B", "connection_id": "tab-2-abc123"}'
            )
            presence = await worker_b.clear_http_presence("room-1", "Scout B")
            # A real socket for the same name still counts, so replacement rules apply.
            self.assertEqual(_connections(presence, "Scout B"), 1)

        asyncio.run(scenario())

    def test_takeover_closes_only_sockets_older_than_the_new_one(self):
        class _Socket:
            def __init__(self):
                self.closed = None

            async def close(self, code=1000, reason=""):
                self.closed = code

        async def scenario():
            hub = _hub(_SharedRedis())
            now = datetime.now(timezone.utc)
            old, new = _Socket(), _Socket()
            for socket, connected_at in ((old, now - timedelta(seconds=30)), (new, now + timedelta(seconds=1))):
                hub._connection_meta[socket] = _ConnectionMeta(
                    room_key="room-1", scout_profile="Scout B", client_id=None,
                    connection_id=f"c-{id(socket)}", connected_at=connected_at, seen_at=connected_at,
                )
                hub._room_connections.setdefault("room-1", set()).add(socket)
            removed, _presence = await hub.disconnect_scout_profile(
                "room-1", "Scout B", reason="Session replaced", connected_before=now,
            )
            self.assertEqual(removed, 1)
            self.assertEqual(old.closed, 4403)
            self.assertIsNone(new.closed)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
