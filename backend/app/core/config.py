"""Backend settings, read from environment variables or backend/.env."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/smartcar"
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "smartcar"
    seed_password: str = "change-me"


settings = Settings()
