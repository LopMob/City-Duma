#!/usr/bin/env bash
# Выполняется НА app-server, от пользователя с sudo.
# Проверено на Ubuntu Server 26.04 LTS.
set -euo pipefail

APP_USER="cityduma"
APP_DIR="/opt/city-duma"
CONFIG_DIR="/etc/city-duma"
REPO_URL="https://github.com/LopMob/City-Duma.git"

echo "== 1. Системные пакеты =="
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git ufw

echo "== 2. Пользователь для запуска приложения (без sudo, без интерактивного входа) =="
if ! id "$APP_USER" &>/dev/null; then
    sudo useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

echo "== 3. Каталог приложения =="
sudo mkdir -p "$APP_DIR"
if [ ! -d "$APP_DIR/.git" ]; then
    sudo git clone "$REPO_URL" "$APP_DIR"
fi
sudo chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
sudo chmod 750 "$APP_DIR"

echo "== 4. Виртуальное окружение и зависимости =="
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "== 5. Конфигурация, отдельно от кода =="
sudo mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/app.env" ]; then
    sudo cp "$APP_DIR/.env.example" "$CONFIG_DIR/app.env"
    echo "!! Отредактируйте $CONFIG_DIR/app.env — впишите DATABASE_URL и ENVIRONMENT=production !!"
fi
sudo chown "$APP_USER":"$APP_USER" "$CONFIG_DIR/app.env"
sudo chmod 600 "$CONFIG_DIR/app.env"

echo "Готово. Дальше: отредактируйте $CONFIG_DIR/app.env и запустите install_service.sh"