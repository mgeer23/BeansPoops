#!/usr/bin/env bash
#
# Bean's Potty Log - Server Setup Script
# Run with: sudo bash deploy/setup.sh
#
# This script is idempotent - safe to run multiple times.
#
set -euo pipefail

APP_DIR="/opt/beans-potty"
SERVICE_NAME="beans-potty"
DB_NAME="beans"
DB_USER="beans"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Bean's Potty Log - Server Setup ==="
echo ""

# Must run as root
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: Please run with sudo: sudo bash deploy/setup.sh"
    exit 1
fi

# -------------------------------------------------------
# 1. Install system packages
# -------------------------------------------------------
echo "[1/9] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3-venv python3-dev libpq-dev nginx curl > /dev/null
echo "  Done."

# -------------------------------------------------------
# 2. Create system user
# -------------------------------------------------------
echo "[2/9] Creating system user 'beans'..."
if id "beans" &>/dev/null; then
    echo "  User 'beans' already exists."
else
    useradd --system --shell /usr/sbin/nologin --home-dir "$APP_DIR" beans
    echo "  Created user 'beans'."
fi

# -------------------------------------------------------
# 3. Copy app files
# -------------------------------------------------------
echo "[3/9] Copying app to $APP_DIR..."
mkdir -p "$APP_DIR"
cp "$REPO_DIR/app.py" "$APP_DIR/"
cp "$REPO_DIR/requirements.txt" "$APP_DIR/"
cp "$REPO_DIR/seed_data.csv" "$APP_DIR/"
cp -r "$REPO_DIR/templates" "$APP_DIR/"
mkdir -p "$APP_DIR/data"
echo "  Done."

# -------------------------------------------------------
# 4. Create Python venv and install dependencies
# -------------------------------------------------------
echo "[4/9] Setting up Python virtual environment..."
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
    echo "  Created venv."
else
    echo "  Venv already exists."
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
echo "  Dependencies installed."

# -------------------------------------------------------
# 5. Set up PostgreSQL database and user
# -------------------------------------------------------
echo "[5/9] Setting up PostgreSQL..."
DB_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")

# Create user if not exists
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
    echo "  User '$DB_USER' already exists."
    # Keep existing password
    DB_PASS="EXISTING"
else
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" > /dev/null
    echo "  Created user '$DB_USER'."
fi

# Create database if not exists
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
    echo "  Database '$DB_NAME' already exists."
else
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" > /dev/null
    echo "  Created database '$DB_NAME'."
fi

# -------------------------------------------------------
# 6. Generate .env file
# -------------------------------------------------------
echo "[6/9] Configuring environment..."
if [ -f "$APP_DIR/.env" ]; then
    echo "  .env already exists, keeping it."
else
    if [ "$DB_PASS" = "EXISTING" ]; then
        echo ""
        echo "  The database user already exists. Please enter the password for '$DB_USER':"
        read -rsp "  Password: " DB_PASS
        echo ""
    fi
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    cat > "$APP_DIR/.env" << EOF
DATABASE_URL=postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME
SECRET_KEY=$SECRET_KEY
FLASK_ENV=production
EOF
    chmod 600 "$APP_DIR/.env"
    echo "  Created .env file."
fi

# Set ownership
chown -R beans:beans "$APP_DIR"

# -------------------------------------------------------
# 7. Install systemd service
# -------------------------------------------------------
echo "[7/9] Installing systemd service..."
cp "$REPO_DIR/deploy/beans-potty.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" > /dev/null 2>&1
echo "  Done."

# -------------------------------------------------------
# 8. Install Nginx config
# -------------------------------------------------------
echo "[8/9] Configuring Nginx..."
cp "$REPO_DIR/deploy/nginx-beans-potty.conf" /etc/nginx/sites-available/beans-potty
ln -sf /etc/nginx/sites-available/beans-potty /etc/nginx/sites-enabled/beans-potty

# Remove default site if it exists
if [ -L /etc/nginx/sites-enabled/default ]; then
    rm /etc/nginx/sites-enabled/default
    echo "  Removed default Nginx site."
fi

nginx -t > /dev/null 2>&1
systemctl restart nginx
echo "  Done."

# -------------------------------------------------------
# 9. Disable lid-close suspend (laptop server)
# -------------------------------------------------------
echo "[9/9] Configuring laptop for server use..."
LOGIND_CONF="/etc/systemd/logind.conf"
if grep -q "^HandleLidSwitch=ignore" "$LOGIND_CONF" 2>/dev/null; then
    echo "  Lid switch already configured."
else
    # Append or uncomment the setting
    if grep -q "^#HandleLidSwitch=" "$LOGIND_CONF"; then
        sed -i 's/^#HandleLidSwitch=.*/HandleLidSwitch=ignore/' "$LOGIND_CONF"
    else
        echo "HandleLidSwitch=ignore" >> "$LOGIND_CONF"
    fi
    if grep -q "^#HandleLidSwitchDocked=" "$LOGIND_CONF"; then
        sed -i 's/^#HandleLidSwitchDocked=.*/HandleLidSwitchDocked=ignore/' "$LOGIND_CONF"
    else
        echo "HandleLidSwitchDocked=ignore" >> "$LOGIND_CONF"
    fi
    systemctl restart systemd-logind > /dev/null 2>&1 || true
    echo "  Lid close will no longer suspend the laptop."
fi

# -------------------------------------------------------
# Start the app
# -------------------------------------------------------
echo ""
echo "Starting Bean's Potty Log..."
systemctl restart "$SERVICE_NAME"
sleep 2

if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo ""
    echo "=== Setup Complete! ==="
    echo ""
    # Get local IP
    LOCAL_IP=$(hostname -I | awk '{print $1}')
    echo "  App is running!"
    echo "  Local:   http://localhost/"
    echo "  Network: http://$LOCAL_IP/"
    echo ""
    echo "  Logs:    sudo journalctl -u beans-potty -f"
    echo "  Status:  sudo systemctl status beans-potty"
    echo ""
    echo "  To import your existing data, run:"
    echo "    bash $REPO_DIR/deploy/seed.sh"
    echo ""
else
    echo ""
    echo "ERROR: Service failed to start. Check logs with:"
    echo "  sudo journalctl -u beans-potty --no-pager -n 30"
    exit 1
fi
