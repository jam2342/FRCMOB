"""Live flags: a tag tapped mid-match reaches the room at once, from the socket's own identity."""
import unittest
from unittest import mock

from app.api import routes_scouting_rooms as rooms
from app.db import models
from tests import test_scout_identity_security as identity
from tests.test_team_workspaces import EVENT


def _next_of_type(ws, wanted: str, limit: int = 6) -> dict:
    for _ in range(limit):
        message = ws.receive_json()
        if message.get('type') == wanted:
            return message
    raise AssertionError(f'no {wanted} message')


class LiveFlagTests(unittest.TestCase):
    # The identity tests' room and member fixture, without running those tests again here.
    setUp = identity.ScoutIdentitySecurityTests.setUp
    url = identity.ScoutIdentitySecurityTests.url
    join = identity.ScoutIdentitySecurityTests.join

    def _recording_broadcasts(self):
        sent: list[tuple[str, dict]] = []

        async def _record(room_key, payload):
            sent.append((room_key, payload))

        return sent, mock.patch.object(rooms, '_broadcast_room_message', _record)

    def _sync(self, ws):
        # The fixture's room has no other listener: a pong after the flag means it was handled.
        ws.send_json({'type': 'ping'})
        _next_of_type(ws, 'pong')

    def test_flag_is_broadcast_with_the_sockets_own_name(self):
        sent, patch = self._recording_broadcasts()
        with patch, self.client.websocket_connect(self.url()) as ws:
            self.assertEqual(ws.receive_json()['type'], 'snapshot')
            ws.send_json({
                'type': 'flag', 'tag': 'BROKE_DOWN', 'team_key': 'frc118', 'match_key': f'{EVENT}_qm1',
                'scout_profile': 'Someone Else',
            })
            self._sync(ws)
        flags = [payload for _, payload in sent if payload['type'] == 'live_flag']
        self.assertEqual(len(flags), 1)
        self.assertEqual(sent[-1][0], 'identity-room')
        self.assertEqual(flags[0]['tag'], 'broke_down')
        self.assertEqual(flags[0]['team_key'], 'frc118')
        self.assertEqual(flags[0]['match_key'], f'{EVENT}_qm1')
        self.assertEqual(flags[0]['scout_profile'], 'Scout')
        with self.fixture.SessionLocal() as db:
            self.assertEqual(db.query(models.ScoutingRoomEntry).count(), 0)

    def test_unknown_tags_and_malformed_keys_are_refused(self):
        with self.client.websocket_connect(self.url()) as ws:
            self.assertEqual(ws.receive_json()['type'], 'snapshot')
            for bad in (
                {'tag': 'anything goes', 'team_key': 'frc118', 'match_key': f'{EVENT}_qm1'},
                {'tag': 'tipped', 'team_key': '<b>118</b>', 'match_key': f'{EVENT}_qm1'},
                {'tag': 'tipped', 'team_key': 'frc118', 'match_key': 'x'},
            ):
                ws.send_json({'type': 'flag', **bad})
                self.assertIn('flag needs', _next_of_type(ws, 'error')['detail'])

    def test_a_socket_cannot_flood_the_room(self):
        sent, patch = self._recording_broadcasts()
        with patch, mock.patch.object(rooms, '_LIVE_FLAGS_PER_MINUTE', 2):
            with self.client.websocket_connect(self.url()) as ws:
                self.assertEqual(ws.receive_json()['type'], 'snapshot')
                body = {'type': 'flag', 'tag': 'tipped', 'team_key': 'frc118', 'match_key': f'{EVENT}_qm1'}
                ws.send_json(body)
                ws.send_json(body)
                ws.send_json(body)
                self.assertIn('Too many flags', _next_of_type(ws, 'error')['detail'])
        self.assertEqual(len([payload for _, payload in sent if payload['type'] == 'live_flag']), 2)


class LiveFlagBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_flag_from_another_worker_reaches_this_workers_sockets(self):
        # Production runs more than one web worker; the bus carries a flag to the others.
        from app import main

        payload = {'type': 'live_flag', 'room_key': 'r', 'tag': 'broke_down', 'team_key': 'frc254'}
        with mock.patch.object(main.scouting_room_hub, 'broadcast', mock.AsyncMock()) as broadcast:
            await main._handle_scouting_room_bus_message('r', payload)
        broadcast.assert_awaited_once_with('r', payload)
