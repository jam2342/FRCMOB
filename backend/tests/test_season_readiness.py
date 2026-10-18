# The season readiness report (scripts/season_readiness.py) reads the same season tables
# the app does: the current season must come out ready, an unknown one must not.
from __future__ import annotations

import runpy
import sys
from pathlib import Path
from unittest import mock

import pytest

from app.services.season_config import CURRENT_SEASON_YEAR

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "season_readiness.py"


def _run(season: int) -> int:
    with mock.patch.object(sys, "argv", ["season_readiness.py", "--season", str(season)]):
        with pytest.raises(SystemExit) as stopped:
            runpy.run_path(str(SCRIPT), run_name="__main__")
    return int(stopped.value.code or 0)


def test_current_season_is_ready(capsys):
    with mock.patch("app.core.config.settings.automation_regional_halfday_season", 0):
        assert _run(CURRENT_SEASON_YEAR) == 0
    assert "ready" in capsys.readouterr().out


def test_next_season_lists_what_is_missing(capsys):
    assert _run(CURRENT_SEASON_YEAR + 1) == 1
    out = capsys.readouterr().out
    assert "MISSING" in out and "SEASON_BASELINES" in out
