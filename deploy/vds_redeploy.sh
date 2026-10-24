#!/usr/bin/env bash
# Update the app on the VDS from /tmp/deploy/code.tar.gz (git archive of the repo). Keeps .env files, data and the database.
set -euo pipefail
APP=/opt/hackathon
tar -xzf /tmp/deploy/code.tar.gz -C "$APP/app"
"$APP/venv/bin/pip" install -q -r "$APP/app/requirements.txt" -r "$APP/app/knowledge-ingestion/requirements.txt"
chown -R hackathon:hackathon "$APP"
cd "$APP/app/frontend"
sudo -u hackathon -H env HOME="$APP" npm ci --no-audit --no-fund --loglevel=error >/dev/null
sudo -u hackathon -H env HOME="$APP" VITE_BACKEND_API=/api npm run build --silent 2>&1 | tail -1
systemctl restart hackathon-backend
sleep 3
systemctl restart hackathon-ingestion
rm -f /tmp/deploy/code.tar.gz
for s in hackathon-postgres hackathon-backend hackathon-ingestion; do echo "$s: $(systemctl is-active "$s")"; done
