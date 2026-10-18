#!/usr/bin/env python3
# What's still missing for a season, as a checklist (docs/SEASON_TRANSITION.md).
#
#   PYTHONPATH=. python scripts/season_readiness.py --season 2027        code + config
#   PYTHONPATH=. python scripts/season_readiness.py --season 2027 --db   also the database
#
# "needed" items must exist before the season's events are ingested; "after events"
# items can only be built from that season's matches. Exits 1 while anything needed
# is missing, so it can gate a deploy. Read-only.
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def frontend_season_facts() -> tuple[int | None, set[int]]:
    path = REPO / "ScoutingApp" / "src" / "config" / "season.ts"
    if not path.exists():
        return None, set()
    text = path.read_text(encoding="utf-8")
    year = re.search(r"^\s*year:\s*(\d{4})", text, re.M)
    games_block = re.search(r"SEASON_GAMES[^=]*=\s*\{(.*?)\}", text, re.S)
    games = {int(y) for y in re.findall(r"(\d{4})\s*:", games_block.group(1))} if games_block else set()
    return (int(year.group(1)) if year else None), games


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--db", action="store_true", help="also check database rows (prior strengths, results, frozen seasons)")
    args = parser.parse_args()
    season = args.season
    rows: list[tuple[str, bool, str, str]] = []  # (status kind, ok, item, how)

    def need(ok: bool, item: str, how: str) -> None:
        rows.append(("needed", ok, item, how))

    def later(ok: bool, item: str, how: str) -> None:
        rows.append(("after events", ok, item, how))

    from app.core.config import settings
    from app.services.auto_scout import specs
    from app.services.game_config import load_game_config
    from app.services.ratings.constants import SEASON_BASELINES
    from app.services.ratings.ranking_points import BONUS_RP_CALIBRATION, BONUS_RPS
    from app.services.scoring.alliance_results import COMPONENT_PARSERS
    from app.services.scoring.official_team_stats import COPR_METRICS_BY_SEASON
    from app.services.season_config import UnsupportedSeasonRulesError, require_known_season_rules
    from app.services.seasons import SEASON_GAMES

    config_year = int(load_game_config().season_year)
    need(config_year == season, f"game config is the {season} game (now {config_year})", "season_template.json: season_year, zones, phases, scoring, shift_schedule")
    try:
        require_known_season_rules(season, feature="readiness")
        rules_ok = True
    except UnsupportedSeasonRulesError:
        rules_ok = False
    need(rules_ok, "scoring rules adapter accepted (require_known_season_rules)", "season_config.py: allow the season once breakdown.py/truth.py parse its TBA payload")
    need(season in COMPONENT_PARSERS, "TBA breakdown → alliance components parser", "scoring/alliance_results.py COMPONENT_PARSERS, then scripts/backfill_alliance_results.py")
    need(season in COPR_METRICS_BY_SEASON, "component OPR names for Team Center official stats", "scoring/official_team_stats.py COPR_METRICS_BY_SEASON")
    need(season in BONUS_RPS, "bonus ranking points (which part, threshold, key)", "ratings/ranking_points.py BONUS_RPS")
    need(season in SEASON_BASELINES, "rating baseline row (provisional from week 1)", "ratings/constants.py SEASON_BASELINES; evaluate_rating_replay.py prints it")
    need(season in SEASON_GAMES, "game name for labels", "services/seasons.py SEASON_GAMES")
    by_season = {name: getattr(specs, name) for name in dir(specs) if name.endswith("_BY_SEASON") and isinstance(getattr(specs, name), dict)}
    missing_specs = sorted(name for name, table in by_season.items() if season not in table)
    need(not missing_specs, f"auto-scout tables ({len(by_season)})" + (f": missing {', '.join(missing_specs)}" if missing_specs else ""), "auto_scout/specs.py *_BY_SEASON + mapper_version_for_season")

    fe_year, fe_games = frontend_season_facts()
    need(fe_year == season, f"frontend season.ts year (now {fe_year})", "ScoutingApp/src/config/season.ts (season.test.ts checks it against the game config)")
    need(season in fe_games, "frontend game name", "season.ts SEASON_GAMES")
    pinned = int(getattr(settings, "automation_regional_halfday_season", 0) or 0)
    need(pinned in (0, season), f"regional automation follows the season (AUTOMATION_REGIONAL_HALFDAY_SEASON={pinned or 'unset'})", "VM .env: bump only after the rules above land, then recreate the containers")

    later(season in BONUS_RP_CALIBRATION, "bonus RP calibration", "evaluate_rating_replay.py \"bonus_rp\" → BONUS_RP_CALIBRATION")
    weights = REPO / "backend" / "app" / "services" / "ratings" / "data" / f"win_correction_{season}.json"
    later(weights.exists(), "learned win correction weights", f"scripts/train_win_correction.py --season {season}")

    if args.db:
        from sqlalchemy import func, select

        from app.db import models
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            priors = db.execute(select(func.count()).select_from(models.TeamPriorStrength).where(models.TeamPriorStrength.season == season - 1)).scalar() or 0
            need(priors > 0, f"last season's strengths as priors ({priors} teams for {season - 1})", f"scripts/build_prior_strengths.py --season {season - 1} --source db")
            frozen = db.get(models.FrozenSeason, season - 1) is not None
            need(frozen, f"{season - 1} frozen (read-only, archived predictions)", f"scripts/freeze_season.py --season {season - 1} (after its last refresh)")
            results = db.execute(
                select(func.count()).select_from(models.MatchAllianceResult).where(models.MatchAllianceResult.match_key.like(f"{season}%"))
            ).scalar() or 0
            later(results > 0, f"{season} alliance results ingested ({results})", f"scripts/backfill_alliance_results.py --season {season}")

    blocking = 0
    for kind in ("needed", "after events"):
        print(f"\n{kind.upper()}")
        for row_kind, ok, item, how in rows:
            if row_kind != kind:
                continue
            if not ok and kind == "needed":
                blocking += 1
            print(f"  {'ok' if ok else 'MISSING' if kind == 'needed' else 'later':8} {item}")
            if not ok:
                print(f"           → {how}")
    print(f"\n{season}: {'ready' if not blocking else f'{blocking} needed item(s) missing'} (docs/SEASON_TRANSITION.md)")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
