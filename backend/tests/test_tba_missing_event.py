from __future__ import annotations

import unittest
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_matches
from app.db import models
from app.db.base import Base
from app.tba import client as tba_client
from app.tba.client import TBAClient, TBAClientError


class _MissingEventTBA:
    def event(self, event_key):
        raise TBAClientError("TBA returned 404", status_code=404)


class _TBADown:
    def event(self, event_key):
        raise TBAClientError("TBA timeout after 3 attempts")


class MissingEventTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_event_tba_does_not_know_is_not_created(self):
        with self.assertRaises(HTTPException) as raised:
            routes_matches._upsert_event_if_missing(self.db, "2026typo", tba=_MissingEventTBA())
        self.assertEqual(raised.exception.status_code, 404)
        self.assertIsNone(self.db.get(models.Event, "2026typo"))

    def test_tba_outage_still_falls_back_to_a_placeholder(self):
        event = routes_matches._upsert_event_if_missing(self.db, "2026txhou", tba=_TBADown())
        self.assertEqual(event.event_key, "2026txhou")

    def test_client_does_not_retry_a_404(self):
        response = mock.Mock(status_code=404)
        session = mock.Mock()
        session.request.return_value = response
        tba = TBAClient()
        tba.s = session
        with mock.patch.object(tba_client, "_cached_tba_payload", return_value=None), mock.patch.object(tba_client.time, "sleep") as sleep:
            with self.assertRaises(TBAClientError) as raised:
                tba._request("GET", "https://www.thebluealliance.com/api/v3/event/2026typo")
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(session.request.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
