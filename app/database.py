"""Engine/session config only (see models.py for tables, repositories.py
for queries). PostgreSQL only — DATABASE_URL must be set explicitly,
no fallback, fails fast at startup if missing/invalid."""
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    sys.exit(
        "DATABASE_URL is not set. This app requires PostgreSQL — e.g.\n"
        '  export DATABASE_URL="postgresql://bookingapp:bookingpass@localhost:5432/bookings"\n'
        "See README.md > Local Postgres setup for the full walkthrough."
    )

if not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")):
    sys.exit(
        f"DATABASE_URL must be a PostgreSQL connection string "
        f"(postgresql://...); got: {DATABASE_URL!r}"
    )

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db():
    from . import models  # noqa: F401  (ensures Booking is registered on Base before create_all)
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
