"""Infra bootstrap settings only -- values that must exist before the
database is even reachable (so they can't live in the DB themselves).

Everything a user should be able to customize at runtime -- Ollama
config, Tavily key, household size, dietary preferences, system prompts
-- lives in the database instead (see app/services/settings_service.py,
app/models/household.py, app/models/settings.py) so it's editable from
the Settings UI (Phase 8) without a container rebuild or restart. This
mirrors the author's preferred pattern from a sibling project: `.env` is
for bootstrap plumbing, the DB is the source of truth for everything else.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The ports the one container actually binds. Declared here, and
    # nowhere else, because two things need them and they must agree:
    # run_server.py binds them, and tls_service.status() reports them to
    # Settings > Security > Certificate so the household can be told the
    # exact HTTPS address to trust. When those two were separate env
    # reads they drifted -- the reported ports outlived the backend
    # service they were named after and pointed at nothing.
    app_port: int = 5173
    app_https_port: int = 5174
    database_url: str = "sqlite:////app/data/chef.db"


settings = Settings()
