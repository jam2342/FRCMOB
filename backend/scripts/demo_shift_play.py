#!/usr/bin/env python3
# Reference demonstration of the shift-based offense/defense engine on synthetic
# tracks (no DB / no video needed). Shows that the engine reads attack vs defense
# out of where a robot is during each shift, and renders the two heat maps.
#
#   PYTHONPATH=. python scripts/demo_shift_play.py
from __future__ import annotations

# ruff: noqa: E402

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.auto_scout.shift_play import TrackPoint, analyze_robot_shift_play
from app.services.game_config import load_game_config

ZONE_XY = {
    "red_alliance_scoring_zone": (14.0, 4.0),
    "red_loading_depot_zone": (15.5, 2.0),
    "blue_alliance_scoring_zone": (2.0, 4.0),
    "neutral_transition_zone": (8.0, 4.0),
}


def series(specs, step=0.5):
    points = []
    for start, end, zone, speed in specs:
        x, y = ZONE_XY[zone]
        t = start
        while t < end:
            points.append(TrackPoint(round(t, 2), x, y, zone, speed))
            t += step
    return points


def attacker():
    specs = []
    for ws, we in [(0, 20), (20, 30), (30, 55), (80, 105), (130, 160)]:
        t = ws
        while t < we:
            specs.append((t, min(t + 4, we), "red_alliance_scoring_zone", 1.1))
            specs.append((min(t + 4, we), min(t + 5, we), "red_loading_depot_zone", 1.1))
            t += 5
    specs += [(55, 80, "neutral_transition_zone", 0.2), (105, 130, "neutral_transition_zone", 0.2)]
    return series(specs)


def defender():
    return series([
        (0, 55, "neutral_transition_zone", 0.2),
        (55, 80, "blue_alliance_scoring_zone", 0.9),
        (80, 105, "neutral_transition_zone", 0.2),
        (105, 130, "blue_alliance_scoring_zone", 0.9),
        (130, 160, "neutral_transition_zone", 0.2),
    ])


def render_heatmap(grid) -> str:
    shades = " .:-=+*#%@"
    peak = max((max(row) for row in grid), default=0) or 1
    lines = []
    for row in grid:
        lines.append("    " + "".join(shades[min(len(shades) - 1, int(v / peak * (len(shades) - 1)))] for v in row))
    return "\n".join(lines)


def main():
    schedule = load_game_config().shift_schedule
    blue = series([(55, 80, "blue_alliance_scoring_zone", 0.8), (105, 130, "blue_alliance_scoring_zone", 0.8)])
    points = {"frc_attacker": attacker(), "frc_defender": defender(), "frc_blue": blue}
    alliances = {"frc_attacker": "red", "frc_defender": "red", "frc_blue": "blue"}

    for team in ("frc_attacker", "frc_defender"):
        r = analyze_robot_shift_play(
            team_key=team, alliance="red", points_by_team=points,
            alliance_by_team=alliances, schedule=schedule,
        )
        print("=" * 64)
        print(f"{team}")
        print(f"  OFFENSE level {r.offense_level_1_5}/5 (conf {r.offense_confidence})  "
              f"DEFENSE level {r.defense_level_1_5}/5 (conf {r.defense_confidence}, assessable={r.defense_assessable})")
        print(f"  metrics: {r.metrics}")
        print("  ATTACK heat map (own active shifts):")
        print(render_heatmap(r.attack_heatmap))
        print("  DEFENSE heat map (opponent active shifts):")
        print(render_heatmap(r.defense_heatmap))


if __name__ == "__main__":
    main()
