"""Конфигурация приложения через переменные окружения (.env)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки сервиса. Все значения переопределяются переменными окружения."""

    app_name: str = "Городская Дума API"
    # nosec B104 — сервис обязан быть доступен по сети (см. ЛР2,
    # deploy/duma.service: --host ${APP_HOST}), это не забытый debug-бинд;
    # в проде доступ ограничивается firewall'ом (deploy/firewall_app_server.sh),
    # а не привязкой к 127.0.0.1.
    app_host: str = "0.0.0.0"  # nosec B104
    app_port: int = 8000

    # По умолчанию — локальный SQLite для быстрого старта (ЛР1).
    database_url: str = "sqlite:///./duma.db"

    environment: str = "development"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
