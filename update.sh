#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# update.sh — Zaptec Solarcharge updatescript
#
# Gebruik:
#   bash update.sh           # update naar nieuwste versie op huidige branch
#   bash update.sh beta      # schakel over naar beta-branch en update
#   bash update.sh main      # schakel terug naar main-branch en update
#
# Dit script:
#   1. Maakt een backup van config, .env en database
#   2. Lost eenmalig de overgang op waarbij config.yaml nog in git zit
#   3. Voert git pull uit
#   4. Herstelt config.yaml als git die heeft verwijderd
#   5. Installeert eventuele nieuwe Python-packages
#   6. Herstart de systemd-service
# ─────────────────────────────────────────────────────────────────────────────

set -e  # stop bij elke fout

# Ga naar de projectmap (ook als het script vanuit een andere map wordt aangeroepen)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BACKUP_DIR="backups/$(date +%Y-%m-%d_%H-%M-%S)"
BRANCH="${1:-}"  # optioneel: gewenste branch als eerste argument

echo ""
echo "════════════════════════════════════════════════"
echo "  Zaptec Solarcharge — Update"
echo "════════════════════════════════════════════════"

# ─── Stap 1: Backup maken ────────────────────────────────────────────────────
echo ""
echo "[1/5] Backup maken → $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

[ -f config/config.yaml ] && cp config/config.yaml "$BACKUP_DIR/config.yaml" && echo "      config.yaml opgeslagen"
[ -f config/.env ]        && cp config/.env        "$BACKUP_DIR/.env"        && echo "      .env opgeslagen"
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

# ─── Stap 3: Config.yaml losmaken van git (eenmalige migratie) ───────────────
echo ""
echo "[3/5] Git pull voorbereiden"

# Controleer of config.yaml nog door git getrackt wordt
if git ls-files --error-unmatch config/config.yaml 2>/dev/null; then
    echo "      config.yaml zit nog in git — wordt losgekoppeld (eenmalig)"
    # Reset naar schone git-versie zodat git pull niet blokkeert op lokale wijzigingen
    git checkout -- config/config.yaml
    echo "      config.yaml gereset naar git-versie (backup al gemaakt in stap 1)"
fi

# ─── Stap 4: Git pull ────────────────────────────────────────────────────────
echo ""
echo "[4/5] Git pull uitvoeren"
git pull

# ─── Herstellen: config.yaml terugzetten als git die heeft verwijderd ────────
if [ ! -f config/config.yaml ]; then
    if [ -f "$BACKUP_DIR/config.yaml" ]; then
        cp "$BACKUP_DIR/config.yaml" config/config.yaml
        echo "      config.yaml hersteld vanuit backup (git had het verwijderd)"
    else
        echo "WAARSCHUWING: config.yaml ontbreekt en er is geen backup!"
        echo "Kopieer config/config.yaml.example naar config/config.yaml"
        echo "en pas de instellingen aan."
    fi
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
