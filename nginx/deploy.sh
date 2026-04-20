#!/usr/bin/env bash
# deploy.sh — первый деплой и обновления
# Запускать на VPS от root или пользователя с sudo
set -euo pipefail

DOMAIN="${DOMAIN:-example.com}"          # export DOMAIN=yourdomain.com
EMAIL="${EMAIL:-admin@example.com}"      # export EMAIL=your@email.com
APP_DIR="/opt/tonpred"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

# ── 1. Зависимости ─────────────────────────────────────────────────────────────
install_deps() {
  log "Устанавливаем зависимости..."
  apt-get update -qq
  apt-get install -y -qq docker.io docker-compose-plugin curl git ufw

  systemctl enable docker --now
  log "Docker $(docker --version)"
}

# ── 2. Firewall ────────────────────────────────────────────────────────────────
setup_firewall() {
  log "Настраиваем UFW..."
  ufw --force reset
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow 22/tcp    # SSH
  ufw allow 80/tcp    # HTTP (нужен для certbot challenge)
  ufw allow 443/tcp   # HTTPS
  ufw --force enable
  log "UFW активен"
}

# ── 3. Код ─────────────────────────────────────────────────────────────────────
setup_app() {
  log "Разворачиваем приложение в $APP_DIR..."
  mkdir -p "$APP_DIR"
  cd "$APP_DIR"

  if [ ! -f .env.prod ]; then
    warn ".env.prod не найден — создаём шаблон. ОБЯЗАТЕЛЬНО заполни перед запуском!"
    cat > .env.prod <<EOF
APP_ENV=production
SECRET_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
POSTGRES_USER=postgres
POSTGRES_DB=tonpred
DATABASE_URL=postgresql+asyncpg://postgres:\${POSTGRES_PASSWORD}@db:5432/tonpred
TON_API_KEY=
TON_NETWORK=mainnet
TON_API_URL=https://toncenter.com/api/v2
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 16)
MINI_APP_URL=https://${DOMAIN}
ALLOWED_ORIGINS=["https://${DOMAIN}","https://t.me"]
PLATFORM_FEE_BPS=200
EOF
    warn "Отредактируй $APP_DIR/.env.prod и запусти скрипт снова"
    exit 0
  fi
}

# ── 4. SSL — первичный выпуск сертификата ──────────────────────────────────────
issue_ssl() {
  log "Выпускаем SSL через Let's Encrypt..."

  # Поднимаем только nginx в режиме без SSL для прохождения challenge
  docker compose -f docker-compose.prod.yml up -d nginx certbot

  sleep 3

  docker compose -f docker-compose.prod.yml run --rm certbot \
    certonly --webroot \
    --webroot-path /var/www/certbot \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    -d "$DOMAIN" \
    -d "www.$DOMAIN"

  log "SSL сертификат получен"
}

# ── 5. Запуск ──────────────────────────────────────────────────────────────────
start_all() {
  log "Запускаем все сервисы..."
  cd "$APP_DIR"
  docker compose -f docker-compose.prod.yml pull
  docker compose -f docker-compose.prod.yml up -d --build

  log "Ждём запуска API..."
  sleep 8

  # Регистрируем Telegram webhook
  if grep -q "TELEGRAM_BOT_TOKEN=" .env.prod; then
    local token
    token=$(grep TELEGRAM_BOT_TOKEN .env.prod | cut -d= -f2)
    if [ -n "$token" ]; then
      curl -s -X POST "https://${DOMAIN}/api/v1/telegram/set-webhook" \
        -H "Content-Type: application/json" \
        -d "{\"url\": \"https://${DOMAIN}/api/v1/telegram/webhook\"}" | jq .
      log "Telegram webhook зарегистрирован"
    fi
  fi
}

# ── 6. Обновление (без первого деплоя) ─────────────────────────────────────────
update() {
  log "Обновляем приложение..."
  cd "$APP_DIR"
  git pull origin main
  docker compose -f docker-compose.prod.yml build api
  docker compose -f docker-compose.prod.yml up -d --no-deps api tasks
  docker compose -f docker-compose.prod.yml exec api alembic upgrade head
  log "Обновление завершено"
}

# ── Entrypoint ─────────────────────────────────────────────────────────────────
case "${1:-deploy}" in
  deploy)
    install_deps
    setup_firewall
    setup_app
    issue_ssl
    start_all
    log "✅ Деплой завершён! Открой https://${DOMAIN}"
    ;;
  update)
    update
    ;;
  ssl-renew)
    cd "$APP_DIR"
    docker compose -f docker-compose.prod.yml run --rm certbot renew
    docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
    ;;
  status)
    cd "$APP_DIR"
    docker compose -f docker-compose.prod.yml ps
    ;;
  logs)
    cd "$APP_DIR"
    docker compose -f docker-compose.prod.yml logs -f --tail=100 "${2:-api}"
    ;;
  *)
    echo "Использование: $0 [deploy|update|ssl-renew|status|logs]"
    ;;
esac
