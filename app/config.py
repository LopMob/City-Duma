"""Конфигурация приложения через переменные окружения (.env)."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки сервиса. Все значения переопределяются переменными окружения."""

    app_name: str = "Городская Дума API"
    # Осознанное исключение bandit B104: сервис обязан быть доступен по сети
    # (см. deploy/duma.service: --host ${APP_HOST}), это не забытый debug-бинд;
    # в проде доступ ограничивается firewall'ом (deploy/firewall_app_server.sh),
    # а не привязкой к 127.0.0.1.
    app_host: str = "0.0.0.0"  # nosec B104
    app_port: int = 8000

    # Единственная поддерживаемая СУБД — PostgreSQL. Строка подключения
    # обычно приходит из .env / окружения (в проде — /etc/city-duma/app.env).
    database_url: str = (
        "postgresql+psycopg2://postgres:postgres@localhost:5432/city_duma"
    )

    environment: str = "development"

    @field_validator("database_url")
    @classmethod
    def _only_postgresql(cls, value: str) -> str:
        if not value.startswith(("postgresql://", "postgresql+")):
            raise ValueError(
                "DATABASE_URL: поддерживается только PostgreSQL "
                "(postgresql+psycopg2://user:password@host:5432/dbname). "
                "Проверьте переменную окружения или файл .env."
            )
        return value

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
