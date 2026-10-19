from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from sqlalchemy.orm import Session

from app.db import models


def write_match_lineups(db: Session, event_key: str, links: Iterable[tuple[str, str, str, str]]) -> int:
    # links: (match_key, team_key, alliance, station). A match's lineup replaces its
    # previous one: backup robots and substitutions change lineups mid-event, and merging
    # alone left the replaced robot on the alliance (58 alliances at 5 events had 4-6
    # robots on 2026-10-18). A lineup missing an alliance (unplayed playoffs) only adds.
    by_match: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for link in links:
        by_match[link[0]].append(link)
    for match_key, rows in by_match.items():
        if {alliance for _, _, alliance, _ in rows} >= {"red", "blue"}:
            keep = {team_key for _, team_key, _, _ in rows}
            (
                db.query(models.MatchTeam)
                .filter(models.MatchTeam.match_key == match_key, models.MatchTeam.team_key.notin_(keep))
                .delete(synchronize_session=False)
            )
        for _, team_key, alliance, station in rows:
            db.merge(
                models.MatchTeam(
                    match_key=match_key,
                    team_key=team_key,
                    event_key=event_key,
                    alliance=alliance,
                    station=station,
                )
            )
    return sum(len(rows) for rows in by_match.values())
