#!/usr/bin/env bash
# ЛР6: развёртывание приложения в окружении staging (выполняется НА СЕРВЕРЕ).
#
# Pipeline подключается к серверу по SSH под отдельной учётной записью deploy
# и передаёт этот скрипт на вход bash:
#   ssh deploy@<сервер> "bash -s -- deploy <образ>"   < deploy/staging/deploy.sh
#
# Действия:
#   deploy <образ>         скачать образ из registry и запустить его
#   rollback <репозиторий> [тег]
#                          вернуть последнюю проверенную версию или указанный тег
#   status                 проверить контейнеры (app и db healthy, migrate завершён с 0)
#   mark-good <образ>      записать образ как проверенный рабочий релиз
#   history                показать журнал выпусков
#
# Принципы:
#   * образ только скачивается из registry (docker pull), на сервере не собирается;
#   * настройки — в $DEPLOY_DIR/.env (только на сервере), в образе их нет;
#   * всё, что может не получиться до остановки старой версии (нет переменной,
#     неверный тег, недоступный registry), проверяется ДО замены контейнеров —
#     работающая версия при этом не затрагивается;
#   * одновременно идёт только одно развёртывание (flock);
#   * «рабочей» версия становится только после проверки релиза (mark-good).
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/srv/city-duma-staging}"
PROJECT="${COMPOSE_PROJECT:-city-duma-staging}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"
STATE="$DEPLOY_DIR/state"
REQUIRED_VARS=(POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB APP_PORT APP_BIND ENVIRONMENT)

log() { printf '[deploy %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { printf '[deploy] ОШИБКА: %s\n' "$1" >&2; exit "${2:-1}"; }
record() { printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" >> "$STATE/releases.log"; }

compose() {
    docker compose -p "$PROJECT" --env-file "$DEPLOY_DIR/.env" \
        -f "$DEPLOY_DIR/compose.yaml" -f "$DEPLOY_DIR/compose.deploy.yaml" "$@"
}

# ------------------------------------------------------------- проверки --

preflight() {
    [[ -d "$DEPLOY_DIR" ]] || fail "нет каталога окружения $DEPLOY_DIR (см. deploy/staging/setup-server.sh)"
    for f in compose.yaml compose.deploy.yaml .env; do
        [[ -f "$DEPLOY_DIR/$f" ]] || fail "нет файла $DEPLOY_DIR/$f"
    done
    local missing=()
    for var in "${REQUIRED_VARS[@]}"; do
        if ! grep -Eq "^${var}=.+" "$DEPLOY_DIR/.env"; then
            missing+=("$var")
        fi
    done
    if ((${#missing[@]})); then
        fail "в $DEPLOY_DIR/.env не задана переменная: ${missing[*]} — развёртывание остановлено, работающая версия не тронута" 10
    fi
    mkdir -p "$STATE"
    touch "$STATE/releases.log"
}

check_image_ref() {
    local ref="$1"
    # <registry>/<путь>:<тег>; тег по правилам Docker: буквы, цифры, . _ -
    [[ "$ref" =~ ^[a-z0-9.:-]+/[a-z0-9._/-]+:([A-Za-z0-9_][A-Za-z0-9._-]{0,127})$ ]] \
        || fail "некорректная ссылка на образ: '$ref' (ожидается <registry>/<проект>:<тег>)" 11
}

pull_image() {
    local ref="$1" out
    log "скачиваю образ $ref из registry"
    if ! out=$(docker pull "$ref" 2>&1); then
        printf '%s\n' "$out" >&2
        if grep -qiE 'manifest unknown|not found|manifest for .* not found' <<<"$out"; then
            fail "образ $ref не найден в registry: неверный тег? Работающая версия не тронута" 12
        fi
        fail "registry недоступен или отказал в доступе (см. ответ выше). Работающая версия не тронута" 13
    fi
    printf '%s\n' "$out" | tail -n 2
}

container_id() { compose ps -a -q "$1" 2>/dev/null | head -n 1; }

wait_healthy() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT)) id status
    log "жду, пока app станет healthy (до ${HEALTH_TIMEOUT} с)"
    while ((SECONDS < deadline)); do
        id=$(container_id app)
        if [[ -n "$id" ]]; then
            status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id")
            [[ "$status" == healthy ]] && { log "app healthy"; return 0; }
        fi
        sleep 3
    done
    log "app не стал healthy за ${HEALTH_TIMEOUT} с. Последние строки журнала:"
    compose logs --no-color --tail 30 app migrate >&2 || true
    return 1
}

# -------------------------------------------------------------- действия --

do_deploy() {
    local ref="${1:?не указан образ}"
    check_image_ref "$ref"
    preflight
    exec 9>"$STATE/deploy.lock"
    flock -n 9 || fail "в окружении уже идёт другое развёртывание — дождитесь его окончания" 14

    pull_image "$ref"
    log "запускаю $ref (без сборки на сервере)"
    export APP_IMAGE="$ref"
    printf '%s\n' "$ref" > "$STATE/deployed"
    record deployed "$ref"
    if ! compose up -d --no-build --remove-orphans; then
        compose logs --no-color --tail 30 migrate app >&2 || true
        record failed-start "$ref"
        fail "контейнеры не запустились (см. журнал выше) — выпуск НЕ принят. Верните предыдущую версию: job rollback:staging" 21
    fi

    if ! wait_healthy; then
        record failed-health "$ref"
        fail "проверка здоровья не пройдена — выпуск НЕ принят. Верните предыдущую версию: job rollback:staging" 20
    fi
    log "развёрнут $ref; выпуск будет принят только после проверки релиза"
    echo "DEPLOYED_IMAGE=$ref"
}

do_rollback() {
    local repo="${1:?не указан репозиторий образа}" tag="${2:-}" target
    preflight
    if [[ -n "$tag" ]]; then
        target="$repo:$tag"
    else
        local deployed current previous
        deployed=$(cat "$STATE/deployed" 2>/dev/null || true)
        current=$(cat "$STATE/current" 2>/dev/null || true)
        previous=$(cat "$STATE/previous" 2>/dev/null || true)
        # Если сейчас запущен непроверенный выпуск — вернуть последний проверенный,
        # иначе — предпоследний проверенный.
        if [[ -n "$current" && "$deployed" != "$current" ]]; then
            target="$current"
        else
            target="$previous"
        fi
        [[ -n "$target" ]] || fail "нет предыдущей проверенной версии — укажите тег явно (ROLLBACK_TO)" 15
    fi
    log "возврат к версии $target"
    record rollback "$target"
    do_deploy "$target"
}

do_status() {
    preflight
    local ok=0 id state health code
    for svc in db app; do
        id=$(container_id "$svc")
        if [[ -z "$id" ]]; then
            echo "  [FAIL] $svc: контейнер не найден"; ok=1; continue
        fi
        state=$(docker inspect -f '{{.State.Status}}' "$id")
        health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}нет{{end}}' "$id")
        if [[ "$state" == running && "$health" == healthy ]]; then
            echo "  [OK]   $svc: running, healthy"
        else
            echo "  [FAIL] $svc: $state, health=$health"; ok=1
        fi
    done
    id=$(container_id migrate)
    code=$([[ -n "$id" ]] && docker inspect -f '{{.State.ExitCode}}' "$id" || echo "?")
    if [[ "$code" == 0 ]]; then
        echo "  [OK]   migrate: миграции применены (код 0)"
    else
        echo "  [FAIL] migrate: код завершения $code"; ok=1
    fi
    echo "  запущенный образ: $(cat "$STATE/deployed" 2>/dev/null || echo '-')"
    echo "  проверенный релиз: $(cat "$STATE/current" 2>/dev/null || echo '-')"
    return "$ok"
}

do_mark_good() {
    local ref="${1:?не указан образ}" current
    preflight
    [[ "$(cat "$STATE/deployed" 2>/dev/null)" == "$ref" ]] \
        || fail "сейчас запущен не $ref — отметить его рабочим нельзя" 16
    current=$(cat "$STATE/current" 2>/dev/null || true)
    if [[ "$current" != "$ref" ]]; then
        [[ -n "$current" ]] && printf '%s\n' "$current" > "$STATE/previous"
        printf '%s\n' "$ref" > "$STATE/current"
    fi
    record verified "$ref"
    log "выпуск $ref принят (проверка релиза пройдена)"
}

case "${1:-}" in
    deploy) shift; do_deploy "$@" ;;
    rollback) shift; do_rollback "$@" ;;
    status) do_status ;;
    mark-good) shift; do_mark_good "$@" ;;
    history) preflight; tail -n 30 "$STATE/releases.log" ;;
    *) fail "использование: deploy <образ> | rollback <репозиторий> [тег] | status | mark-good <образ> | history" 2 ;;
esac
