#!/usr/bin/env bash
set -euo pipefail
APP_DIR="/opt/city-duma"

sudo -u cityduma bash -c "cd $APP_DIR && set -a && source /etc/city-duma/app.env && set +a && .venv/bin/python -m alembic upgrade head"

sudo cp "$APP_DIR/deploy/duma.service" /etc/systemd/system/city-duma.service
sudo systemctl daemon-reload
sudo systemctl enable city-duma.service
sudo systemctl restart city-duma.service
sudo systemctl status city-duma.service --no-pager