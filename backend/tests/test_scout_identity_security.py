"""Report authors and room credentials survive neither name reuse nor expiry."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest import mock
from urllib.parse import urlencode

from starlette.websockets import WebSocketDisconnect

from app.api import routes_scouting_rooms as rooms, routes_tracks
from app.core import security
from app.db import models
from tests.test_team_workspaces import TeamWorkspaceTests, EVENT
from tests.test_routes_tracks_on_device_sync import _session_body


class ScoutIdentitySecurityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = TeamWorkspaceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.client = self.fixture.client
        self.creation = mock.patch('app.api.routes_workspaces.take_workspace_creation_slot', return_value=True)
        self.creation.start()
        self.addCleanup(self.creation.stop)
        self.lead = self.fixture._create()
        self.lead_headers = self.fixture._headers(self.lead)
        self.scout = self.fixture._join(self.lead['join_code'], 'Scout')
        self.headers = self.fixture._headers(self.scout)
        response = self.client.post('/scouting/rooms', headers=self.headers, json={
            'room_key': 'identity-room', 'event_key': EVENT, 'create_if_missing': True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.token = response.json()['access']['room_access_token']
        self.headers[security.ROOM_ACCESS_HEADER] = self.token

    def save(self, headers=None, *, total=10, saved=1000, report='report'):
        return self.client.post('/scouting/rooms/identity-room/entries', headers=headers or self.headers, json={
            'client_entry_id': report, 'entry': {'match_key': f'{EVENT}_qm1', 'team_key': 'frc118',
                                              'saved_at_ms': saved, 'points': {'total': total}},
        })

    def join(self, headers):
        response = self.client.post('/scouting/rooms', headers=headers, json={'room_key': 'identity-room'})
        self.assertEqual(response.status_code, 200, response.text)
        return headers | {security.ROOM_ACCESS_HEADER: response.json()['access']['room_access_token']}

    def recycle_name(self):
        other = self.fixture._join(self.lead['join_code'], 'Other')
        headers = self.fixture._headers(other)
        removed = self.client.post(f"/workspaces/me/members/{self.scout['me']['id']}/remove", headers=self.lead_headers, json={})
        self.assertEqual(removed.status_code, 200, removed.text)
        renamed = self.client.patch('/workspaces/me/profile', headers=headers, json={'display_name': 'Scout'})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        return other, self.join(headers)

    def url(self, *, room_token=None, workspace_token=None):
        return '/scouting/rooms/identity-room/ws?' + urlencode({
            'scout_profile': 'Scout', 'room_access': room_token or self.token,
            'workspace_access': workspace_token or self.headers[security.WORKSPACE_ACCESS_HEADER],
        })

    def test_recycled_name_cannot_edit_or_receive_old_report_as_save_result(self):
        self.assertEqual(self.save().status_code, 200)
        _, replacement = self.recycle_name()
        response = self.save(replacement, total=999, saved=2000)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertNotIn('entry', response.json())
        with self.fixture.SessionLocal() as db:
            row = db.query(models.ScoutingRoomEntry).one()
            self.assertEqual(row.total_points, 10)
            self.assertEqual(row.author_member_id, self.scout['me']['id'])
        state = self.client.get('/scouting/rooms/identity-room', headers=replacement, params={'scout_profile': 'Scout'}).json()
        self.assertNotEqual(state['access']['room_role'], 'owner')

    def test_same_author_retries_corrects_and_keeps_ownership_after_rename(self):
        first = self.save()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(self.save().json()['created'])
        self.assertEqual(self.save(total=20, saved=2000).json()['entry']['entry']['points']['total'], 20)
        renamed = self.client.patch('/workspaces/me/profile', headers=self.headers, json={'display_name': 'Renamed'})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        fresh = self.join(self.headers)
        corrected = self.save(fresh, total=30, saved=3000)
        self.assertEqual(corrected.status_code, 200, corrected.text)
        self.assertEqual(corrected.json()['entry']['scout_profile'], 'Renamed')
        with self.fixture.SessionLocal() as db:
            row = db.query(models.ScoutingRoomEntry).one()
            self.assertEqual(row.total_points, 30)
            self.assertEqual(row.author_member_id, self.scout['me']['id'])

    def test_legacy_unknown_author_is_not_adopted_or_renamed(self):
        self.assertEqual(self.save().status_code, 200)
        with self.fixture.SessionLocal() as db:
            row = db.query(models.ScoutingRoomEntry).one()
            row.author_member_id = None
            db.commit()
        self.assertEqual(self.save(total=999, saved=2000).status_code, 409)
        self.client.patch('/workspaces/me/profile', headers=self.headers, json={'display_name': 'Renamed'})
        with self.fixture.SessionLocal() as db:
            row = db.query(models.ScoutingRoomEntry).one()
            self.assertEqual((row.total_points, row.scout_profile, row.author_member_id), (10, 'Scout', None))

    def test_legacy_and_other_members_room_tokens_are_rejected(self):
        legacy = security.issue_room_access_token(room_key='identity-room', scout_profile='Scout')['token']
        self.assertEqual(self.save(self.headers | {security.ROOM_ACCESS_HEADER: legacy}).status_code, 403)
        _, replacement = self.recycle_name()
        self.assertEqual(self.save(replacement | {security.ROOM_ACCESS_HEADER: self.token}).status_code, 403)
        with self.assertRaises(WebSocketDisconnect) as closed:
            with self.client.websocket_connect(self.url(workspace_token=replacement[security.WORKSPACE_ACCESS_HEADER])) as ws:
                ws.receive_json()
        self.assertEqual(closed.exception.code, 4403)

    def test_socket_cannot_write_after_member_rename(self):
        with self.client.websocket_connect(self.url()) as ws:
            self.assertEqual(ws.receive_json()['type'], 'snapshot')
            changed = self.client.patch('/workspaces/me/profile', headers=self.headers, json={'display_name': 'Renamed'})
            self.assertEqual(changed.status_code, 200, changed.text)
            ws.send_json({'type': 'save_entry', 'entry': {'points': {'total': 999}}, 'client_entry_id': 'stale'})
            with self.assertRaises(WebSocketDisconnect) as closed:
                ws.receive_json()
            self.assertEqual(closed.exception.code, 4403)
        with self.fixture.SessionLocal() as db:
            self.assertEqual(db.query(models.ScoutingRoomEntry).count(), 0)

    def test_idle_socket_closes_when_room_or_workspace_token_expires(self):
        class Future(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz or timezone.utc) + timedelta(minutes=2)

        for kind, expected in [('room', 4403), ('workspace', 4401)]:
            with self.subTest(kind=kind):
                room_token, workspace_token = self.token, self.headers[security.WORKSPACE_ACCESS_HEADER]
                payload = (security.parse_room_access_token(room_token) if kind == 'room'
                           else security.parse_workspace_access_token(workspace_token))
                payload['exp'] = int(datetime.now(timezone.utc).timestamp()) + 60
                short = security._signed_payload_token(payload)
                if kind == 'room':
                    room_token = short
                else:
                    workspace_token = short
                with mock.patch.object(rooms, '_ROOM_MEMBERSHIP_RECHECK_SEC', 0.02):
                    with self.client.websocket_connect(self.url(room_token=room_token, workspace_token=workspace_token)) as ws:
                        self.assertEqual(ws.receive_json()['type'], 'snapshot')
                        with mock.patch('jwt.api_jwt.datetime', Future):
                            with self.assertRaises(WebSocketDisconnect) as closed:
                                ws.receive_json()
                        self.assertEqual(closed.exception.code, expected)

    def test_recording_sync_rejects_room_token_from_removed_membership(self):
        _, replacement = self.recycle_name()
        self.client.app.include_router(routes_tracks.router)
        with self.fixture.SessionLocal() as db:
            # Existing fixture match only needs its alliance link for the sync gate.
            db.add(models.MatchTeam(match_key=f'{EVENT}_qm1', team_key='frc118', event_key=EVENT, alliance='red', station='r1'))
            db.commit()
        response = self.client.post('/tracks/on-device-session', headers=replacement | {
            security.ROOM_ACCESS_HEADER: self.token,
        }, json=_session_body('recycled-recording'))
        self.assertEqual(response.status_code, 403, response.text)
