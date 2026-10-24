#!/usr/bin/env bash
# Deploy the hackathon app to /opt/hackathon (idempotent). Inputs in /tmp/deploy: code.tar.gz, data.tar.gz,
# aethelgard_finance.dump, backend_knowledge.dump, backend.env, ingestion.env. Other sites on this server are not touched.
set -euo pipefail
APP=/opt/hackathon
SRC=/tmp/deploy
DOMAIN=nullpointers.rgmmonitor.co.in
PGPORT=54329
step() { echo; echo "=== $*"; }

step "system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3.12-venv unixodbc >/dev/null

step "user and files"
id hackathon >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin hackathon
mkdir -p "$APP/app" "$APP/pgdata" "$APP/logs"
tar -xzf "$SRC/code.tar.gz" -C "$APP/app"
[ -d "$APP/app/data" ] || tar -xzf "$SRC/data.tar.gz" -C "$APP/app"
install -m 600 "$SRC/backend.env" "$APP/app/.env"
install -m 600 "$SRC/ingestion.env" "$APP/app/knowledge-ingestion/.env"
sed -i "s#^BACKEND_EVENTS_URL=.*#BACKEND_EVENTS_URL=http://127.0.0.1:8100#" "$APP/app/knowledge-ingestion/.env"
sed -i "s#^DATABASE_URL=.*#DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:$PGPORT/aethelgard_finance#" "$APP/app/knowledge-ingestion/.env"

step "python environment"
[ -x "$APP/venv/bin/python" ] || python3.12 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/app/requirements.txt" -r "$APP/app/knowledge-ingestion/requirements.txt" 
PGBIN=$("$APP/venv/bin/python" -c "import pgserver, pathlib; print(pathlib.Path(pgserver.__file__).parent / 'pginstall' / 'bin')")
echo "postgres binaries: $PGBIN"
chown -R hackathon:hackathon "$APP"

step "postgres service"
if [ ! -f "$APP/pgdata/PG_VERSION" ]; then
  sudo -u hackathon "$PGBIN/initdb" -D "$APP/pgdata" -U postgres --auth=trust -E UTF8 >/dev/null
fi
cat > /etc/systemd/system/hackathon-postgres.service <<EOF
[Unit]
Description=Hackathon PostgreSQL 16 (local only)
After=network.target
[Service]
User=hackathon
ExecStart=$PGBIN/postgres -D $APP/pgdata -h 127.0.0.1 -p $PGPORT
Restart=always
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now hackathon-postgres >/dev/null
for i in $(seq 1 30); do "$PGBIN/pg_isready" -h 127.0.0.1 -p $PGPORT -q && break; sleep 1; done

step "restore databases"
for db in aethelgard_finance backend_knowledge; do
  if ! "$PGBIN/psql" -h 127.0.0.1 -p $PGPORT -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1; then
    "$PGBIN/createdb" -h 127.0.0.1 -p $PGPORT -U postgres "$db"
    "$PGBIN/pg_restore" -h 127.0.0.1 -p $PGPORT -U postgres -d "$db" --no-owner "$SRC/$db.dump" || echo "(pg_restore reported warnings for $db)"
    echo "restored $db"
  else
    echo "$db exists: kept"
  fi
done
"$PGBIN/psql" -h 127.0.0.1 -p $PGPORT -U postgres -d aethelgard_finance -tAc \
  "SELECT 'finance tables: ' || count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')"

step "frontend build"
cd "$APP/app/frontend"
sudo -u hackathon -H env HOME="$APP" npm ci --no-audit --no-fund --loglevel=error >/dev/null
sudo -u hackathon -H env HOME="$APP" VITE_BACKEND_API=/api npm run build --silent 2>&1 | tail -2

step "app services"
cat > /etc/systemd/system/hackathon-backend.service <<EOF
[Unit]
Description=Hackathon backend API
After=hackathon-postgres.service
Requires=hackathon-postgres.service
[Service]
User=hackathon
WorkingDirectory=$APP/app
Environment=BACKEND_PG_URI=postgresql://postgres@127.0.0.1:$PGPORT/postgres
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP/venv/bin/uvicorn backend.api.app:app --host 127.0.0.1 --port 8100
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/hackathon-ingestion.service <<EOF
[Unit]
Description=Hackathon HubSpot knowledge ingestion
After=hackathon-backend.service
[Service]
User=hackathon
WorkingDirectory=$APP/app/knowledge-ingestion
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP/venv/bin/uvicorn knowledge_ingestion.api.app:app --host 127.0.0.1 --port 8101
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
chown -R hackathon:hackathon "$APP"
systemctl daemon-reload
systemctl enable hackathon-backend hackathon-ingestion >/dev/null
systemctl restart hackathon-backend
sleep 2
systemctl restart hackathon-ingestion

step "nginx site"
cat > /etc/nginx/sites-available/$DOMAIN <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    root $APP/app/frontend/dist;
    index index.html;
    client_max_body_size 100m;

    location /api/ {
        proxy_pass http://127.0.0.1:8100/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 1800s;
        proxy_send_timeout 1800s;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF
ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/$DOMAIN
nginx -t
systemctl reload nginx
echo "nginx site enabled for $DOMAIN"
