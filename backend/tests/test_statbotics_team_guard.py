import asyncio
from unittest import mock

import pytest

from app.services.clients import statbotics


@pytest.mark.parametrize("call", [
    lambda: statbotics.get_team(0),
    lambda: statbotics.get_team_event(0, "2026azrl4"),
    lambda: statbotics.get_team_year(0, 2026),
])
def test_team_zero_never_reaches_statbotics(call):
    # frc1234b-style off-season keys parse to 0 upstream.
    with mock.patch.object(statbotics, "_fetch_json") as fetch:
        with pytest.raises(RuntimeError):
            asyncio.run(call())
    fetch.assert_not_called()
