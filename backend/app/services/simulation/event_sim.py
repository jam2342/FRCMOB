# Event simulation (docs/RATING_ROADMAP.md, Phase 6): play the rest of an event many
# times from the team model's chances and count how often each team ends up where.
#
# Quals: each remaining match is won with its predicted chance and each bonus RP earned
# with its calibrated chance; Ranking Score = RP / matches played (manual 10.5), ties
# broken by average alliance points (the manual's 2nd sort; the 3rd and 4th are close
# behind it in practice). Alliance selection: the top 8 captains draft serpentine (1-8,
# then 8-1), each taking the strongest team left, other captains included (a picked
# captain's alliance moves up). Playoffs: the 8-alliance double-elimination bracket and
# a best-of-3 final. Pure: inputs are plain numbers, so tests can build events by hand.
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class QualMatch:
    red: tuple[str, ...]
    blue: tuple[str, ...]
    red_win: float  # chance red wins
    red_bonus: dict[str, float] = field(default_factory=dict)  # bonus RP -> chance
    blue_bonus: dict[str, float] = field(default_factory=dict)
    red_points: float = 0.0  # expected alliance points (tiebreak)
    blue_points: float = 0.0


@dataclass
class Standing:
    ranking_points: float = 0.0
    matches: int = 0
    points: float = 0.0  # summed alliance points without fouls (tiebreak)


# Double elimination for 8 alliances (seeds 1-8): (match, (slot, slot)); a slot is a seed
# ("s1") or the winner / loser of an earlier match ("w3" / "l3").
BRACKET = [
    (1, ("s1", "s8")), (2, ("s4", "s5")), (3, ("s2", "s7")), (4, ("s3", "s6")),
    (5, ("l1", "l2")), (6, ("l3", "l4")), (7, ("w1", "w2")), (8, ("w3", "w4")),
    (9, ("l7", "w6")), (10, ("l8", "w5")), (11, ("w7", "w8")),
    (12, ("w10", "w9")), (13, ("l11", "w12")),
]


def select_alliances(order: list[str], strength: dict[str, float]) -> list[list[str]]:
    # Manual 10.6.1, with every captain taking the strongest team left (and accepting):
    # the top 8 ranked are captains; round 1 picks 1-8, round 2 picks 8-1. A captain may
    # pick a lower-ranked captain; that seat empties, the captains below move up, and the
    # next ranked team not yet on an alliance becomes the 8th captain.
    captains = list(order[:8])
    next_up = 8
    seated: set[str] = set()
    alliances: list[list[str]] = []
    by_strength = sorted(order, key=lambda t: -strength.get(t, 0.0))
    for seat in range(8):
        if seat >= len(captains):
            break
        captain = captains[seat]
        seated.add(captain)
        pick = next((t for t in by_strength if t not in seated and t != captain), None)
        if pick is None:
            break
        seated.add(pick)
        alliances.append([captain, pick])
        if pick in captains[seat + 1:]:
            captains.remove(pick)
            while next_up < len(order) and (order[next_up] in seated or order[next_up] in captains):
                next_up += 1
            if next_up < len(order):
                captains.append(order[next_up])
                next_up += 1
    for alliance in reversed(alliances):
        pick = next((t for t in by_strength if t not in seated), None)
        if pick is None:
            break
        seated.add(pick)
        alliance.append(pick)
    return alliances


def simulate(
    teams: list[str],
    standings: dict[str, Standing],
    remaining: list[QualMatch],
    strength: dict[str, float],
    playoff_win: Callable[[tuple[str, ...], tuple[str, ...]], float],
    *,
    runs: int = 2000,
    seed: int = 7,
) -> dict[str, dict[str, float]]:
    rng = random.Random(seed)
    counts: dict[str, dict[str, float]] = {t: defaultdict(float) for t in teams}
    playoff_cache: dict[tuple, float] = {}

    def p_win(a: tuple[str, ...], b: tuple[str, ...]) -> float:
        key = (a, b)
        if key not in playoff_cache:
            playoff_cache[key] = playoff_win(a, b)
        return playoff_cache[key]

    for _ in range(runs):
        table = {t: Standing(s.ranking_points, s.matches, s.points) for t, s in standings.items()}
        for t in teams:
            table.setdefault(t, Standing())
        for m in remaining:
            red_won = rng.random() < m.red_win
            for side, won, bonus, pts in ((m.red, red_won, m.red_bonus, m.red_points), (m.blue, not red_won, m.blue_bonus, m.blue_points)):
                rp = (3.0 if won else 0.0) + sum(1.0 for p in bonus.values() if rng.random() < p)
                for t in side:
                    row = table[t]
                    row.ranking_points += rp
                    row.matches += 1
                    row.points += pts
        order = sorted(
            teams,
            key=lambda t: (
                -(table[t].ranking_points / table[t].matches if table[t].matches else 0.0),
                -(table[t].points / table[t].matches if table[t].matches else 0.0),
                rng.random(),
            ),
        )
        for rank, t in enumerate(order, start=1):
            counts[t]["rank_sum"] += rank
            if rank <= 8:
                counts[t]["top8"] += 1
            counts[t]["rp_sum"] += table[t].ranking_points
        if len(order) < 24:
            continue

        seeds = [tuple(a) for a in select_alliances(order, strength)]
        for a in seeds:
            counts[a[0]]["captain"] += 1
            for t in a:
                counts[t]["alliance"] += 1
        if len(seeds) < 8:
            continue

        slots: dict[str, tuple[str, ...]] = {f"s{i + 1}": seeds[i] for i in range(8)}
        for number, (x, y) in BRACKET:
            a, b = slots[x], slots[y]
            winner, loser = (a, b) if rng.random() < p_win(a, b) else (b, a)
            slots[f"w{number}"], slots[f"l{number}"] = winner, loser
        upper, lower = slots["w11"], slots["w13"]
        wins = {upper: 0, lower: 0}
        while max(wins.values()) < 2:
            wins[upper if rng.random() < p_win(upper, lower) else lower] += 1
        champion = upper if wins[upper] == 2 else lower
        for t in champion:
            counts[t]["win_event"] += 1

    return {
        t: {
            "expected_rank": round(c["rank_sum"] / runs, 2),
            "top8": round(c["top8"] / runs, 4),
            "captain": round(c["captain"] / runs, 4),
            "on_alliance": round(c["alliance"] / runs, 4),
            "win_event": round(c["win_event"] / runs, 4),
            "expected_rp": round(c["rp_sum"] / runs, 2),
        }
        for t, c in counts.items()
    }
