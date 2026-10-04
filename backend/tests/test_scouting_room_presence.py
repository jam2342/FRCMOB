from __future__ import annotations

import asyncio
import fnmatch
import unittest

from app.services.scouting_rooms.realtime import ScoutingRoomRealtimeHub


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


if __name__ == "__main__":
    unittest.main()
