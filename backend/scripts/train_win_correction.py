#!/usr/bin/env python3
# Train the learned win-probability correction (ratings/win_correction.py) for a season
# and write its weights to app/services/ratings/data/win_correction_<season>.json.
#
# Features come from the same season replay production runs (team_model_service), one
# row per played match as known just before it. Reports Brier on the usual splits first
# (fit on the older events, scored on the rest), then trains on every event. Needs torch
# (in the server image). Read-only on the database.
#
#   PYTHONPATH=. python scripts/train_win_correction.py --season 2026 [--out PATH]
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.events.win_probability import red_win_prob
from app.services.ratings.replay import load_season, split_holdout
from app.services.ratings.team_model_service import _compute, _fingerprint
from app.services.ratings.win_correction import CORRECTION_BLEND  # noqa: F401  (documents the module trained for)

EPOCHS, HIDDEN, SEED = 300, 16, 0


def fit(X: np.ndarray, y: np.ndarray, seed: int = SEED):
    import torch

    torch.manual_seed(seed)
    net = torch.nn.Sequential(torch.nn.Linear(X.shape[1], HIDDEN), torch.nn.Tanh(), torch.nn.Linear(HIDDEN, 1))
    opt = torch.optim.Adam(net.parameters(), lr=0.01, weight_decay=1e-3)
    Xt, yt = torch.tensor(X, dtype=torch.float32), torch.tensor(y, dtype=torch.float32)
    for _ in range(EPOCHS):
        opt.zero_grad()
        loss = torch.nn.functional.binary_cross_entropy_with_logits(net(Xt).squeeze(1), yt)
        loss.backward()
        opt.step()
    return net


def predict(net, X: np.ndarray) -> np.ndarray:
    import torch

    return torch.sigmoid(net(torch.tensor(X, dtype=torch.float32)).squeeze(1)).detach().numpy()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    db = SessionLocal()
    db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()
    season = load_season(db, args.season)
    run = _compute(db, args.season, _fingerprint(db, args.season))
    db.close()

    training = {r.card.event_key for r in season.results} - season.holdout_events
    inner_val = split_holdout([r for r in season.results if r.card.event_key in training], 0.3)
    rows = []
    for r in season.results:
        if r.red_score == r.blue_score or r.card.match_key not in run.before_match:
            continue
        prediction, seen, features = run.before_match[r.card.match_key]
        margin = (prediction.red_mean + prediction.red_fouls) - (prediction.blue_mean + prediction.blue_fouls)
        sd = ((prediction.red_var or 0) + (prediction.blue_var or 0) + prediction.red_fouls_var + prediction.blue_fouls_var) ** 0.5
        base = red_win_prob(margin, max(sd, 1e-3), seen, playoff=r.card.comp_level != "qm")
        group = "holdout" if r.card.event_key in season.holdout_events else ("inner_val" if r.card.event_key in inner_val else "fit")
        rows.append((group, features, 1.0 if r.red_score > r.blue_score else 0.0, base))

    def arrays(groups):
        chosen = [row for row in rows if row[0] in groups]
        return np.array([row[1] for row in chosen]), np.array([row[2] for row in chosen]), np.array([row[3] for row in chosen])

    report = {}
    for name, fit_groups, eval_group in (("validation", ("fit",), "inner_val"), ("holdout", ("fit", "inner_val"), "holdout")):
        Xf, yf, _ = arrays(fit_groups)
        Xe, ye, base = arrays((eval_group,))
        report[name] = {
            "n": int(len(ye)),
            "team_model_brier": round(float(np.mean((base - ye) ** 2)), 4),
            "corrected_brier": round(float(np.mean((predict(fit(Xf, yf), Xe) - ye) ** 2)), 4),
        }
    X, y, _ = arrays(("fit", "inner_val", "holdout"))
    net = fit(X, y)
    out = Path(args.out) if args.out else BACKEND_ROOT / "app/services/ratings/data" / f"win_correction_{args.season}.json"
    out.write_text(json.dumps({
        "season": args.season,
        "features": int(X.shape[1]),
        "hidden": HIDDEN,
        "trained_on_matches": int(len(y)),
        "report": report,
        "state": {k: v.detach().numpy().round(6).tolist() for k, v in net.state_dict().items()},
    }))
    print(json.dumps({"report": report, "written": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
