from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Twelve-factor config: every value comes from the environment (prefix STAGG_)."""

    model_config = SettingsConfigDict(env_prefix="STAGG_", env_file=".env", extra="ignore")

    kettle_url: str  # e.g. http://192.168.0.58 — give the kettle a DHCP reservation
    state_file: Path = Path("data/state.json")
    reconcile_interval_seconds: float = 60.0
    http_timeout_seconds: float = 5.0
