#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# update.sh — Zaptec Solarcharge updatescript
#
# Gebruik:
#   bash update.sh           # update naar nieuwste versie op huidige branch
#   bash update.sh beta      # schakel over naar beta-branch en update
#   bash update.sh main      # schakel terug naar main-branch en update
#
# BELANGRIJK: Gebruik altijd dit script om te updaten, nooit 'git pull' direct.
# Dit script zorgt dat je lokale instellingen (config.yaml) nooit verloren gaan.
# ─────────────────────────────────────────────────────────────────────────────

set -e  # stop bij elke fout

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BACKUP_DIR="backups/$(date +%Y-%m-%d_%H-%M-%S)"
BRANCH="${1:-}"
CONFIG_TIJDELIJK="/tmp/zaptec_config_backup_$$.yaml"

echo ""
echo "════════════════════════════════════════════════"
echo "  Zaptec Solarcharge — Update"
echo "════════════════════════════════════════════════"

# ─── Stap 1: Lokale config.yaml opslaan VÓÓR elke git-operatie ───────────────
echo ""
echo "[1/5] Lokale instellingen beveiligen"
mkdir -p "$BACKUP_DIR"

if [ -f config/config.yaml ]; then
    cp config/config.yaml "$CONFIG_TIJDELIJK"
    cp config/config.yaml "$BACKUP_DIR/config.yaml"
    echo "      config.yaml beveiligd"
else
    CONFIG_TIJDELIJK=""
    echo "      config/config.yaml niet gevonden — wordt na update aangemaakt vanuit voorbeeld"
fi

[ -f config/.env ] && cp config/.env "$BACKUP_DIR/.env" && echo "      .env opgeslagen"
[ -f data/zaptec-solarcharge.db ] && \
    cp data/zaptec-solarcharge.db "$BACKUP_DIR/zaptec-solarcharge.db" && echo "      database opgeslagen"

# ─── Stap 2: Branch wisselen (optioneel) ─────────────────────────────────────
if [ -n "$BRANCH" ]; then
    echo ""
    echo "[2/5] Overschakelen naar branch: $BRANCH"
    git fetch origin
    git checkout "$BRANCH"
else
    echo ""
    echo "[2/5] Branch ongewijzigd: $(git branch --show-current)"
fi

# ─── Stap 3: Git pull ────────────────────────────────────────────────────────
echo ""
echo "[3/5] Git pull uitvoeren"
git pull

# ─── Stap 4: Config.yaml ALTIJD terugzetten ──────────────────────────────────
echo ""
echo "[4/5] Lokale instellingen terugzetten"

if [ -n "$CONFIG_TIJDELIJK" ] && [ -f "$CONFIG_TIJDELIJK" ]; then
    cp "$CONFIG_TIJDELIJK" config/config.yaml
    rm -f "$CONFIG_TIJDELIJK"
    echo "      Lokale instellingen teruggezet"
else
    # Eerste installatie of config was er niet — kopieer voorbeeld als startpunt
    cp config/config.yaml.example config/config.yaml
    echo "      Voorbeeld gekopieerd naar config/config.yaml"
    echo "      LET OP: pas config/config.yaml aan met jouw instellingen!"
fi

# ─── Stap 5: Dependencies + herstart ─────────────────────────────────────────
echo ""
echo "[5/5] Dependencies bijwerken en service herstarten"
venv/bin/pip install -r requirements.txt -q && echo "      pip install klaar"
sudo systemctl restart zaptec-solarcharge && echo "      service herstart"

# ─── Klaar ───────────────────────────────────────────────────────────────────
NIEUWE_VERSIE=$(cat VERSION 2>/dev/null || echo "onbekend")
echo ""
echo "════════════════════════════════════════════════"
echo "  Update klaar!  Versie: $NIEUWE_VERSIE"
echo "  Branch:        $(git branch --show-current)"
echo "  Backup staat:  $BACKUP_DIR"
echo "  Bij problemen: bash rollback.sh"
echo "════════════════════════════════════════════════"
echo ""
