from __future__ import annotations

# ruff: noqa: E402

import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import settings


def main() -> int:
    try:
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            value = conn.execute(text("SELECT 1")).scalar()
        print("db_connection_ok", {"dialect": engine.dialect.name, "probe_result": value})
        return 0
    except SQLAlchemyError as exc:
        print("db_connection_failed", str(exc))
        return 1
    except Exception as exc:  # pragma: no cover
        print("db_connection_failed", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
