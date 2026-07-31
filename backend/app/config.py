"""Central app settings, sourced from environment variables / .env.

Anything a user should be able to customize without editing code belongs
here, and should eventually be exposed through the Settings GUI (Phase 8)
rather than requiring a container rebuild.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Ollama
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_chat_model: str = "qwen2.5:14b"
    ollama_vision_model: str = "llava:13b"

    # Tavily
    tavily_api_key: str = ""

    # App defaults
    household_size: int = 2
    backend_port: int = 8095

    # Database
    database_url: str = "sqlite:////app/data/chef.db"


settings = Settings()
