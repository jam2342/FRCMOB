# Calibration Service

Fuel-rate calibration: keeps per-phase scoring-rate estimates grounded in the data we actually have.

Field calibration (image → field homography) no longer lives on the server. The on-device recorder calibrates with four taps in the browser (`ScoutingApp/src/features/onDevice/`), and `services/auto_scout/on_device.py` is the tested Python mirror of that math.

## Files

**`fuel_rate.py`**
Fits linear models (`scoring_rate = intercept + slope × throughput_score`) for teleop and auto from recent `TeamMatchFinding` and `EventTeamRating` rows, clamps the slopes (teleop 0.2–2.2, auto 0.02–0.9), and caches the result. Stale cached values are served if the database is unreachable. The ops dashboard reports the current fit.
