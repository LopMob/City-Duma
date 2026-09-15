from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import crud
from app.config import get_settings
from app.routers import commissions, deputies, memberships

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")

app.include_router(deputies.router)
app.include_router(commissions.router)
app.include_router(memberships.router)

# NB: роутер заседаний/посещаемости (задача #3) подключается здесь же
# по мере готовности, см. docs/tasks/.

# Лёгкий веб-интерфейс поверх API — см. docs/UI.md.
app.mount("/ui", StaticFiles(directory="static", html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ui/")


@app.get("/health")
def health():
    return {"status": "ok", "environment": settings.environment}


@app.exception_handler(crud.ConflictError)
def conflict_handler(request: Request, exc: crud.ConflictError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": "Некорректные данные запроса", "errors": exc.errors()},
    )

