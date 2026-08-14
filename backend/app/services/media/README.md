# Media Service

This service manages match video storage and enforces retention policies. Its job is to make sure the system doesn't accumulate video files indefinitely, while also not deleting footage that's still needed for analysis.

## What It Does

Match videos can be large, and storage is finite. This service runs periodic cleanup jobs that look at what videos are stored, apply configurable retention rules, and purge files that no longer need to be kept.

Retention decisions factor in things like video quality level and whether analysis has been completed — a video that hasn't been analyzed yet won't get deleted, even if it's old.

## Files

**`retention.py`**
The main retention engine. Defines and enforces TTL-based retention rules per video type. Runs queries against the database to find videos eligible for deletion, then removes both the files and their database records.

Retention is configurable by:
- Quality level (low quality videos may have shorter retention than high quality)
- Analysis status (unanalyzed videos are protected from deletion)

**`storage_cleanup.py`**
Handles the actual cleanup operations — file system deletions, removing orphaned records, and ensuring database state stays consistent with what's actually on disk.

## How It Runs

1. A scheduled job triggers `run_media_retention()`.
2. The service queries for videos that have passed their retention TTL.
3. Analysis status is checked — videos pending or in-progress analysis are excluded.
4. Eligible videos are purged from storage.
5. Database records are updated to reflect the deletion.

## Dependencies

- File system access — for the actual video files
- Database ORM — for querying and updating media records
- Configuration — retention TTLs and quality level mappings
