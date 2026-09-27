#!/usr/bin/env bash
# Выполняется НА db-server, от пользователя с sudo.
# Проверено на Ubuntu Server 26.04 LTS + PostgreSQL 18.
set -euo pipefail

DB_NAME="city_duma"
DB_USER="cityduma_app"
APP_SERVER_IP="192.168.216.11"
PG_VERSION="18"

echo "== 1. Установка PostgreSQL =="
sudo apt-get update -y
sudo apt-get install -y postgresql postgresql-contrib

echo "== 2. Роль и база данных (минимальные права) =="
read -rsp "Введите пароль для роли ${DB_USER}: " DB_PASSWORD
echo
sudo -u postgres psql <<SQL
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${DB_USER}') THEN
      CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE;
   ELSE
      ALTER ROLE ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';
   END IF;
END
\$\$;

SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec
SQL

echo "== 3. Подключения только с app-server =="
PG_HBA="/etc/postgresql/${PG_VERSION}/main/pg_hba.conf"
PG_CONF="/etc/postgresql/${PG_VERSION}/main/postgresql.conf"

if ! sudo grep -q "${APP_SERVER_IP}/32" "$PG_HBA"; then
    echo "host    ${DB_NAME}    ${DB_USER}    ${APP_SERVER_IP}/32    scram-sha-256" | sudo tee -a "$PG_HBA"
fi
sudo sed -i "s/^#\?listen_addresses.*/listen_addresses = 'localhost,$(hostname -I | awk '{print $1}')'/" "$PG_CONF"

sudo systemctl restart postgresql
echo "Готово. Проверка: sudo -u postgres psql -c '\\du' и '\\l'"