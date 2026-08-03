"""SQLAlchemy engine/session setup.

SQLite is the datastore (see PROJECT-PLAN.md for why). The file lives on a
mounted volume so it survives container rebuilds. Only this backend process
should ever write to it directly -- avoid the spreadsheet-as-database
file-locking problem hit in the earlier prototype.

Four pragmas are applied to every connection. None of them are optional
for how this app actually runs:

- **foreign_keys=ON.** SQLite ignores foreign key constraints unless they
  are enabled per connection, and SQLAlchemy does not enable them. Without
  this, every FK in the schema is decorative -- deleting a recipe can leave
  orphaned recipe_ingredients, meal_plan_entries and knowledge_chunks
  behind wherever the ORM's own cascade doesn't happen to cover the path.

- **journal_mode=WAL.** Three things write to this database concurrently:
  the asyncio event loop, FastAPI's threadpool for plain `def` routes, and
  the background job worker. The default rollback journal makes readers and
  writers block each other; WAL lets readers proceed during a write, which
  is exactly the access pattern here (a job writing while the UI polls).

- **busy_timeout.** pysqlite's default is 5 seconds, after which a
  contended write raises "database is locked" as a 500. A background job
  committing a large batch import can plausibly exceed that. 15 seconds
  turns a spurious error into a short wait.

- **synchronous=NORMAL.** The recommended durability level under WAL: a
  host crash can lose the most recent transactions but the database stays
  consistent. FULL costs an fsync per commit, which is real overhead on
  this deployment's hardware for no benefit a household app needs.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

_IS_SQLITE = settings.database_url.startswith("sqlite")

# check_same_thread=False is required because connections are handed
# between the event loop, the threadpool and the job worker.
connect_args = {"check_same_thread": False} if _IS_SQLITE else {}

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    # Cheap liveness check before handing out a pooled connection --
    # avoids a stale connection surfacing as a request failure.
    pool_pre_ping=True,
)

BUSY_TIMEOUT_MS = 15000


if _IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
