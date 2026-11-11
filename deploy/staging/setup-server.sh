#!/usr/bin/env bash
# ЛР6: однократная подготовка окружения staging на сервере (запускает администратор).
#
#   sudo bash deploy/staging/setup-server.sh "<публичный SSH-ключ pipeline>"
#
# Что делает:
#   1. создаёт отдельную учётную запись deploy — под ней pipeline разворачивает
#      приложение; пароля нет, вход только по ключу;
#   2. разрешает этому ключу только неинтерактивные команды и только из сетей
#      Docker (откуда подключаются jobs runner'а): без терминала и пробросов;
#   3. создаёт каталог окружения /srv/city-duma-staging и файл настроек .env
#      со случайным паролем БД — настройки живут только на сервере;
#   4. подсказывает, как один раз выполнить docker login к registry (deploy token).
#
# Скрипт идемпотентен: повторный запуск ничего не ломает и пароль не меняет.
set -euo pipefail

PUBKEY="${1:?Передайте публичный ключ: sudo bash setup-server.sh \"ssh-ed25519 AAAA... gitlab-ci-deploy\"}"
DEPLOY_USER="deploy"
DEPLOY_DIR="/srv/city-duma-staging"
APP_PORT="${APP_PORT:-8800}"
# Подключения из jobs приходят с адресов сетей Docker (172.16.0.0/12).
ALLOWED_FROM="${ALLOWED_FROM:-172.16.0.0/12,127.0.0.1}"

[[ $EUID -eq 0 ]] || { echo "Запустите через sudo"; exit 1; }
getent group docker >/dev/null || { echo "Нет группы docker — установите Docker"; exit 1; }

if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$DEPLOY_USER"
    passwd --lock "$DEPLOY_USER" >/dev/null
    echo "Создан пользователь $DEPLOY_USER (вход по паролю заблокирован)"
fi
# Группа docker нужна, чтобы запускать контейнеры. Это права уровня root на
# Docker, поэтому учётная запись используется ТОЛЬКО для развёртывания.
usermod -aG docker "$DEPLOY_USER"

install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
printf 'restrict,from="%s" %s\n' "$ALLOWED_FROM" "$PUBKEY" \
    > "/home/$DEPLOY_USER/.ssh/authorized_keys"
chown "$DEPLOY_USER:$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh/authorized_keys"
chmod 600 "/home/$DEPLOY_USER/.ssh/authorized_keys"
echo "Ключ pipeline добавлен (restrict, from=$ALLOWED_FROM)"

install -d -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DEPLOY_DIR" "$DEPLOY_DIR/state"
if [[ ! -f "$DEPLOY_DIR/.env" ]]; then
    umask 077
    cat > "$DEPLOY_DIR/.env" <<EOF
# Настройки окружения staging. Хранятся только на сервере (не в образе, не в git).
POSTGRES_USER=duma
POSTGRES_PASSWORD=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)
POSTGRES_DB=city_duma
APP_PORT=$APP_PORT
APP_BIND=0.0.0.0
ENVIRONMENT=staging
EOF
    chown "$DEPLOY_USER:$DEPLOY_USER" "$DEPLOY_DIR/.env"
    echo "Создан $DEPLOY_DIR/.env (пароль БД сгенерирован)"
fi

cat <<EOF

Готово. Остался один шаг — вход в registry для пользователя $DEPLOY_USER
(deploy token с правом read_registry: Settings -> Repository -> Deploy tokens):

  sudo -iu $DEPLOY_USER docker login 192.168.58.139:5050 -u <имя deploy token>

Приложение staging будет доступно по адресу http://<IP сервера>:$APP_PORT
EOF
