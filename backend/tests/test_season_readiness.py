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
    assert "MISSING" in out


def test_a_state_season_needs_the_recipe_not_a_baseline_and_parsers_come_later(capsys):
    # 2027 on: the self-tuning recipe rates the season, so a hand-entered baseline is the thing to
    # avoid, and the game's breakdown parsers are built from its first real payloads.
    assert _run(2027) == 1
    needed, after = capsys.readouterr().out.split("AFTER EVENTS")
    assert "ok       no hand-entered rating baseline" in needed
    assert "MISSING  Tier A component declaration" in needed and "MISSING  rating switches on" in needed
    assert "TBA breakdown → alliance components parser" in after and "parser" not in needed
    assert "learned win correction weights" not in needed + after
