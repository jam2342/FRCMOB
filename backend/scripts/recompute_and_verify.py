# Recompute ratings for all events and show frc118 results.
import sys
sys.path.insert(0, ".")

from app.db.session import SessionLocal
from app.db import models
from app.services.ratings.model import recompute_event_ratings

db = SessionLocal()

# Get all event keys
events = [r[0] for r in db.query(models.EventTeamRating.event_key).distinct().all()]
print(f"Recomputing {len(events)} events...")
for ek in events:
    result = recompute_event_ratings(db, ek)
    print(f"  {ek}: ok={result.get('ok')}")

# Show frc118 results
print("\n=== frc118 ratings after fix ===")
rows = db.query(models.EventTeamRating).filter(
    models.EventTeamRating.team_key == "frc118"
).all()
for r in rows:
    d = r.details_json or {}
    comps = d.get("model_components", {})
    subs = d.get("subscores", {})
    print(f"\n{r.event_key}: rating={r.rating_0_100:.1f}, conf={r.confidence_0_1:.4f}")
    print(f"  base_rating={d.get('base_rating'):.2f}, raw_final={d.get('raw_final_rating_0_100'):.2f}")
    print(f"  auto_contribution={comps.get('auto_contribution', '?')}")
    print(f"  rp_contribution={comps.get('rp_contribution', '?')}")
    print(f"  manual_points={comps.get('manual_points_impact', '?')}")
    print(f"  endgame={subs.get('endgame', '?')}")
    print(f"  robot_level={r.robot_level_0_100}")
    print(f"  driver_skill={r.driver_skill_0_100}")

db.close()
