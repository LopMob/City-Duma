#!/usr/bin/env bash
set -euo pipefail
ADMIN_SUBNET="192.168.216.0/24"
APP_SERVER_IP="192.168.216.11"

sudo apt-get install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from "$ADMIN_SUBNET" to any port 22 proto tcp comment 'SSH только из локальной сети'
sudo ufw allow from "$APP_SERVER_IP" to any port 5432 proto tcp comment 'PostgreSQL только с app-server'
sudo ufw --force enable
sudo ufw status verbose