from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
)

# Enforce BEGIN IMMEDIATE for all SQLite transactions.
# This acquires a write lock at the start of every transaction,
# preventing race conditions between concurrent writers (e.g. resolve vs bulk_add).
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "begin")
    def set_sqlite_immediate(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
