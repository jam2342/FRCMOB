# Rating research follow-up

This is the offline comparison and export tool. The opt-in database service, weekly
job, admin controls and prediction log are documented in [RATING_SETTINGS.md](RATING_SETTINGS.md).
The full phase 7 rehearsal remains open. Production continues using its existing model.

From `backend/`, with the cached phase 3 tables and production replay inputs available:

```sh
APP_ENV=development .venv/bin/python scripts/evaluate_rating_phase4.py --step freeze
APP_ENV=development OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 .venv/bin/python scripts/evaluate_rating_phase4.py --step evaluate
APP_ENV=development .venv/bin/python scripts/evaluate_rating_phase4.py --step scores
```

The evaluation needs torch, using the existing production training function. Install it
in the local research environment; it is not a new serving dependency. The cache and
reports stay outside git. Nothing connects to the production database.

`research_rehearsal.PROTOCOL` fixes the champion, stacking, network and late-term
challengers, thresholds, and the 2027 clean-test requirement. All 2023–2026 results
are development evidence. Adding a candidate creates a new protocol hash; it does
not rewrite an earlier protocol or restore blind status to an examined season.

The production network is retrained with the same torch architecture, optimizer,
epoch count and seed as the existing trainer. Its labels exclude all held-out events
and every match played at or after the first held-out match. The candidate logistic
is also frozen at that cutoff, with earlier-season weights. Both rating states still
update from earlier matches during evaluation. This holds out correction training;
it does not undo the production base model's hand tuning on 2026. The weekly research
network challenger uses the existing numpy trainer and is a separate experiment.

Weekly champion exports contain score calibration coefficients, temperatures and
correction weights at full precision, recipe, feature order, playing-time cutoff,
code and table hashes. The filename is the SHA-256 of its contents. Loading checks
the hash, and selecting an earlier version provides offline rollback. Tests reproduce
weekly predictions from these exports. Win probabilities use uncalibrated scores.
The serving system does not yet read these files or automatically refit each Monday.

The rehearsal report blocks promotion unless the candidate meets the held-out network
and late-event checks and the first-match score-bias check. For the network comparison,
positive Brier difference means the candidate is worse; passing requires the upper
90% event-bootstrap bound to be at most zero. The provisional first-match bias guard
is ±3%. Challenger complexity requires a pooled improvement of at least 0.0015,
an interval excluding zero, and no season regression above 0.0005. Passing a development
gate does not supply independent evidence for adoption.

Two first-match remedies are measured: separate make-up coefficients in the first
context, and half of phase 3's score correction for first qualification matches.
The second was proposed after seeing the first fail and is explicitly development
work. Neither is automatically adopted. The original blind pipeline and claim files
are not rerun or overwritten.
