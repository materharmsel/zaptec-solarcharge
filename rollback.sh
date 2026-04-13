#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# rollback.sh — Zaptec Solarcharge rollback naar vorige versie
#
# Gebruik:
#   bash rollback.sh         # toont lijst, vraagt om keuze
#   bash rollback.sh 2026-04-13_14-30-00   # herstel specifieke backup direct
#
# Dit script herstelt: config.yaml, .env en de database vanuit een backup.
# De code (Python-bestanden) wordt NIET teruggezet — die bevinden zich in git.
# Voor code-rollback: git checkout <commit-of-tag>
# ─────────────────────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "════════════════════════════════════════════════"
echo "  Zaptec Solarcharge — Rollback"
echo "════════════════════════════════════════════════"

# ─── Controleer of backups bestaan ───────────────────────────────────────────
if [ ! -d backups ] || [ -z "$(ls -A backups 2>/dev/null)" ]; then
    echo ""
    echo "FOUT: Geen backups gevonden in de map 'backups/'."
    echo "Er is niets om naar terug te keren."
    exit 1
fi

# ─── Backup kiezen ───────────────────────────────────────────────────────────
if [ -n "$1" ]; then
    KEUZE="$1"
else
    echo ""
    echo "Beschikbare backups (nieuwste eerst):"
    echo ""
    ls -1 backups/ | sort -r | head -15 | nl -w2 -s". "
    echo ""
    read -p "Voer de mapnaam in van de backup die je wilt herstellen: " KEUZE
fi

BACKUP_PAD="backups/$KEUZE"

if [ ! -d "$BACKUP_PAD" ]; then
    echo ""
    echo "FOUT: Backup niet gevonden: $BACKUP_PAD"
    exit 1
fi

# ─── Bevestiging vragen ───────────────────────────────────────────────────────
echo ""
echo "Te herstellen backup: $BACKUP_PAD"
echo "Inhoud:"
ls -lh "$BACKUP_PAD" | tail -n +2 | awk '{print "  " $NF " (" $5 ")"}'
echo ""
read -p "Weet je het zeker? Dit overschrijft de huidige config en database. [j/N] " BEVESTIGING

if [[ "$BEVESTIGING" != "j" && "$BEVESTIGING" != "J" ]]; then
    echo "Rollback geannuleerd."
    exit 0
fi

# ─── Bestanden herstellen ────────────────────────────────────────────────────
echo ""
echo "Herstellen..."

if [ -f "$BACKUP_PAD/config.yaml" ]; then
    cp "$BACKUP_PAD/config.yaml" config/config.yaml
    echo "  config.yaml hersteld"
else
    echo "  config.yaml: niet aanwezig in backup, overgeslagen"
fi

if [ -f "$BACKUP_PAD/.env" ]; then
    cp "$BACKUP_PAD/.env" config/.env
    echo "  .env hersteld"
else
    echo "  .env: niet aanwezig in backup, overgeslagen"
fi

if [ -f "$BACKUP_PAD/zaptec-solarcharge.db" ]; then
    cp "$BACKUP_PAD/zaptec-solarcharge.db" data/zaptec-solarcharge.db
    echo "  database hersteld"
else
    echo "  database: niet aanwezig in backup, overgeslagen"
fi

# ─── Service herstarten ───────────────────────────────────────────────────────
echo ""
echo "Service herstarten..."
sudo systemctl restart zaptec-solarcharge && echo "  service herstart"

echo ""
echo "════════════════════════════════════════════════"
echo "  Rollback klaar! Backup '$KEUZE' hersteld."
echo "  Controleer de webinterface om te bevestigen."
echo "════════════════════════════════════════════════"
echo ""
