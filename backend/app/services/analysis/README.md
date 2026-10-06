# Analysis Service

Shared rules for which analysis data counts: which run a consumer reads, and whether a finding's evidence is strong enough to use.

The ten-dimension "elite robot" scoring that used to live here was removed on 2026-10-05: it ran for every team on every ratings recompute and nothing read its output.

## Files

**`evidence.py`**
Decides whether a finding's evidence is strong enough to count; ratings and the quality gate call it. It still quarantines historical broadcast findings.

**`runs.py`**
Run-kind constants (`official_truth`, `on_device`, and `video` for legacy broadcast rows) and the lookups that pick which run a consumer reads — including `best_on_device_run()`, the highest-quality operator-accepted phone recording of a match.

## Dependencies

- `AnalysisRun`, `AnalysisRunContext`, `TeamMatchFinding` ORM models
