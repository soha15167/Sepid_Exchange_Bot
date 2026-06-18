#!/bin/bash
# Sepid Exchange — systemd units (run on server as root)
#
# Bot + shared DB/API codebase:
#   /root/telegram_bot_project2
#
# Web UI (Next.js standalone):
#   /root/web
#
# Services:
#   sepid-web-api  → port 8100  (FastAPI)
#   sepid-web-ui   → port 3100  (Next.js)
#
# Setup:
#   cp deploy/sepid-web-api.service /etc/systemd/system/
#   cp deploy/sepid-web-ui.service /etc/systemd/system/
#   systemctl daemon-reload
#   systemctl enable --now sepid-web-api sepid-web-ui
#
# Restart after deploy:
#   systemctl restart sepid-web-api sepid-web-ui
#
# HTTPS (optional):
#   cp deploy/nginx-sepid.conf /etc/nginx/sites-available/sepid
#   edit server_name + certbot
