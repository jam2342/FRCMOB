# Quick script to inspect current ratings state.
from sqlalchemy import create_engine, text
from app.core.config import settings

e = create_engine(settings.database_url)
with e.connect() as c:
    # Get column info
    cols = c.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='event_team_ratings' ORDER BY ordinal_position"
    )).fetchall()
    print("columns:", [r[0] for r in cols])

    # frc118 ratings
    rows = c.execute(text(
        "SELECT * FROM event_team_ratings "
        "WHERE team_key LIKE '%%118%%' "
        "ORDER BY updated_at DESC LIMIT 5"
    )).fetchall()
    print("\n=== frc118 ratings ===")
    for r in rows:
        print(dict(r._mapping))

    # Total count
    cnt = c.execute(text("SELECT count(*) FROM event_team_ratings")).scalar()
    print(f"\nTotal ratings: {cnt}")

    # Top 15
    rows2 = c.execute(text(
        "SELECT team_key, event_key, rating_0_100, confidence_0_1 "
        "FROM event_team_ratings ORDER BY rating_0_100 DESC LIMIT 15"
    )).fetchall()
    print("\n=== Top 15 ratings ===")
    for r in rows2:
        print(dict(r._mapping))

    # Bottom 15
    rows3 = c.execute(text(
        "SELECT team_key, event_key, rating_0_100, confidence_0_1 "
        "FROM event_team_ratings ORDER BY rating_0_100 ASC LIMIT 15"
    )).fetchall()
    print("\n=== Bottom 15 ratings ===")
    for r in rows3:
        print(dict(r._mapping))

    # Analysis data available
    findings_cnt = c.execute(text("SELECT count(*) FROM team_match_findings")).scalar()
    runs_cnt = c.execute(text("SELECT count(*) FROM analysis_runs")).scalar()
    tracks_cnt = c.execute(text("SELECT count(*) FROM robot_tracks")).scalar()
    print(f"\nAnalysis: {runs_cnt} runs, {findings_cnt} findings, {tracks_cnt} tracks")

    # Findings per team for frc118
    f118 = c.execute(text(
        "SELECT team_key, count(*) as cnt FROM team_match_findings "
        "WHERE team_key LIKE '%%118%%' GROUP BY team_key"
    )).fetchall()
    print(f"\nfrc118 findings: {[dict(r._mapping) for r in f118]}")
