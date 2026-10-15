# ЛР4: образ приложения «Городская Дума».
#
# Воспроизводимость: базовый образ закреплён точной версией (не latest),
# зависимости ставятся из requirements-runtime.txt с точными версиями (==).
# Безопасность: процесс работает от непривилегированного пользователя,
# в образе нет секретов — настройки приходят переменными окружения при запуске.
FROM python:3.11.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Системный пользователь без пароля, без домашней папки и без оболочки.
# Числовой UID закреплён, чтобы проверка «не root» не зависела от имени.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home \
        --shell /usr/sbin/nologin app

WORKDIR /app

# Сначала зависимости: слой кешируется и пересобирается только при смене версий.
COPY requirements-runtime.txt .
RUN pip install -r requirements-runtime.txt

# Код принадлежит root и недоступен для записи пользователю app:
# скомпрометированный процесс не сможет подменить код приложения.
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY static ./static

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

# Корректное завершение: exec-форма (uvicorn — PID 1 и сам получает сигнал),
# SIGTERM — штатный сигнал остановки, uvicorn дожидается текущих запросов
# и выходит с кодом 0. Настройки (БД, порт) — только из переменных окружения.
STOPSIGNAL SIGTERM
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "15"]
