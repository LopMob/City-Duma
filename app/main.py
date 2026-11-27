from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import crud, models
from app.config import get_settings
from app.database import get_db
from app.observability import instruments, logs
from app.observability.metrics import CONTENT_TYPE
from app.observability.middleware import ObservabilityMiddleware
from app.routers import commissions, deputies, meetings, memberships

settings = get_settings()

# ЛР8: все журналы — JSON на stdout, без секретов (docs/OBSERVABILITY.md).
logs.setup_logging(settings.log_level)
log = logs.get_logger()
instruments.set_app_info(
    settings.app_version, settings.git_commit, settings.environment
)

DbSession = Annotated[Session, Depends(get_db)]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.info(
        "приложение запущено",
        extra={
            "event": "app_started",
            "version": settings.app_version,
            "commit": settings.git_commit,
            "environment": settings.environment,
        },
    )
    yield
    log.info("приложение останавливается", extra={"event": "app_stopping"})


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.add_middleware(ObservabilityMiddleware)

app.include_router(deputies.router)
app.include_router(commissions.router)
app.include_router(memberships.router)
app.include_router(meetings.router)

# Лёгкий веб-интерфейс поверх API — см. docs/UI.md.
app.mount("/ui", StaticFiles(directory="static", html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ui/")


@app.get("/health")
def health():
    """Жив ли процесс (liveness): по нему Docker решает, здоров ли контейнер."""
    return {"status": "ok", "environment": settings.environment}


def database_available(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        instruments.DB_UP.set(0)
        log.warning(
            "база данных недоступна",
            extra={"event": "db_unavailable", "error": type(exc).__name__},
        )
        return False
    instruments.DB_UP.set(1)
    return True


@app.get("/version")
def version():
    """Какая версия запущена: тег/короткий SHA и полный SHA коммита (ЛР6)."""
    return {
        "version": settings.app_version,
        "commit": settings.git_commit,
        "environment": settings.environment,
    }


@app.get("/metrics", include_in_schema=False)
def metrics(db: DbSession):
    """Метрики в формате Prometheus (ЛР8). Список — docs/OBSERVABILITY.md."""
    if database_available(db):
        try:
            rows = db.execute(
                select(models.Meeting.status, func.count()).group_by(
                    models.Meeting.status
                )
            ).all()
        except SQLAlchemyError:
            instruments.DB_UP.set(0)
        else:
            counts = {status.value: 0 for status in models.MeetingStatus}
            counts.update({status.value: count for status, count in rows})
            for status, count in counts.items():
                instruments.MEETINGS.set(count, status=status)
    return Response(instruments.REGISTRY.render(), media_type=CONTENT_TYPE)


@app.exception_handler(crud.ConflictError)
def conflict_handler(request: Request, exc: crud.ConflictError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": "Некорректные данные запроса", "errors": exc.errors()},
    )
