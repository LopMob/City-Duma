from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

settings = get_settings()

# pool_pre_ping: после перезапуска PostgreSQL (или обрыва сети) соединение из
# пула проверяется перед использованием, а не падает на первом запросе.
# connect_timeout (ЛР8): если БД недоступна, /ready и /metrics отвечают через
# 3 секунды, а не висят до таймаута TCP — проблема видна сразу.
engine = create_engine(
    settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
