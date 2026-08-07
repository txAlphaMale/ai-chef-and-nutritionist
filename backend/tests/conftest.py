"""Shared pytest fixtures for backend tests.

Sets DATABASE_URL to a throwaway temp-file SQLite database BEFORE any
`app.*` module is imported, so importing app.config/app.database never
touches the real /app/data/chef.db path this repo's Docker volume uses.
This has to happen at module import time (not inside a fixture), since
pydantic-settings reads the environment once at `app.config.settings`
construction, which happens as a side effect of the first `import app...`
anywhere in the test session.

A real temp file is used rather than `sqlite:///:memory:` because
`:memory:` is connection-scoped -- it would create a fresh, empty database
for every new connection SQLAlchemy's pool opens, which doesn't match how
`app.database` is wired (a shared `engine`/`SessionLocal` at module scope).
"""

import os
import tempfile

_tmp_db_fd, _tmp_db_path = tempfile.mkstemp(prefix="chef-test-", suffix=".db")
os.close(_tmp_db_fd)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_db_path}")

# secrets_crypto.py defaults to /app/data/... (the real container's volume
# path), which doesn't exist -- and shouldn't be written to -- outside a
# real deployment. Redirect both to a throwaway temp dir for tests.
_tmp_secrets_dir = tempfile.mkdtemp(prefix="chef-test-secrets-")
os.environ.setdefault("SECRETS_KEY_FILE", os.path.join(_tmp_secrets_dir, "secrets.key"))
os.environ.setdefault("SECRETS_KEYRING_FILE", os.path.join(_tmp_secrets_dir, "secrets_keyring.json"))

# Backlog B15.1 (2026-08-01) -- main.py's session-cookie secret and
# tls_service's TLS_DIR both default to real container paths (/app/data,
# /app/tls) for the same reason as SECRETS_KEY_FILE above. Redirected
# here (not per-test) since both are read/computed once at import time
# (main.py's SessionMiddleware setup call, tls_service's module-level
# TLS_DIR constant) -- this is what makes a plain `import app.main`
# possible at all outside a real container.
os.environ.setdefault("SESSION_SECRET_FILE", os.path.join(_tmp_secrets_dir, "session_secret.key"))
os.environ.setdefault("TLS_DIR", tempfile.mkdtemp(prefix="chef-test-tls-"))

# B7.5's built-in sound library writes WAV files next to the database, and
# `seed()` calls `seed_builtin_sounds()` on every run -- so from the moment
# that shipped, every test that seeds tried to create `/app/data/sounds`
# and got PermissionError outside the container. Nine tests, all in
# test_seed_system_prompts.py, and the failure had nothing to do with what
# they assert. Same pattern as the four above: point it somewhere
# disposable before app code reads the environment.
os.environ.setdefault("SOUNDS_DIR", tempfile.mkdtemp(prefix="chef-test-sounds-"))

import pytest  # noqa: E402

import app.models  # noqa: E402  -- import side effect: registers every model on Base.metadata
from app.database import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture()
def db_session():
    """A fresh set of tables per test, backed by the shared temp-file
    SQLite database. Fine at this project's test volume/velocity; revisit
    with per-test isolated databases if tests start interfering."""
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _reset_auth_enabled_cache():
    """`auth_service` caches the "is the password gate on" flag in module
    state so the per-request middleware doesn't hit the database on every
    single API call. That cache is process-global, and each test gets its
    own throwaway database -- so without this, one test enabling the gate
    would leak a stale `True` into every test that ran after it."""
    from app.services import auth_service

    auth_service.invalidate_enabled_cache()
    yield
    auth_service.invalidate_enabled_cache()
