# ML Service

This service handles machine learning model training, evaluation, and inference for team-level predictions. It covers two main areas: role signal classification (what role does a robot play on the field?) and alliance synergy scoring (how well do teams complement each other?).

## What It Does

Not all robots play the same role. Some are primary scorers, some run defense, some focus on feeding game pieces, and some specialize in endgame. This service builds models that classify team roles based on scouting data and outputs role signal scores. It also scores how well sets of teams work together as an alliance — useful for picklist decisions.

Shadow deployment is supported, meaning a new model can run alongside the production model in read-only mode to evaluate its predictions before it goes live.

## Files

**`role_ml.py`**
The primary role classification model. Builds a 20-feature vector from `EventTeamRating` data — covering rating, confidence, throughput, driver skill, robot level, endgame, consistency, and others — and predicts role membership scores (0–100) across four roles:

- **Scorer** — high throughput, low cycle times
- **Defender** — high defensive engagement, lower scoring
- **Feeder** — moderate scoring with strong efficiency
- **Endgame Specialist** — high climb success rates

The feature vector is deterministic and versioned, so models trained at different points remain comparable.

**`synergy.py`**
Computes alliance synergy by evaluating how well a set of teams covers different strategic roles. A balanced alliance — one scorer, one support, one endgame specialist — scores higher than three robots with overlapping strengths.

**`synergy_ml.py`**
The ML backbone for synergy scoring. Uses pair-wise team compatibility vectors to estimate how well any two-team combination performs relative to historical data.

**`model_eval.py`**
Evaluation utilities — precision, recall, feature importance analysis, and calibration metrics. Used to validate a model before promoting it to production.

**`shadow.py`**
Shadow deployment management. Allows a candidate model to run alongside production, logging its predictions without affecting outputs. Used to verify model quality before switching.

## How It Runs

**Role Classification:**
1. Load `EventTeamRating` records for the target event.
2. Build a 20-feature vector per team using the standard feature order.
3. Run the classifier to get role signal scores (0–100) for each of the four roles.
4. Store results in `MLFeatureSnapshot` for later analysis.

**Synergy Scoring:**
1. Accept a list of teams (typically 3 for a full alliance).
2. Build pair-wise compatibility vectors for each team combination.
3. Compute a synergy score based on role coverage and historical match outcomes.
4. Return ranked recommendations for picklist use.

**Shadow Deployment:**
1. A candidate model is registered in shadow mode.
2. On each inference call, both the production and shadow models run.
3. Shadow predictions are logged but not returned to the caller.
4. After sufficient coverage, model_eval.py compares the two models.
5. Shadow model is promoted to production if metrics improve.

## Dependencies

- `EventTeamRating` — primary data source for feature vectors
- `MLFeatureSnapshot` — output storage for feature vectors and predictions
- SQLAlchemy ORM — database access
