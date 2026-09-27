#!/usr/bin/env bash
set -euo pipefail
ADMIN_SUBNET="192.168.216.0/24"

sudo apt-get install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from "$ADMIN_SUBNET" to any port 22 proto tcp comment 'SSH только из локальной сети'
sudo ufw allow 8000/tcp comment 'API доступен клиентам'
sudo ufw --force enable
sudo ufw status verbose