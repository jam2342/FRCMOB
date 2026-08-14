# Inspect all component scores for frc118 to find zeroed-out values.
import json
import sys
sys.path.insert(0, ".")

from app.db.session import SessionLocal
from app.db import models

db = SessionLocal()
rows = db.query(models.EventTeamRating).filter(
    models.EventTeamRating.team_key == "frc118"
).all()

for r in rows:
    print(f"\n{'='*60}")
    print(f"EVENT: {r.event_key}  |  RATING: {r.rating_0_100:.1f}  |  CONF: {r.confidence_0_1:.4f}")
    print(f"{'='*60}")
    print(f"  robot_level      = {r.robot_level_0_100}")
    print(f"  driver_skill     = {r.driver_skill_0_100}")
    print(f"  results_anchor   = {r.results_anchor}")
    print(f"  throughput       = {r.throughput}")
    print(f"  shift_prod       = {r.shift_productivity}")
    print(f"  cap_util         = {r.capacity_utilization}")
    print(f"  endgame          = {r.endgame}")
    print(f"  consistency      = {r.consistency}")

    d = r.details_json or {}
    print("\n  --- details_json keys with numeric values ---")
    for k in sorted(d.keys()):
        v = d[k]
        if isinstance(v, (int, float)):
            flag = " <<<< ZERO" if v == 0 or v == 0.0 else ""
            print(f"  {k:45s} = {v}{flag}")
        elif isinstance(v, dict):
            # Check nested dicts for zeros
            zeros = {k2: v2 for k2, v2 in v.items() if isinstance(v2, (int, float)) and (v2 == 0 or v2 == 0.0)}
            if zeros:
                print(f"  {k} -> ZEROS: {zeros}")

    # Dump full details for debugging
    print("\n  --- full details_json ---")
    print(json.dumps(d, indent=2, default=str)[:3000])

db.close()
