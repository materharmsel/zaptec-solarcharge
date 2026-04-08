#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Zaptec Solarcharge — Installatiescript voor Raspberry Pi OS
# Uitvoeren vanuit de projectmap: bash setup.sh
# ─────────────────────────────────────────────────────────────────

set -e  # Stop bij elke fout

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAAM="zaptec-solarcharge"
SERVICE_BESTAND="$INSTALL_DIR/zaptec-solarcharge.service"
VENV_DIR="$INSTALL_DIR/venv"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Zaptec Solarcharge — Installatie        ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Installatiemap: $INSTALL_DIR"
echo ""

# ── Stap 1: Controleer Python 3 ──────────────────────────────────
echo "[1/6] Python 3 controleren..."
if ! command -v python3 &>/dev/null; then
    echo "  FOUT: python3 niet gevonden. Installeer het met:"
    echo "  sudo apt install python3 python3-venv python3-pip"
    exit 1
fi
PYTHON_VER=$(python3 --version)
echo "  OK: $PYTHON_VER"

# ── Stap 2: Virtual environment aanmaken ─────────────────────────
echo "[2/6] Virtual environment aanmaken..."
if [ -d "$VENV_DIR" ]; then
    echo "  Bestaand venv gevonden — overgeslagen."
else
    python3 -m venv "$VENV_DIR"
    echo "  OK: venv aangemaakt in $VENV_DIR"
fi

# ── Stap 3: Python pakketten installeren ─────────────────────────
echo "[3/6] Python pakketten installeren..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet
echo "  OK: pakketten geïnstalleerd"

# ── Stap 4: Mappen aanmaken ───────────────────────────────────────
echo "[4/6] Mappen aanmaken..."
mkdir -p "$INSTALL_DIR/data"
mkdir -p "$INSTALL_DIR/logs"
echo "  OK: data/ en logs/ aangemaakt"

# ── Stap 5: .env aanmaken als die nog niet bestaat ────────────────
echo "[5/6] Credentials bestand controleren..."
ENV_BESTAND="$INSTALL_DIR/config/.env"
if [ ! -f "$ENV_BESTAND" ]; then
    cat > "$ENV_BESTAND" << 'EOF'
# Zaptec Solarcharge — Inloggegevens
# Pas deze waarden aan: nano config/.env

ZAPTEC_USERNAME=vul_hier_je_email_in
ZAPTEC_PASSWORD=vul_hier_je_wachtwoord_in
HOMEWIZARD_TOKEN=vul_hier_je_token_in
EOF
    echo "  OK: config/.env aangemaakt (nog invullen!)"
else
    echo "  config/.env bestaat al — niet overschreven."
fi

# ── Stap 6: systemd service installeren ──────────────────────────
echo "[6/6] Systemd service installeren..."

# Pas het pad in het service-bestand aan naar de werkelijke installatielocatie
TIJDELIJK_SERVICE="/tmp/${SERVICE_NAAM}.service"
sed "s|/home/pi/zaptec-solarcharge|$INSTALL_DIR|g" "$SERVICE_BESTAND" > "$TIJDELIJK_SERVICE"

# Pas ook de gebruiker aan naar de huidige gebruiker als dat niet 'pi' is
HUIDIG_GEBRUIKER="$(whoami)"
if [ "$HUIDIG_GEBRUIKER" != "pi" ]; then
    sed -i "s|User=pi|User=$HUIDIG_GEBRUIKER|g" "$TIJDELIJK_SERVICE"
    sed -i "s|Group=pi|Group=$HUIDIG_GEBRUIKER|g" "$TIJDELIJK_SERVICE"
fi

sudo cp "$TIJDELIJK_SERVICE" "/etc/systemd/system/${SERVICE_NAAM}.service"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAAM"
sudo systemctl start "$SERVICE_NAAM"
echo "  OK: service geïnstalleerd, ingeschakeld en gestart"

# ── Klaar ─────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     Installatie voltooid!                ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Haal het IP-adres op voor de webinterface-URL
IP=$(hostname -I | awk '{print $1}')
POORT=$(grep -E "^\s*poort:" "$INSTALL_DIR/config/config.yaml" | awk '{print $2}' | tr -d '\r' || echo "5000")

echo "📋 Volgende stappen:"
echo ""
echo "  1. Vul je inloggegevens in:"
echo "     nano $INSTALL_DIR/config/.env"
echo ""
echo "  2. Stel je IP-adressen en IDs in:"
echo "     nano $INSTALL_DIR/config/config.yaml"
echo ""
echo "  3. Herstart de service na het aanpassen:"
echo "     sudo systemctl restart zaptec-solarcharge"
echo ""
echo "🔧 Service beheren:"
echo "  Status:    sudo systemctl status zaptec-solarcharge"
echo "  Logs:      journalctl -u zaptec-solarcharge -f"
echo "  Stop:      sudo systemctl stop zaptec-solarcharge"
echo "  Herstart:  sudo systemctl restart zaptec-solarcharge"
echo ""
echo "🌐 Webinterface:  http://${IP}:${POORT}"
echo ""
