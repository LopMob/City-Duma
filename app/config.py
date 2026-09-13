"""Конфигурация приложения через переменные окружения (.env)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки сервиса. Все значения переопределяются переменными окружения."""

    app_name: str = "Городская Дума API"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # По умолчанию — локальный SQLite для быстрого старта (ЛР1).
    database_url: str = "sqlite:///./duma.db"

    environment: str = "development"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
