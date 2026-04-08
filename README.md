# Solarcharge

Automatische laadregeling voor de Zaptec Go 2 op basis van de HomeWizard P1 Meter.
De auto laadt zoveel mogelijk op zonne-energie zonder teruglevering aan het net.

---

## Wat doet dit?

Solarcharge leest elke 10 seconden het netto stroomverbruik van je HomeWizard P1 Meter.
Als er een auto aangesloten is aan de Zaptec lader, past het systeem automatisch het laadvermogen aan:

- **Zonnepanelen produceren meer dan je verbruikt** → laadvermogen omhoog
- **Je importeert stroom van het net** → laadvermogen omlaag
- **Genoeg zon voor 3-fase** → automatisch wisselen naar 3-fase (in auto-modus)
- **Niet genoeg zon** → 1-fase laden op lagere stroom

---

## Vereisten

- Raspberry Pi met Raspberry Pi OS, Ubuntu of Debian
- Python 3.11 of nieuwer (`python3 --version`)
- HomeWizard P1 Meter (HWE-P1) met firmware ≥ 2.2.0 (voor v2 API)
- Zaptec Go 2 lader, aangesloten op een Zaptec-account
- Beide apparaten bereikbaar op hetzelfde Wi-Fi-netwerk als de Raspberry Pi

---

## Installatie

```bash
# 1. Haal de code op
git clone https://github.com/gebruikersnaam/solarcharge.git
cd solarcharge

# 2. Voer het installatiescript uit
bash setup.sh
```

Het script:
- Maakt een Python virtual environment aan
- Installeert alle afhankelijkheden
- Maakt de benodigde mappen aan (`data/`, `logs/`)
- Installeert de systemd-service zodat het systeem automatisch start bij het opstarten

---

## Stap 1: HomeWizard token ophalen

De HomeWizard v2 API vereist een token dat je eenmalig ophaalt door op de knop te drukken.

```bash
# Vervang <IP> door het IP-adres van je P1 Meter (zie je router of HomeWizard-app)
curl https://<IP>/api/user --insecure \
  -X POST \
  -H "Content-Type: application/json" \
  -H "X-Api-Version: 2" \
  -d '{"name": "local/solarcharge"}'
```

Je krijgt eerst `403 Forbidden` — dat is normaal.

**Druk nu op de knop van de P1 Meter** en stuur het commando opnieuw binnen 30 seconden.
Je ontvangt dan een JSON-response met een `token` veld (32 tekens).

Kopieer dit token en sla het op in `config/.env`:
```
HOMEWIZARD_TOKEN=ABCDEF1234567890ABCDEF1234567890
```

---

## Stap 2: Zaptec IDs vinden

**Optie A — Via de Zaptec portal**
1. Ga naar [portal.zaptec.com](https://portal.zaptec.com)
2. Kies je installatie → de URL bevat het `installation_id`
3. Kies je lader → de URL bevat het `charger_id`

**Optie B — Via de API**
```bash
# Vervang EMAIL en WACHTWOORD door je Zaptec-gegevens
TOKEN=$(curl -s https://api.zaptec.com/oauth/token \
  -d "grant_type=password&username=EMAIL&password=WACHTWOORD&scope=openid" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Haal installaties op
curl -s https://api.zaptec.com/api/installation \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -E '"Id"|"Name"'

# Haal laders op
curl -s https://api.zaptec.com/api/chargers \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -E '"Id"|"Name"'
```

---

## Stap 3: Configuratie aanpassen

```bash
# Credentials instellen
nano config/.env

# Instellingen aanpassen
nano config/config.yaml
```

### config/.env

```dotenv
ZAPTEC_USERNAME=jouw@email.nl
ZAPTEC_PASSWORD=jouwwachtwoord
HOMEWIZARD_TOKEN=ABCDEF1234567890ABCDEF1234567890
```

### config/config.yaml — Instellingen uitleg

| Instelling | Standaard | Uitleg |
|---|---|---|
| `homewizard.ip` | `192.168.1.50` | IP-adres van de P1 Meter |
| `homewizard.poll_interval_s` | `10` | Hoe vaak de meter uitgelezen wordt (seconden) |
| `zaptec.installation_id` | — | Zaptec installatie-ID (GUID) |
| `zaptec.charger_id` | — | Zaptec lader-ID (GUID) |
| `zaptec.update_interval_s` | `300` | Hoe vaak Zaptec bijgewerkt wordt (min. 300s aanbevolen) |
| `zaptec.state_poll_interval_s` | `60` | Hoe vaak lader-status gecheckt wordt |
| `laadregeling.fase_modus` | `"auto"` | `"auto"`, `"1"` of `"3"` |
| `laadregeling.spanning_v` | `230` | Spanning per fase in Volt |
| `laadregeling.min_stroom_a` | `6` | Minimum laadstroom (IEC 61851: minimaal 6A) |
| `laadregeling.max_stroom_a` | `25` | Maximum laadstroom / groepsbeveiliging |
| `laadregeling.veiligheidsbuffer_w` | `0` | Buffer in Watt (0 = maximaal laden) |
| `laadregeling.fase_wissel_wachttijd_s` | `900` | Minimale tijd tussen twee fasewijzigingen |
| `laadregeling.fase_wissel_hysterese_w` | `200` | Extra surplus nodig voor upgrade naar 3-fase |
| `web.poort` | `5000` | Poort voor de webinterface |
| `opslag.log_niveau` | `"INFO"` | Logging detail: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## Service beheren

```bash
# Status bekijken
sudo systemctl status solarcharge

# Live logs bekijken (afsluiten met Ctrl+C)
journalctl -u solarcharge -f

# Alleen fouten tonen
journalctl -u solarcharge -p err

# Herstart na config-aanpassing
sudo systemctl restart solarcharge

# Stoppen
sudo systemctl stop solarcharge

# Starten
sudo systemctl start solarcharge
```

---

## Webinterface

De webinterface is bereikbaar op `http://<IP van je Pi>:5000`

- **Dashboard** — Huidig netvermogen, laadstroom, status en recente metingen
- **Instellingen** (`/instellingen`) — Alle config.yaml instellingen live aanpassen
- **Debug** (`/debug`) — Recente logregels en gebeurtenissen voor diagnose

---

## Fase-wisseling (automatische modus)

Met `fase_modus: "auto"` wisselt Solarcharge automatisch tussen 1-fase en 3-fase laden:

| Beschikbaar surplus | Actie |
|---|---|
| ≥ 6A × 230V × 3 = **4140 W** + hysterese | Upgrade naar 3-fase |
| ≥ 6A × 230V = **1380 W** | Gebruik 1-fase |
| < 1380 W | Houd vast aan minimum (6A, 1-fase) |

**Bescherming:**
- Minimaal 15 minuten (instelbaar) tussen fasewijzigingen
- Hysteresisband: 200W extra surplus nodig voor upgrade (instelbaar)
- Mechanisch limiet Zaptec Go 2: 20 schakeloperaties per laadsessie

---

## Troubleshooting

### "HomeWizard token ongeldig (HTTP 401)"
Haal een nieuw token op (zie Stap 1) en update `HOMEWIZARD_TOKEN` in `config/.env`.
Herstart daarna de service.

### "Zaptec login mislukt: HTTP 400"
Controleer `ZAPTEC_USERNAME` en `ZAPTEC_PASSWORD` in `config/.env`.

### "resource niet gevonden (HTTP 404)"
Controleer `installation_id` en `charger_id` in `config/config.yaml` (zie Stap 2).

### "HomeWizard stuurt geen 'power_w' waarde"
De slimme meter stuurt dit veld niet — controleer of de P1-kabel goed aangesloten is
en of de slimme meter DSMR 5.0 ondersteunt.

### Service start niet na herstart Pi
```bash
sudo systemctl status solarcharge
journalctl -u solarcharge -n 50
```
Controleer of het netwerk beschikbaar is voordat de service start.

### Debug-modus inschakelen
Zet in `config/config.yaml`:
```yaml
opslag:
  log_niveau: "DEBUG"
```
Dan worden alle API-aanroepen gelogd. Herstart de service.

---

## Projectstructuur

```
solarcharge/
├── main.py              — Hoofdprogramma en regelaar-lus
├── src/
│   ├── homewizard.py    — HomeWizard P1 API client
│   ├── zaptec.py        — Zaptec API client
│   ├── controller.py    — Laadregelingsalgoritme
│   ├── database.py      — SQLite data logging
│   └── web.py           — Flask webinterface
├── templates/           — HTML-templates voor de webinterface
├── config/
│   ├── config.yaml      — Instellingen (aanpassen via SSH of webinterface)
│   └── .env             — Credentials (alleen via SSH aanpassen)
├── data/                — SQLite database (automatisch aangemaakt)
├── logs/                — Logbestanden (automatisch aangemaakt)
└── setup.sh             — Installatiescript
```
