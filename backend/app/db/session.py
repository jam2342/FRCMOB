import logging
import time

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

session_logger = logging.getLogger("app.db.session")


def _normalized_database_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if value.startswith("postgresql://"):
        # Force SQLAlchemy psycopg3 dialect for predictable connect args/support.
        return f"postgresql+psycopg://{value[len('postgresql://'):]}"
    return value


DATABASE_URL = _normalized_database_url(str(settings.db.url or ""))
IS_NEON_POOLER = ("neon.tech" in DATABASE_URL) and ("-pooler." in DATABASE_URL)

pool_size = max(1, settings.db.pool_size)
max_overflow = max(0, settings.db.max_overflow)
pool_recycle = max(30, settings.db.pool_recycle_sec)
pool_timeout = max(3, settings.db.pool_timeout_sec)

connect_args: dict[str, object] = {}
if IS_NEON_POOLER:
    # Neon transaction poolers are sensitive to prepared statements and large pools.
    pool_size = min(pool_size, 5)
    max_overflow = min(max_overflow, 5)
    connect_args["prepare_threshold"] = 0
    connect_args["connect_timeout"] = 10
    session_logger.info(
        "Using Neon pooler-safe DB settings pool_size=%s max_overflow=%s",
        pool_size,
        max_overflow,
    )

engine_kwargs = {
    "pool_pre_ping": True,
    "pool_size": pool_size,
    "max_overflow": max_overflow,
    "pool_recycle": pool_recycle,
    "pool_timeout": pool_timeout,
}
if connect_args:
    engine_kwargs["connect_args"] = connect_args

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
sql_logger = logging.getLogger("app.sql")


if bool(settings.db_slow_query_logging_enabled):
    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, _executemany):
        context._query_start_time = time.perf_counter()


    @event.listens_for(engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, _executemany):
        started = getattr(context, "_query_start_time", None)
        if started is None:
            return
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        slow_threshold_ms = max(
            1.0,
            float(settings.db_slow_query_threshold_ms or 80.0),
        )
        if elapsed_ms < slow_threshold_ms:
            return
        max_chars = max(
            80,
            int(settings.db_slow_query_max_sql_chars or 220),
        )
        normalized_sql = " ".join(str(statement or "").split())
        if len(normalized_sql) > max_chars:
            normalized_sql = f"{normalized_sql[: max_chars - 3]}..."
        rowcount = getattr(cursor, "rowcount", None)
        sql_logger.warning(
            "sql.slow duration_ms=%.3f rowcount=%s statement=%s",
            elapsed_ms,
            rowcount,
            normalized_sql,
        )


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
