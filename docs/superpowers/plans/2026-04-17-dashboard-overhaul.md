# Dashboard Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Volledige visuele en structurele overhaul van de webinterface naar een professionele SaaS-stijl met donker groen/zwart thema, inklapbare sidebar, Chart.js grafieken en Tailwind + Alpine.js.

**Architecture:** Alle templates erven van een nieuwe `templates/base.html` met CDN-imports (Tailwind, Alpine.js, Chart.js, Lucide) en de sidebar. De Python backend krijgt twee nieuwe database-functies, twee nieuwe API-endpoints, een quick-settings endpoint, en gesplitste routes voor Instellingen (3 sub-pagina's) en Systeem (2 sub-pagina's). Frontend-logica verhuist naar `static/js/dashboard.js` en `static/js/sessies.js`.

**Tech Stack:** Python 3 / Flask / Jinja2 · Tailwind CSS Play CDN · Alpine.js CDN · Chart.js CDN · Lucide Icons CDN · SQLite

---

## Bestandsstructuur

**Nieuw:**
- `templates/base.html` — Sidebar + CDN-imports + CSS-variabelen
- `templates/laadregeling.html` — Instellingen sub-pagina: algoritme, stroom, fasen
- `templates/apparaten.html` — Instellingen sub-pagina: HomeWizard + Zaptec verbindingen
- `templates/interface.html` — Instellingen sub-pagina: poort, logging, retentie
- `templates/updates.html` — Systeem sub-pagina: versie + branch (uit beheer.html)
- `templates/backups.html` — Systeem sub-pagina: backup + rollback (uit beheer.html)
- `static/js/dashboard.js` — Chart.js, polling, sparklines, tijdselectie, toggle
- `static/js/sessies.js` — Uitklapbare rijen + mini-grafiek per sessie

**Herschreven:**
- `templates/index.html` — Dashboard: 3 KPI-kaarten, controls strip, grafiek
- `templates/sessies.html` — Sessies-tabel met uitklapbare rijen
- `templates/debug.html` — Diagnostics, erft base.html

**Aangepast:**
- `src/database.py` — +`haal_metingen_tijdvenster()` +`haal_sessie_metingen()`
- `src/web.py` — Nieuwe routes, 2 nieuwe API-endpoints, quick-settings endpoint, `ema_net_vermogen_w` in api_status

**Vervalt (functionaliteit gesplitst):**
- `templates/instellingen.html` → gesplitst in laadregeling/apparaten/interface
- `templates/beheer.html` → gesplitst in updates/backups

---

## Task 1: Database — twee nieuwe query-functies

**Files:**
- Modify: `src/database.py`
- Test: `tests/test_database_nieuw.py` (nieuw bestand)

- [ ] **Stap 1: Schrijf de falende tests**

Maak `tests/test_database_nieuw.py`:

```python
import os
import sqlite3
import tempfile
import pytest
from src.database import init_database, sla_meting_op, sla_event_op, start_sessie, sluit_sessie
from src.database import haal_metingen_tijdvenster, haal_sessie_metingen


@pytest.fixture
def db():
    fd, pad = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_database(pad)
    yield pad
    os.unlink(pad)


def test_haal_metingen_tijdvenster_leeg(db):
    result = haal_metingen_tijdvenster(db, minuten=30)
    assert result == []


def test_haal_metingen_tijdvenster_geeft_rijen(db):
    sla_meting_op(db, net_vermogen_w=-200.0, auto_aangesloten=True,
                  gesteld_stroom_a=11.0, huidige_fasen=1, controller_actief=True)
    result = haal_metingen_tijdvenster(db, minuten=5)
    assert len(result) == 1
    assert "tijdstip" in result[0]
    assert "net_vermogen_w" in result[0]
    assert "gesteld_stroom_a" in result[0]
    assert "huidige_fasen" in result[0]


def test_haal_sessie_metingen_onbekende_sessie(db):
    result = haal_sessie_metingen(db, sessie_id=9999)
    assert result["metingen"] == []
    assert result["events"] == []


def test_haal_sessie_metingen_geeft_data(db):
    sessie_id = start_sessie(db, model="solarflow")
    sla_meting_op(db, net_vermogen_w=-150.0, auto_aangesloten=True,
                  gesteld_stroom_a=8.0, huidige_fasen=1, controller_actief=True)
    sla_event_op(db, event_type="fase_wissel", details="1->3")
    sluit_sessie(db, sessie_id, {
        "duur_s": 3600, "no_import_count": 0, "no_export_count": 1,
        "fase_wissel_count": 1, "gem_score": 85.0, "gem_afwijking_w": 90.0,
        "geladen_kwh": 5.2,
    })
    result = haal_sessie_metingen(db, sessie_id=sessie_id)
    assert len(result["metingen"]) >= 1
    assert len(result["events"]) >= 1
```

- [ ] **Stap 2: Draai tests — verwacht FAIL**

```bash
cd /home/pi/zaptec-solarcharge && source venv/bin/activate
python -m pytest tests/test_database_nieuw.py -v
```

Verwacht: `ImportError: cannot import name 'haal_metingen_tijdvenster'`

- [ ] **Stap 3: Voeg functies toe aan `src/database.py`**

Voeg toe na de bestaande `haal_recente_events_op` functie (na regel ~225):

```python
def haal_metingen_tijdvenster(db_pad: str, minuten: int = 30) -> list[dict]:
    """
    Haalt metingen op van de afgelopen N minuten, oudste eerst (voor grafieken).

    Args:
        db_pad:  Pad naar het databasebestand.
        minuten: Tijdvenster in minuten.

    Returns:
        Lijst van dicts, oudste meting eerst.
    """
    try:
        with _verbinding(db_pad) as conn:
            rows = conn.execute(
                """
                SELECT tijdstip, net_vermogen_w, auto_aangesloten,
                       gesteld_stroom_a, huidige_fasen, controller_actief
                FROM metingen
                WHERE tijdstip >= datetime('now', ? || ' minutes')
                ORDER BY id ASC
                """,
                (f"-{minuten}",),
            ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        logger.warning("Kon metingen tijdvenster niet ophalen: %s", e)
        return []


def haal_sessie_metingen(db_pad: str, sessie_id: int) -> dict:
    """
    Haalt metingen en events op die vielen tijdens een specifieke sessie.

    Args:
        db_pad:    Pad naar het databasebestand.
        sessie_id: ID van de sessie.

    Returns:
        Dict met 'metingen' (lijst, oudste eerst) en 'events' (lijst, oudste eerst).
        Retourneert lege lijsten als sessie niet bestaat.
    """
    try:
        with _verbinding(db_pad) as conn:
            sessie = conn.execute(
                "SELECT start_tijdstip, eind_tijdstip FROM sessies WHERE id = ?",
                (sessie_id,),
            ).fetchone()
            if not sessie:
                return {"metingen": [], "events": []}
            start, eind = sessie["start_tijdstip"], sessie["eind_tijdstip"]
            metingen = conn.execute(
                """
                SELECT tijdstip, net_vermogen_w, gesteld_stroom_a, huidige_fasen
                FROM metingen
                WHERE tijdstip >= ? AND (? IS NULL OR tijdstip <= ?)
                ORDER BY id ASC
                """,
                (start, eind, eind),
            ).fetchall()
            events = conn.execute(
                """
                SELECT tijdstip, event_type, details
                FROM events
                WHERE tijdstip >= ? AND (? IS NULL OR tijdstip <= ?)
                ORDER BY id ASC
                """,
                (start, eind, eind),
            ).fetchall()
        return {
            "metingen": [dict(r) for r in metingen],
            "events":   [dict(r) for r in events],
        }
    except sqlite3.Error as e:
        logger.warning("Kon sessie-metingen niet ophalen: %s", e)
        return {"metingen": [], "events": []}
```

- [ ] **Stap 4: Draai tests — verwacht PASS**

```bash
python -m pytest tests/test_database_nieuw.py -v
```

Verwacht: alle 4 tests PASS

- [ ] **Stap 5: Syntax check**

```bash
python -m py_compile src/database.py && echo "OK"
```

- [ ] **Stap 6: Commit**

```bash
git add src/database.py tests/test_database_nieuw.py
git commit -m "feat: haal_metingen_tijdvenster + haal_sessie_metingen voor grafiek-endpoints"
```

---

## Task 2: Nieuwe API-endpoints in web.py

**Files:**
- Modify: `src/web.py`

- [ ] **Stap 1: Voeg imports toe bovenaan `src/web.py`**

De bestaande import van `haal_recente_metingen_op` uitbreiden:

```python
from src.database import (
    haal_recente_metingen_op,
    haal_recente_events_op,
    haal_ongeziene_sessie_op,
    markeer_popup_getoond,
    haal_sessies_op,
    haal_metingen_tijdvenster,
    haal_sessie_metingen,
)
```

- [ ] **Stap 2: Voeg `ema_net_vermogen_w` toe aan `api_status` response**

Zoek in `api_status()` de return-statement en voeg toe:

```python
"ema_net_vermogen_w": state.get("ema_net_vermogen_w"),
```

Direct na de regel `"fase_wissel_bezig": state.get("fase_wissel_bezig", False),`

- [ ] **Stap 3: Voeg `/api/metingen` endpoint toe aan `maak_app()`**

Voeg toe in `maak_app()`, na de `api_status` route:

```python
@app.route("/api/metingen")
def api_metingen():
    """JSON-lijst van metingen over een tijdvenster (voor grafiek tijdselectie)."""
    minuten = request.args.get("minuten", 30, type=int)
    minuten = max(5, min(minuten, 180))  # clamp: 5–180 minuten
    return jsonify(haal_metingen_tijdvenster(db_pad, minuten=minuten))
```

- [ ] **Stap 4: Voeg `/api/sessies/<id>/metingen` endpoint toe**

Voeg toe in `maak_app()`, na de `sessie_gezien` route (vóór `return app`):

```python
@app.route("/api/sessies/<int:sessie_id>/metingen")
def api_sessie_metingen(sessie_id):
    """Metingen en events van één sessie, voor de mini-grafiek op de sessies-pagina."""
    return jsonify(haal_sessie_metingen(db_pad, sessie_id=sessie_id))
```

- [ ] **Stap 5: Voeg `/api/quick-settings` endpoint toe**

Voeg toe in `maak_app()`, na de toggle route:

```python
@app.route("/api/quick-settings", methods=["POST"])
def quick_settings():
    """
    Slaat snel drie dashboard-instellingen op zonder paginaverversing.
    Accepteert JSON: {regelaar_model, doelinstelling_preset, huisprofiel}
    """
    data = request.get_json(force=True, silent=True) or {}

    _preset_map = {"50": 50, "0": 0, "-100": -100, "-200": -200}
    _profiel_map = {
        "rustig":  dict(ema_alpha_min=0.08, ema_alpha_max=0.4, ema_adaptief_drempel_w=500),
        "normaal": dict(ema_alpha_min=0.10, ema_alpha_max=0.6, ema_adaptief_drempel_w=400),
        "druk":    dict(ema_alpha_min=0.10, ema_alpha_max=0.7, ema_adaptief_drempel_w=350),
    }

    with config_lock:
        model = data.get("regelaar_model", "").strip()
        if model in ("legacy", "solarflow"):
            config["laadregeling"]["regelaar_model"] = model

        preset = data.get("doelinstelling_preset", "").strip()
        if preset in _preset_map:
            config["laadregeling"]["doel_net_vermogen_w"] = _preset_map[preset]

        profiel = data.get("huisprofiel", "").strip()
        if profiel in _profiel_map:
            config["laadregeling"]["huisprofiel"] = profiel
            for k, v in _profiel_map[profiel].items():
                config["laadregeling"][k] = v

    _schrijf_config(config)
    logger.info("Quick-settings bijgewerkt via dashboard: %s", data)
    return jsonify({"ok": True})
```

- [ ] **Stap 6: Syntax check**

```bash
python -m py_compile src/web.py && echo "OK"
```

- [ ] **Stap 7: Draai alle tests**

```bash
python -m pytest tests/ -q
```

Verwacht: alle tests PASS

- [ ] **Stap 8: Commit**

```bash
git add src/web.py
git commit -m "feat: /api/metingen, /api/sessies/<id>/metingen, /api/quick-settings, ema in api_status"
```

---

## Task 3: Gesplitste instellingen-routes in web.py

**Files:**
- Modify: `src/web.py`

- [ ] **Stap 1: Voeg helperfuncties toe onderaan `src/web.py`** (na `_lees_laatste_logregels`)

```python
def _verwerk_laadregeling(form: dict, config: dict, lock: threading.Lock) -> list[str]:
    """Valideert en slaat laadregeling-instellingen op."""
    fouten = []

    def lees_float(veld, mn, mx):
        v = form.get(veld, "").strip()
        try:
            g = float(v)
            if not (mn <= g <= mx):
                fouten.append(f"{veld}: moet tussen {mn} en {mx} liggen.")
                return None
            return g
        except ValueError:
            fouten.append(f"{veld}: '{v}' is geen geldig getal.")
            return None

    def lees_int(veld, mn, mx):
        v = form.get(veld, "").strip()
        try:
            g = int(v)
            if not (mn <= g <= mx):
                fouten.append(f"{veld}: moet tussen {mn} en {mx} liggen.")
                return None
            return g
        except ValueError:
            fouten.append(f"{veld}: '{v}' is geen geldig geheel getal.")
            return None

    regelaar_model = form.get("regelaar_model", "").strip()
    if regelaar_model not in ("legacy", "solarflow"):
        fouten.append("regelaar_model: moet 'legacy' of 'solarflow' zijn.")
        regelaar_model = None

    fase_modus = form.get("fase_modus", "").strip()
    if fase_modus not in ("auto", "1", "3"):
        fouten.append("fase_modus: moet 'auto', '1' of '3' zijn.")
        fase_modus = None

    spanning_v          = lees_float("spanning_v", 100, 400)
    min_stroom_a        = lees_float("min_stroom_a", 6, 32)
    max_stroom_a        = lees_float("max_stroom_a", 6, 63)
    veiligheidsbuffer_w = lees_float("veiligheidsbuffer_w", 0, 10000)
    fase_wissel_wacht   = lees_int("fase_wissel_wachttijd_s", 60, 3600)
    fase_wissel_hyst    = lees_float("fase_wissel_hysterese_w", 0, 5000)
    fase_bevestig_wacht = lees_int("fase_wissel_bevestig_wacht_s", 30, 600)
    noodoverride_actief         = "noodoverride_actief" in form
    noodoverride_drempel        = lees_float("noodoverride_drempel_w", 0, 100000)
    noodoverride_wacht          = lees_int("noodoverride_wachttijd_s", 10, 3600)
    noodoverride_export_drempel = lees_float("noodoverride_export_drempel_w", -100000, -1)

    _preset_map = {"50": 50, "0": 0, "-100": -100, "-200": -200}
    doelinstelling_preset = form.get("doelinstelling_preset", "").strip()
    if doelinstelling_preset in _preset_map:
        doel_net_vermogen_w = _preset_map[doelinstelling_preset]
    elif doelinstelling_preset == "aangepast":
        doel_net_vermogen_w = lees_int("doel_net_vermogen_w_geavanceerd", -500, 300)
    else:
        doel_net_vermogen_w = None

    _profiel_map = {
        "rustig":  dict(ema_alpha_min=0.08, ema_alpha_max=0.4, ema_adaptief_drempel_w=500),
        "normaal": dict(ema_alpha_min=0.10, ema_alpha_max=0.6, ema_adaptief_drempel_w=400),
        "druk":    dict(ema_alpha_min=0.10, ema_alpha_max=0.7, ema_adaptief_drempel_w=350),
    }
    huisprofiel = form.get("huisprofiel", "").strip()
    if huisprofiel and huisprofiel not in _profiel_map:
        fouten.append("huisprofiel: moet 'rustig', 'normaal' of 'druk' zijn.")
        huisprofiel = None

    if min_stroom_a is not None and max_stroom_a is not None:
        if min_stroom_a > max_stroom_a:
            fouten.append("min_stroom_a mag niet groter zijn dan max_stroom_a.")

    if fouten:
        return fouten

    with lock:
        if regelaar_model:
            config["laadregeling"]["regelaar_model"] = regelaar_model
        if doel_net_vermogen_w is not None:
            config["laadregeling"]["doel_net_vermogen_w"] = doel_net_vermogen_w
        if huisprofiel in _profiel_map:
            config["laadregeling"]["huisprofiel"] = huisprofiel
            for k, v in _profiel_map[huisprofiel].items():
                config["laadregeling"][k] = v
        else:
            config["laadregeling"]["huisprofiel"] = "aangepast"
            for veld, mn, mx in [("ema_alpha_min",0.01,0.5),("ema_alpha_max",0.1,1.0)]:
                val = lees_float(veld, mn, mx)
                if val is not None:
                    config["laadregeling"][veld] = val
            for veld, mn, mx in [("ema_adaptief_drempel_w",100,2000),("scoring_sigma_w",50,1000)]:
                val = lees_int(veld, mn, mx)
                if val is not None:
                    config["laadregeling"][veld] = val
        if fase_modus:
            config["laadregeling"]["fase_modus"] = fase_modus
        if spanning_v is not None:
            config["laadregeling"]["spanning_v"] = spanning_v
        if min_stroom_a is not None:
            config["laadregeling"]["min_stroom_a"] = min_stroom_a
        if max_stroom_a is not None:
            config["laadregeling"]["max_stroom_a"] = max_stroom_a
        if veiligheidsbuffer_w is not None:
            config["laadregeling"]["veiligheidsbuffer_w"] = veiligheidsbuffer_w
        if fase_wissel_wacht is not None:
            config["laadregeling"]["fase_wissel_wachttijd_s"] = fase_wissel_wacht
        if fase_wissel_hyst is not None:
            config["laadregeling"]["fase_wissel_hysterese_w"] = fase_wissel_hyst
        if fase_bevestig_wacht is not None:
            config["zaptec"]["fase_wissel_bevestig_wacht_s"] = fase_bevestig_wacht
        config["laadregeling"]["noodoverride_actief"] = noodoverride_actief
        if noodoverride_drempel is not None:
            config["laadregeling"]["noodoverride_drempel_w"] = noodoverride_drempel
        if noodoverride_wacht is not None:
            config["laadregeling"]["noodoverride_wachttijd_s"] = noodoverride_wacht
        if noodoverride_export_drempel is not None:
            config["laadregeling"]["noodoverride_export_drempel_w"] = noodoverride_export_drempel
    return []


def _verwerk_apparaten(form: dict, config: dict, lock: threading.Lock) -> list[str]:
    """Valideert en slaat apparaten-instellingen op."""
    fouten = []

    def lees_int(veld, mn, mx):
        v = form.get(veld, "").strip()
        try:
            g = int(v)
            if not (mn <= g <= mx):
                fouten.append(f"{veld}: moet tussen {mn} en {mx} liggen.")
                return None
            return g
        except ValueError:
            fouten.append(f"{veld}: '{v}' is geen geldig geheel getal.")
            return None

    hw_ip             = form.get("hw_ip", "").strip() or None
    hw_poll_s         = lees_int("homewizard_poll_interval_s", 5, 300)
    zaptec_install_id = form.get("zaptec_installation_id", "").strip() or None
    zaptec_charger_id = form.get("zaptec_charger_id", "").strip() or None
    update_interval_s = lees_int("update_interval_s", 60, 3600)
    state_poll_s      = lees_int("state_poll_interval_s", 10, 300)
    live_stroom_bron  = form.get("live_stroom_bron", "").strip()
    if live_stroom_bron not in ("auto", "708", "meting", "uit"):
        fouten.append("live_stroom_bron: moet 'auto', '708', 'meting' of 'uit' zijn.")
        live_stroom_bron = None

    if fouten:
        return fouten

    with lock:
        if hw_ip:
            config["homewizard"]["ip"] = hw_ip
        if hw_poll_s is not None:
            config["homewizard"]["poll_interval_s"] = hw_poll_s
        if zaptec_install_id:
            config["zaptec"]["installation_id"] = zaptec_install_id
        if zaptec_charger_id:
            config["zaptec"]["charger_id"] = zaptec_charger_id
        if update_interval_s is not None:
            config["zaptec"]["update_interval_s"] = update_interval_s
        if state_poll_s is not None:
            config["zaptec"]["state_poll_interval_s"] = state_poll_s
        if live_stroom_bron is not None:
            config["zaptec"]["live_stroom_bron"] = live_stroom_bron
    return []


def _verwerk_interface(form: dict, config: dict, lock: threading.Lock) -> list[str]:
    """Valideert en slaat interface/opslag-instellingen op."""
    fouten = []

    def lees_int(veld, mn, mx):
        v = form.get(veld, "").strip()
        try:
            g = int(v)
            if not (mn <= g <= mx):
                fouten.append(f"{veld}: moet tussen {mn} en {mx} liggen.")
                return None
            return g
        except ValueError:
            fouten.append(f"{veld}: '{v}' is geen geldig geheel getal.")
            return None

    web_poort           = lees_int("web_poort", 1024, 65535)
    bewaarperiode_dagen = lees_int("bewaarperiode_dagen", 7, 365)
    debug_modus         = "debug_modus" in form
    log_niveau          = form.get("log_niveau", "").strip().upper()
    if log_niveau not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        fouten.append("log_niveau: moet DEBUG, INFO, WARNING of ERROR zijn.")
        log_niveau = None

    if fouten:
        return fouten

    with lock:
        if web_poort is not None:
            config["web"]["poort"] = web_poort
        if bewaarperiode_dagen is not None:
            config["opslag"]["bewaarperiode_dagen"] = bewaarperiode_dagen
        config["opslag"]["debug_modus"] = debug_modus
        if log_niveau:
            config["opslag"]["log_niveau"] = log_niveau
            logging.getLogger().setLevel(getattr(logging, log_niveau))
    return []
```

- [ ] **Stap 2: Voeg de nieuwe routes toe in `maak_app()`**, direct na de bestaande `/instellingen` route:

```python
    @app.route("/laadregeling", methods=["GET", "POST"])
    def laadregeling():
        fouten = []
        if request.method == "POST":
            fouten = _verwerk_laadregeling(request.form, config, config_lock)
            if not fouten:
                _schrijf_config(config)
                logger.info("Laadregeling opgeslagen via webinterface.")
                return redirect(url_for("index"))
        return render_template("laadregeling.html", config=config, fouten=fouten)

    @app.route("/apparaten", methods=["GET", "POST"])
    def apparaten():
        fouten = []
        if request.method == "POST":
            fouten = _verwerk_apparaten(request.form, config, config_lock)
            if not fouten:
                _schrijf_config(config)
                logger.info("Apparaten opgeslagen via webinterface.")
                return redirect(url_for("index"))
        return render_template("apparaten.html", config=config, fouten=fouten)

    @app.route("/interface", methods=["GET", "POST"])
    def interface():
        fouten = []
        if request.method == "POST":
            fouten = _verwerk_interface(request.form, config, config_lock)
            if not fouten:
                _schrijf_config(config)
                logger.info("Interface opgeslagen via webinterface.")
                return redirect(url_for("index"))
        return render_template("interface.html", config=config, fouten=fouten)

    @app.route("/updates")
    def updates():
        return render_template("updates.html", state=state, config=config)

    @app.route("/backups")
    def backups_pagina():
        return render_template("backups.html", state=state, config=config)
```

- [ ] **Stap 3: Syntax check + tests**

```bash
python -m py_compile src/web.py && echo "OK"
python -m pytest tests/ -q
```

- [ ] **Stap 4: Commit**

```bash
git add src/web.py
git commit -m "feat: gesplitste routes laadregeling/apparaten/interface/updates/backups"
```

---

## Task 4: base.html — fundament voor alle pagina's

**Files:**
- Create: `templates/base.html`

- [ ] **Stap 1: Maak `templates/base.html`**

```html
<!DOCTYPE html>
<html lang="nl" x-data="sidebarState()" :class="{ 'sidebar-open': open }">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}Zaptec Solarcharge{% endblock %}</title>

  <!-- Tailwind Play CDN -->
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      theme: {
        extend: {
          colors: {
            base:    '#0d1117',
            surface: '#111827',
            subtle:  '#0a0f16',
            border:  '#1f2937',
            accent:  '#10b981',
          }
        }
      }
    }
  </script>

  <!-- Alpine.js -->
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>

  <!-- Chart.js -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.x/dist/chart.umd.min.js"></script>

  <!-- Lucide Icons -->
  <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>

  <style>
    :root {
      --bg-base:     #0d1117;
      --bg-surface:  #111827;
      --bg-subtle:   #0a0f16;
      --border:      #1f2937;
      --border-green:#1a3a2a;
      --accent:      #10b981;
      --accent-dim:  rgba(16,185,129,0.15);
      --text-primary:#f9fafb;
      --text-secondary:#9ca3af;
      --text-muted:  #4b5563;
      --green:       #10b981;
      --red:         #ef4444;
      --orange:      #f97316;
      --blue:        #60a5fa;
      --purple:      #a78bfa;
    }
    [x-cloak] { display: none !important; }
    body { background: var(--bg-base); color: var(--text-primary); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    .sidebar { transition: width 0.25s ease; }
    .sidebar-label { transition: opacity 0.2s ease, width 0.2s ease; }
    .kpi-card:hover { border-color: rgba(16,185,129,0.4); }
  </style>

  {% block head %}{% endblock %}
</head>
<body class="flex min-h-screen">

<!-- ── Sidebar ─────────────────────────────────────────────────────────── -->
<nav class="sidebar flex flex-col flex-shrink-0 bg-[var(--bg-subtle)] border-r border-[var(--border)]"
     :style="open ? 'width:200px' : 'width:52px'"
     x-cloak>

  <!-- Logo + inklapknop -->
  <div class="flex items-center px-3 py-4 gap-2 mb-1">
    <div class="w-8 h-8 rounded-lg flex-shrink-0 flex items-center justify-center"
         style="background:linear-gradient(135deg,#10b981,#059669);box-shadow:0 0 12px rgba(16,185,129,.3);">
      <i data-lucide="zap" class="w-4 h-4 text-white"></i>
    </div>
    <span x-show="open" x-cloak class="sidebar-label text-sm font-semibold text-emerald-100 whitespace-nowrap overflow-hidden">Zaptec Solar</span>
    <button @click="toggle()" class="ml-auto p-1 rounded text-[var(--text-muted)] hover:text-[var(--text-secondary)]" x-show="open" x-cloak>
      <i data-lucide="chevron-left" class="w-4 h-4"></i>
    </button>
  </div>

  <!-- Uitklapknop als ingeklapt -->
  <button x-show="!open" @click="toggle()"
          class="mx-auto mb-2 p-1 rounded text-[var(--text-muted)] hover:text-[var(--text-secondary)]">
    <i data-lucide="chevron-right" class="w-4 h-4"></i>
  </button>

  <!-- Dashboard -->
  <a href="/" class="nav-item flex items-center gap-2 mx-2 px-2 py-2 rounded-lg transition-colors
     {% if request.path == '/' %}bg-[var(--accent-dim)] text-[var(--accent)]{% else %}text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-white/5{% endif %}">
    <i data-lucide="layout-dashboard" class="w-4 h-4 flex-shrink-0"></i>
    <span x-show="open" x-cloak class="sidebar-label text-sm font-medium whitespace-nowrap overflow-hidden">Dashboard</span>
  </a>

  <!-- Sessies -->
  <a href="/sessies" class="nav-item flex items-center gap-2 mx-2 px-2 py-2 rounded-lg transition-colors
     {% if request.path == '/sessies' %}bg-[var(--accent-dim)] text-[var(--accent)]{% else %}text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-white/5{% endif %}">
    <i data-lucide="activity" class="w-4 h-4 flex-shrink-0"></i>
    <span x-show="open" x-cloak class="sidebar-label text-sm font-medium whitespace-nowrap overflow-hidden">Sessies</span>
  </a>

  <div class="mx-3 my-2 border-t border-[var(--border)]"></div>

  <!-- Instellingen accordion -->
  <div x-data="{ instellingenOpen: {{ 'true' if request.path in ['/laadregeling','/apparaten','/interface'] else 'false' }} }">
    <button @click="instellingenOpen = !instellingenOpen; if(!open) { $store.sidebar.open = true; }"
            class="nav-item w-full flex items-center gap-2 mx-2 px-2 py-2 rounded-lg transition-colors text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-white/5"
            style="width:calc(100% - 1rem)">
      <i data-lucide="settings-2" class="w-4 h-4 flex-shrink-0"></i>
      <span x-show="open" x-cloak class="sidebar-label text-sm font-medium flex-1 text-left whitespace-nowrap overflow-hidden">Instellingen</span>
      <i x-show="open" x-cloak :data-lucide="instellingenOpen ? 'chevron-down' : 'chevron-right'" class="w-3 h-3 flex-shrink-0"></i>
    </button>
    <div x-show="open && instellingenOpen" x-cloak
         class="ml-6 border-l border-[var(--border-green)] pl-2 flex flex-col gap-0.5">
      <a href="/laadregeling" class="flex items-center gap-2 px-2 py-1.5 rounded-md text-xs transition-colors
         {% if request.path == '/laadregeling' %}text-[var(--accent)]{% else %}text-[var(--text-muted)] hover:text-[var(--text-secondary)]{% endif %}">
        <i data-lucide="zap" class="w-3 h-3 flex-shrink-0"></i> Laadregeling
      </a>
      <a href="/apparaten" class="flex items-center gap-2 px-2 py-1.5 rounded-md text-xs transition-colors
         {% if request.path == '/apparaten' %}text-[var(--accent)]{% else %}text-[var(--text-muted)] hover:text-[var(--text-secondary)]{% endif %}">
        <i data-lucide="monitor" class="w-3 h-3 flex-shrink-0"></i> Apparaten
      </a>
      <a href="/interface" class="flex items-center gap-2 px-2 py-1.5 rounded-md text-xs transition-colors
         {% if request.path == '/interface' %}text-[var(--accent)]{% else %}text-[var(--text-muted)] hover:text-[var(--text-secondary)]{% endif %}">
        <i data-lucide="sliders-horizontal" class="w-3 h-3 flex-shrink-0"></i> Interface
      </a>
    </div>
  </div>

  <!-- Systeem accordion -->
  <div x-data="{ systeemOpen: {{ 'true' if request.path in ['/updates','/backups'] else 'false' }} }">
    <button @click="systeemOpen = !systeemOpen; if(!open) { open = true; }"
            class="nav-item w-full flex items-center gap-2 mx-2 px-2 py-2 rounded-lg transition-colors text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-white/5"
            style="width:calc(100% - 1rem)">
      <i data-lucide="server" class="w-4 h-4 flex-shrink-0"></i>
      <span x-show="open" x-cloak class="sidebar-label text-sm font-medium flex-1 text-left whitespace-nowrap overflow-hidden">Systeem</span>
      <i x-show="open" x-cloak :data-lucide="systeemOpen ? 'chevron-down' : 'chevron-right'" class="w-3 h-3 flex-shrink-0"></i>
    </button>
    <div x-show="open && systeemOpen" x-cloak
         class="ml-6 border-l border-[var(--border-green)] pl-2 flex flex-col gap-0.5">
      <a href="/updates" class="flex items-center gap-2 px-2 py-1.5 rounded-md text-xs transition-colors
         {% if request.path == '/updates' %}text-[var(--accent)]{% else %}text-[var(--text-muted)] hover:text-[var(--text-secondary)]{% endif %}">
        <i data-lucide="refresh-cw" class="w-3 h-3 flex-shrink-0"></i> Updates
      </a>
      <a href="/backups" class="flex items-center gap-2 px-2 py-1.5 rounded-md text-xs transition-colors
         {% if request.path == '/backups' %}text-[var(--accent)]{% else %}text-[var(--text-muted)] hover:text-[var(--text-secondary)]{% endif %}">
        <i data-lucide="hard-drive" class="w-3 h-3 flex-shrink-0"></i> Backups
      </a>
    </div>
  </div>

  <!-- Diagnostics (onderin, alleen als debug_modus aan) -->
  {% if config.opslag.debug_modus %}
  <div class="mt-auto mb-2">
    <div class="mx-3 my-2 border-t border-[var(--border)]"></div>
    <a href="/debug" class="nav-item flex items-center gap-2 mx-2 px-2 py-2 rounded-lg transition-colors
       {% if request.path == '/debug' %}bg-[var(--accent-dim)] text-[var(--accent)]{% else %}text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-white/5{% endif %}">
      <i data-lucide="wrench" class="w-4 h-4 flex-shrink-0"></i>
      <span x-show="open" x-cloak class="sidebar-label text-sm whitespace-nowrap overflow-hidden">Diagnostics</span>
    </a>
  </div>
  {% endif %}
</nav>

<!-- ── Hoofdinhoud ────────────────────────────────────────────────────── -->
<main class="flex-1 overflow-y-auto p-6">
  {% block content %}{% endblock %}
</main>

<script>
  function sidebarState() {
    return {
      open: localStorage.getItem('sidebar-open') === 'true',
      toggle() {
        this.open = !this.open;
        localStorage.setItem('sidebar-open', this.open);
        // Herrender Lucide-iconen na sidebar-animatie
        setTimeout(() => lucide.createIcons(), 260);
      }
    }
  }
  // Initialiseer Lucide iconen na DOM-load
  document.addEventListener('DOMContentLoaded', () => lucide.createIcons());
</script>

{% block scripts %}{% endblock %}
</body>
</html>
```

- [ ] **Stap 2: Controleer dat Flask de template vindt**

Start de server kort handmatig (of test met py_compile):
```bash
python -m py_compile src/web.py && echo "OK"
```

- [ ] **Stap 3: Commit**

```bash
git add templates/base.html
git commit -m "feat: base.html met Tailwind, Alpine, Chart.js, Lucide, inklapbare sidebar"
```

---

## Task 5: index.html — Dashboard

**Files:**
- Rewrite: `templates/index.html`

- [ ] **Stap 1: Herschrijf `templates/index.html`**

```html
{% extends "base.html" %}
{% block title %}Dashboard — Zaptec Solarcharge{% endblock %}

{% block content %}
<!-- Paginaheader -->
<div class="flex items-center justify-between mb-6">
  <h1 class="text-base font-semibold text-[var(--text-primary)]">Dashboard</h1>
  <div id="status-badge" class="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs
       bg-[var(--accent-dim)] border border-[var(--border-green)]">
    <span id="status-dot" class="w-2 h-2 rounded-full bg-[var(--accent)] animate-pulse"></span>
    <span id="status-text" class="font-semibold text-[var(--accent)]">{{ state.laadmodus or 'Onbekend' }}</span>
    <span id="status-meta" class="text-[var(--text-muted)]">
      · {{ config.laadregeling.regelaar_model|capitalize }}
      · {{ state.huidige_fasen }}-fase
    </span>
  </div>
</div>

<!-- 3 KPI-kaarten -->
<div class="grid grid-cols-3 gap-4 mb-4">

  <!-- P1 Meter -->
  <div class="kpi-card relative overflow-hidden rounded-xl p-4 border border-[var(--border)]"
       style="background:var(--bg-surface);">
    <div class="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-3">P1 Meter</div>
    <div id="p1-waarde" class="text-3xl font-bold leading-none"
         style="color:var(--green);">
      {{ state.net_vermogen_w|int }} W
    </div>
    <div class="mt-2 flex items-center gap-2 text-xs text-[var(--text-muted)]">
      <span id="p1-badge" class="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-[var(--accent-dim)] text-[var(--accent)]">
        <i data-lucide="arrow-up" class="w-2.5 h-2.5"></i> exporterend
      </span>
      <span id="p1-sub">terug naar net</span>
    </div>
    <!-- Sparkline -->
    <canvas id="sparkline-p1" class="absolute bottom-0 right-0 w-28 h-14 opacity-50"></canvas>
  </div>

  <!-- Laadstroom (breder) -->
  <div class="kpi-card relative rounded-xl p-4 border"
       style="background:var(--bg-surface);border-color:var(--border-green);">
    <div class="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-3">Laadstroom</div>
    <div class="flex items-center gap-4">
      <!-- Cirkel gauge -->
      <div class="relative flex-shrink-0 w-20 h-20">
        <svg class="w-20 h-20" viewBox="0 0 100 100">
          <circle cx="50" cy="50" r="40" fill="none" stroke="var(--border-green)" stroke-width="9"/>
          <circle id="gauge-ring" cx="50" cy="50" r="40" fill="none" stroke="var(--accent)" stroke-width="9"
            stroke-dasharray="251.2" stroke-dashoffset="200"
            stroke-linecap="round" transform="rotate(-90 50 50)"
            style="filter:drop-shadow(0 0 5px var(--accent));transition:stroke-dashoffset 0.5s ease;"/>
        </svg>
        <div class="absolute inset-0 flex flex-col items-center justify-center">
          <span id="gauge-waarde" class="text-lg font-bold leading-none" style="color:var(--accent);">
            {{ state.huidig_stroom_a|int }}A
          </span>
          <span id="gauge-max" class="text-[9px]" style="color:var(--text-muted);">
            /{{ config.laadregeling.max_stroom_a|int }}A
          </span>
        </div>
      </div>
      <!-- Fase-balken -->
      <div class="flex gap-2 items-end flex-1 h-16">
        {% for fase in [1, 2, 3] %}
        {% set actief = (state.huidige_fasen == 3) or (fase == 1) %}
        <div class="flex flex-col items-center gap-1 flex-1 fase-kolom" data-fase="{{ fase }}">
          <span class="fase-val text-[11px] font-semibold {{ 'text-[var(--accent)]' if actief else 'text-[var(--border)]' }}">
            {{ state.huidig_stroom_a|int if actief else 0 }}A
          </span>
          <div class="w-full rounded-md overflow-hidden h-10"
               style="background:{{ 'var(--border-green)' if actief else '#161b22' }};">
            <div class="fase-fill w-full rounded-md transition-all duration-500"
                 style="height:{{ ((state.huidig_stroom_a / config.laadregeling.max_stroom_a) * 100)|int if actief else 0 }}%;
                        background:{{ 'var(--accent)' if actief else 'transparent' }};
                        box-shadow:{{ '0 0 6px var(--accent)' if actief else 'none' }};
                        margin-top:auto;"></div>
          </div>
          <span class="text-[10px]" style="color:{{ 'var(--text-muted)' if actief else 'var(--border)' }}">L{{ fase }}</span>
        </div>
        {% endfor %}
      </div>
    </div>
  </div>

  <!-- Vermogenstrend -->
  <div class="kpi-card relative overflow-hidden rounded-xl p-4 border border-[var(--border)]"
       style="background:var(--bg-surface);">
    <div class="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-3">Vermogenstrend</div>
    <div id="trend-waarde" class="text-3xl font-bold leading-none" style="color:var(--purple);">
      {{ state.ema_net_vermogen_w|int if state.ema_net_vermogen_w else '—' }} W
    </div>
    <div class="mt-2 flex items-center gap-2 text-xs text-[var(--text-muted)]">
      <span id="trend-badge" class="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-[#1a1f2a] text-[var(--text-muted)]">
        <i data-lucide="minus" class="w-2.5 h-2.5"></i> stabiel
      </span>
      <span>gewogen gemiddelde</span>
    </div>
    <canvas id="sparkline-trend" class="absolute bottom-0 right-0 w-28 h-14 opacity-50"></canvas>
  </div>

</div>

<!-- Controls strip -->
<div class="rounded-xl border border-[var(--border)] px-5 py-3 mb-4 flex items-center gap-5 flex-wrap"
     style="background:var(--bg-surface);">

  <!-- Aan/uit knop -->
  <div class="flex flex-col gap-1">
    <span class="text-[10px] uppercase tracking-wider font-medium text-[var(--text-muted)]">Regeling</span>
    <form action="/toggle" method="post">
      <button type="submit"
              class="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors
                     {{ 'bg-[var(--accent-dim)] border border-[rgba(16,185,129,.4)] text-[var(--accent)]' if state.actief else 'bg-[rgba(239,68,68,.1)] border border-[rgba(239,68,68,.4)] text-[var(--red)]' }}">
        <i data-lucide="{{ 'zap' if state.actief else 'zap-off' }}" class="w-3 h-3"></i>
        {{ 'Actief' if state.actief else 'Uitgeschakeld' }}
      </button>
    </form>
  </div>

  <div class="w-px h-9 bg-[var(--border)]"></div>

  <!-- Algoritme -->
  <div class="flex flex-col gap-1">
    <span class="text-[10px] uppercase tracking-wider font-medium text-[var(--text-muted)]">Algoritme</span>
    <select id="ctrl-model" onchange="quickSetting('regelaar_model', this.value)"
            class="bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-1.5 text-xs text-[var(--text-primary)] appearance-none cursor-pointer min-w-[130px]">
      <option value="solarflow" {{ 'selected' if config.laadregeling.regelaar_model == 'solarflow' }}>SolarFlow v1</option>
      <option value="legacy" {{ 'selected' if config.laadregeling.regelaar_model == 'legacy' }}>Legacy</option>
    </select>
  </div>

  <!-- Doel netvermogen -->
  <div class="flex flex-col gap-1">
    <span class="text-[10px] uppercase tracking-wider font-medium text-[var(--text-muted)]">Doel netvermogen</span>
    <select id="ctrl-doel" onchange="quickSetting('doelinstelling_preset', this.value)"
            class="bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-1.5 text-xs text-[var(--text-primary)] appearance-none cursor-pointer min-w-[160px]">
      <option value="50" {{ 'selected' if config.laadregeling.doel_net_vermogen_w == 50 }}>+50 W — Veiligheidsmarge</option>
      <option value="0" {{ 'selected' if config.laadregeling.doel_net_vermogen_w == 0 }}>0 W — Neutraal</option>
      <option value="-100" {{ 'selected' if config.laadregeling.doel_net_vermogen_w == -100 }}>−100 W — Licht export</option>
      <option value="-200" {{ 'selected' if config.laadregeling.doel_net_vermogen_w == -200 }}>−200 W — Meer export</option>
    </select>
  </div>

  <!-- Huisprofiel -->
  <div class="flex flex-col gap-1">
    <span class="text-[10px] uppercase tracking-wider font-medium text-[var(--text-muted)]">Huisprofiel</span>
    <select id="ctrl-profiel" onchange="quickSetting('huisprofiel', this.value)"
            class="bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-1.5 text-xs text-[var(--text-primary)] appearance-none cursor-pointer min-w-[120px]">
      <option value="rustig" {{ 'selected' if config.laadregeling.huisprofiel == 'rustig' }}>Rustig</option>
      <option value="normaal" {{ 'selected' if config.laadregeling.huisprofiel == 'normaal' }}>Normaal</option>
      <option value="druk" {{ 'selected' if config.laadregeling.huisprofiel == 'druk' }}>Druk</option>
    </select>
  </div>

</div>

<!-- Grafiek -->
<div class="rounded-xl border border-[var(--border)] p-5" style="background:var(--bg-surface);">
  <div class="flex items-start justify-between mb-4 gap-3 flex-wrap">
    <div>
      <div class="text-sm font-semibold text-[var(--text-primary)]">Vermogen &amp; Laadstroom</div>
      <div class="text-xs text-[var(--text-muted)] mt-0.5">Realtime · bijgewerkt elke 10 seconden</div>
    </div>
    <div class="flex items-center gap-3 flex-wrap">
      <!-- Tijdselectie -->
      <div class="flex gap-0.5 bg-[var(--bg-base)] border border-[var(--border)] rounded-lg p-1">
        {% for label, min in [('15m',15),('30m',30),('1u',60),('3u',180)] %}
        <button class="time-btn px-2.5 py-1 rounded-md text-xs font-medium transition-colors text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
                data-minuten="{{ min }}" onclick="setTijdvenster({{ min }}, this)">{{ label }}</button>
        {% endfor %}
      </div>
      <!-- Lijn-toggles -->
      <div class="flex gap-1.5 flex-wrap" id="lijn-toggles">
        <button class="lijn-toggle active-green px-2.5 py-1 rounded-md text-xs border transition-colors"
                data-lijn="p1" onclick="toggleLijn(this)">
          <span class="inline-block w-3 h-0.5 rounded mr-1" style="background:var(--green);vertical-align:middle;"></span>P1 netto
        </button>
        <button class="lijn-toggle active-purple px-2.5 py-1 rounded-md text-xs border transition-colors"
                data-lijn="trend" onclick="toggleLijn(this)">
          <span class="inline-block w-3 h-0.5 rounded mr-1" style="background:var(--purple);vertical-align:middle;"></span>Trend
        </button>
        <button class="lijn-toggle active-blue px-2.5 py-1 rounded-md text-xs border transition-colors"
                data-lijn="lv" onclick="toggleLijn(this)">
          <span class="inline-block w-3 h-0.5 rounded mr-1" style="background:var(--blue);vertical-align:middle;"></span>Laadvermogen
        </button>
        <button class="lijn-toggle active-gray px-2.5 py-1 rounded-md text-xs border transition-colors"
                data-lijn="target" onclick="toggleLijn(this)">
          <span class="inline-block w-5 h-px mr-1" style="border-top:2px dashed #4b5563;vertical-align:middle;display:inline-block;"></span>Target
        </button>
      </div>
    </div>
  </div>
  <canvas id="main-chart" height="180"></canvas>

  <!-- Event-legenda -->
  <div class="flex gap-5 mt-3 pt-3 border-t border-[var(--border)]">
    <div class="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
      <svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="rgba(59,130,246,.2)" stroke="#3b82f6" stroke-width="1.5"/></svg>
      Fase wissel
    </div>
    <div class="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
      <svg width="10" height="10" viewBox="0 0 10 10"><polygon points="5,0 0,10 10,10" fill="rgba(249,115,22,.2)" stroke="#f97316" stroke-width="1.5"/></svg>
      Noodoverride
    </div>
  </div>
</div>

{% endblock %}

{% block scripts %}
<script src="/static/js/dashboard.js"></script>
<script>
  // Doorgeven van server-side waarden aan dashboard.js
  window.DASHBOARD_CONFIG = {
    doel_net_vermogen_w: {{ config.laadregeling.doel_net_vermogen_w }},
    max_stroom_a: {{ config.laadregeling.max_stroom_a }},
    spanning_v: {{ config.laadregeling.spanning_v }},
  };
  initDashboard();
</script>
{% endblock %}
```

- [ ] **Stap 2: Controleer syntax**

```bash
python -m py_compile src/web.py && echo "OK"
```

- [ ] **Stap 3: Open browser → http://localhost:5000**

Controleer:
- Sidebar zichtbaar, inklapbaar via pijl-knop
- 3 KPI-kaarten gevuld met live waarden
- Controls strip toont correct geselecteerde waarden
- Grafiek-container aanwezig (leeg tot dashboard.js werkt)

- [ ] **Stap 4: Commit**

```bash
git add templates/index.html
git commit -m "feat: dashboard index.html met KPI-kaarten, controls strip en grafiek-container"
```

---

## Task 6: static/js/dashboard.js

**Files:**
- Create: `static/js/dashboard.js`

- [ ] **Stap 1: Maak `static/` map**

```bash
mkdir -p static/js
```

- [ ] **Stap 2: Maak `static/js/dashboard.js`**

```javascript
// dashboard.js — Chart.js grafiek, polling, sparklines, controls

let mainChart = null;
let sparklineP1 = null;
let sparklineTrend = null;
let huidigTijdvenster = 30;
let lijnenActief = { p1: true, trend: true, lv: true, target: true };
let vorigeSessieId = null;

function initDashboard() {
  initMainChart();
  initSparklines();
  laadGrafiekData(huidigTijdvenster);
  // Activeer 30m knop standaard
  document.querySelectorAll('.time-btn').forEach(btn => {
    if (parseInt(btn.dataset.minuten) === 30) {
      btn.classList.add('bg-[#1f2937]', 'text-[#d1d5db]');
    }
  });
  // Lijn-toggle styling initialiseren
  document.querySelectorAll('.lijn-toggle').forEach(btn => setToggleStijl(btn, true));
  // Start polling
  setInterval(pollStatus, 10000);
}

function initMainChart() {
  const ctx = document.getElementById('main-chart').getContext('2d');
  mainChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'P1 netto (W)',
          data: [],
          borderColor: '#10b981',
          backgroundColor: 'rgba(16,185,129,0.08)',
          borderWidth: 2,
          pointRadius: 0,
          fill: true,
          tension: 0.3,
        },
        {
          label: 'Trend (W)',
          data: [],
          borderColor: '#a78bfa',
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointRadius: 0,
          fill: false,
          tension: 0.5,
          borderDash: [],
        },
        {
          label: 'Laadvermogen (W)',
          data: [],
          borderColor: '#60a5fa',
          backgroundColor: 'rgba(96,165,250,0.06)',
          borderWidth: 2,
          pointRadius: 0,
          fill: true,
          tension: 0.3,
        },
        {
          label: 'Target',
          data: [],
          borderColor: '#4b5563',
          backgroundColor: 'transparent',
          borderWidth: 1.5,
          pointRadius: 0,
          fill: false,
          borderDash: [6, 4],
        },
      ]
    },
    options: {
      responsive: true,
      animation: { duration: 400 },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#111827',
          borderColor: '#1f2937',
          borderWidth: 1,
          titleColor: '#9ca3af',
          bodyColor: '#f9fafb',
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ${Math.round(ctx.parsed.y)} W`
          }
        }
      },
      scales: {
        x: {
          ticks: { color: '#4b5563', font: { size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 6 },
          grid: { color: '#1f2937' },
        },
        y: {
          ticks: { color: '#4b5563', font: { size: 10 }, callback: v => v + ' W' },
          grid: { color: '#1f2937' },
          min: -600, max: 600,
        }
      }
    }
  });
}

function initSparklines() {
  const maakSparkline = (id, kleur) => {
    const ctx = document.getElementById(id);
    if (!ctx) return null;
    return new Chart(ctx.getContext('2d'), {
      type: 'line',
      data: { labels: [], datasets: [{ data: [], borderColor: kleur, borderWidth: 2, pointRadius: 0, fill: true, backgroundColor: kleur + '30', tension: 0.4 }] },
      options: { responsive: false, animation: false, plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } } }
    });
  };
  sparklineP1    = maakSparkline('sparkline-p1', '#10b981');
  sparklineTrend = maakSparkline('sparkline-trend', '#a78bfa');
}

async function laadGrafiekData(minuten) {
  try {
    const res = await fetch(`/api/metingen?minuten=${minuten}`);
    const metingen = await res.json();
    if (!Array.isArray(metingen) || metingen.length === 0) return;

    const labels  = metingen.map(m => m.tijdstip.substr(11, 5));
    const p1Data  = metingen.map(m => m.net_vermogen_w);
    const target  = window.DASHBOARD_CONFIG?.doel_net_vermogen_w ?? 0;
    const spanning = window.DASHBOARD_CONFIG?.spanning_v ?? 230;
    const lvData  = metingen.map(m => m.gesteld_stroom_a * spanning * m.huidige_fasen);
    const trendData = p1Data.map((v, i) => {
      // Benadering: bereken EMA lokaal voor sparkline; echte EMA zit in api_status
      return null; // wordt ingevuld via pollStatus
    });
    const targetData = metingen.map(() => target);

    mainChart.data.labels = labels;
    mainChart.data.datasets[0].data = p1Data;
    mainChart.data.datasets[2].data = lvData;
    mainChart.data.datasets[3].data = targetData;
    mainChart.update('none');
  } catch (e) {
    console.warn('Grafiek laden mislukt:', e);
  }
}

async function pollStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();

    // KPI-kaarten bijwerken
    updateP1Kaart(data);
    updateLaadstroomKaart(data);
    updateTrendKaart(data);
    updateStatusBadge(data);

    // Nieuwe meting toevoegen aan grafiek
    if (data.metingen && data.metingen.length > 0) {
      const m = data.metingen[0]; // nieuwste
      voegMeetpuntToe(m, data);
    }

    // Sparklines bijwerken met laatste 20 metingen
    if (data.metingen) {
      const p1Vals = data.metingen.slice().reverse().map(m => m.net_vermogen_w);
      updateSparkline(sparklineP1, p1Vals);
    }

    // Sessie-popup
    if (data.nieuwe_sessie && data.nieuwe_sessie.id !== vorigeSessieId) {
      vorigeSessieId = data.nieuwe_sessie.id;
      // Simpele melding (popup-logica kan later worden uitgebreid door impeccable)
      console.log('Nieuwe sessie afgerond:', data.nieuwe_sessie);
    }
  } catch (e) {
    console.warn('Polling mislukt:', e);
  }
}

function voegMeetpuntToe(meting, data) {
  const label = meting.tijdstip.substr(11, 5);
  const spanning = window.DASHBOARD_CONFIG?.spanning_v ?? 230;
  const target = window.DASHBOARD_CONFIG?.doel_net_vermogen_w ?? 0;

  if (mainChart.data.labels.includes(label)) return; // duplicaat

  mainChart.data.labels.push(label);
  mainChart.data.datasets[0].data.push(meting.net_vermogen_w);
  mainChart.data.datasets[1].data.push(data.ema_net_vermogen_w ?? null);
  mainChart.data.datasets[2].data.push(meting.gesteld_stroom_a * spanning * meting.huidige_fasen);
  mainChart.data.datasets[3].data.push(target);

  // Sliding window: max 200 punten
  const max = 200;
  if (mainChart.data.labels.length > max) {
    mainChart.data.labels.shift();
    mainChart.data.datasets.forEach(ds => ds.data.shift());
  }
  mainChart.update('none');
}

function updateSparkline(chart, waarden) {
  if (!chart) return;
  chart.data.labels = waarden.map((_, i) => i);
  chart.data.datasets[0].data = waarden;
  chart.update('none');
}

function updateP1Kaart(data) {
  const w = data.net_vermogen_w ?? 0;
  const el = document.getElementById('p1-waarde');
  if (el) {
    el.textContent = Math.round(w) + ' W';
    el.style.color = w <= 0 ? 'var(--green)' : 'var(--red)';
  }
  const badge = document.getElementById('p1-badge');
  const sub = document.getElementById('p1-sub');
  if (badge && sub) {
    if (w <= 0) {
      badge.innerHTML = '<i data-lucide="arrow-up" class="w-2.5 h-2.5"></i> exporterend';
      badge.className = 'flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-[var(--accent-dim)] text-[var(--accent)]';
      sub.textContent = 'terug naar net';
    } else {
      badge.innerHTML = '<i data-lucide="arrow-down" class="w-2.5 h-2.5"></i> importerend';
      badge.className = 'flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-[rgba(239,68,68,.1)] text-[var(--red)]';
      sub.textContent = 'uit het net';
    }
    lucide.createIcons({ nodes: [badge] });
  }
}

function updateLaadstroomKaart(data) {
  const stroom = data.huidig_stroom_a ?? 0;
  const max = window.DASHBOARD_CONFIG?.max_stroom_a ?? 25;
  const fasen = data.huidige_fasen ?? 1;
  const pct = Math.min(stroom / max, 1);
  const omtrek = 251.2;

  const ring = document.getElementById('gauge-ring');
  if (ring) ring.setAttribute('stroke-dashoffset', omtrek - (pct * omtrek));

  const gv = document.getElementById('gauge-waarde');
  if (gv) gv.textContent = Math.round(stroom) + 'A';

  document.querySelectorAll('.fase-kolom').forEach(col => {
    const f = parseInt(col.dataset.fase);
    const actief = (fasen === 3) || (f === 1 && stroom > 0);
    const val = col.querySelector('.fase-val');
    const fill = col.querySelector('.fase-fill');
    if (val) {
      val.textContent = actief ? Math.round(stroom) + 'A' : '0A';
      val.style.color = actief ? 'var(--accent)' : 'var(--border)';
    }
    if (fill) {
      fill.style.height = actief ? (pct * 100) + '%' : '0%';
      fill.style.background = actief ? 'var(--accent)' : 'transparent';
    }
  });
}

function updateTrendKaart(data) {
  const ema = data.ema_net_vermogen_w;
  const el = document.getElementById('trend-waarde');
  if (el) el.textContent = ema != null ? Math.round(ema) + ' W' : '—';
}

function updateStatusBadge(data) {
  const text = document.getElementById('status-text');
  const meta = document.getElementById('status-meta');
  if (text) text.textContent = data.laadmodus || 'Onbekend';
  if (meta) meta.textContent = `· ${data.regelaar_model || ''} · ${data.huidige_fasen || 1}-fase`;
}

function setTijdvenster(minuten, btn) {
  huidigTijdvenster = minuten;
  document.querySelectorAll('.time-btn').forEach(b => {
    b.classList.remove('bg-[#1f2937]', 'text-[#d1d5db]');
    b.classList.add('text-[var(--text-muted)]');
  });
  btn.classList.add('bg-[#1f2937]', 'text-[#d1d5db]');
  btn.classList.remove('text-[var(--text-muted)]');
  // Reset grafiek en herlaad
  mainChart.data.labels = [];
  mainChart.data.datasets.forEach(ds => ds.data = []);
  laadGrafiekData(minuten);
}

function toggleLijn(btn) {
  const lijn = btn.dataset.lijn;
  const was = lijnenActief[lijn];
  lijnenActief[lijn] = !was;
  const dsIndex = { p1: 0, trend: 1, lv: 2, target: 3 }[lijn];
  if (dsIndex !== undefined) {
    mainChart.data.datasets[dsIndex].hidden = was;
    mainChart.update('none');
  }
  setToggleStijl(btn, !was);
}

function setToggleStijl(btn, actief) {
  const kleuren = { p1: '#10b981', trend: '#a78bfa', lv: '#60a5fa', target: '#4b5563' };
  const lijn = btn.dataset.lijn;
  const kleur = kleuren[lijn];
  if (actief) {
    btn.style.background = kleur + '18';
    btn.style.borderColor = kleur + '60';
    btn.style.color = kleur;
  } else {
    btn.style.background = 'transparent';
    btn.style.borderColor = '#1f2937';
    btn.style.color = '#4b5563';
  }
}

async function quickSetting(sleutel, waarde) {
  try {
    await fetch('/api/quick-settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [sleutel]: waarde }),
    });
  } catch (e) {
    console.warn('Quick-setting opslaan mislukt:', e);
  }
}
```

- [ ] **Stap 3: Open browser → http://localhost:5000**

Controleer:
- Grafiek laadt na ~1 seconde met groene P1-lijn en blauwe laadvermogen-lijn
- Tijdknoppen wisselen het tijdvenster (grafiek reset en herlaadt)
- Lijn-toggles zetten lijnen aan/uit
- KPI-kaarten updaten live na 10 seconden

- [ ] **Stap 4: Commit**

```bash
git add static/js/dashboard.js
git commit -m "feat: dashboard.js met Chart.js grafiek, polling, sparklines, tijdselectie, toggles"
```

---

## Task 7: sessies.html — tabel met uitklapbare rijen

**Files:**
- Rewrite: `templates/sessies.html`

- [ ] **Stap 1: Herschrijf `templates/sessies.html`**

```html
{% extends "base.html" %}
{% block title %}Sessies — Zaptec Solarcharge{% endblock %}

{% block content %}
<div class="flex items-center justify-between mb-6">
  <h1 class="text-base font-semibold text-[var(--text-primary)]">Laadsessies</h1>
  <span id="sessie-count" class="text-xs text-[var(--text-muted)]">Laden...</span>
</div>

<div class="rounded-xl border border-[var(--border)] overflow-hidden" style="background:var(--bg-surface);">
  <table class="w-full text-xs">
    <thead>
      <tr class="border-b border-[var(--border)]">
        <th class="px-4 py-3 text-left text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">Datum</th>
        <th class="px-4 py-3 text-left text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">Duur</th>
        <th class="px-4 py-3 text-left text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">kWh</th>
        <th class="px-4 py-3 text-left text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">Score</th>
        <th class="px-4 py-3 text-left text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">Model</th>
        <th class="px-4 py-3 w-10"></th>
      </tr>
    </thead>
    <tbody id="sessies-tbody">
      <tr>
        <td colspan="6" class="px-4 py-8 text-center text-[var(--text-muted)]">Laden...</td>
      </tr>
    </tbody>
  </table>
</div>

<!-- Paginering -->
<div class="flex items-center justify-between mt-4">
  <button id="btn-vorige" onclick="wisselPagina(-1)"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-secondary)] disabled:opacity-30 transition-colors"
          style="background:var(--bg-surface);" disabled>
    <i data-lucide="chevron-left" class="w-3 h-3"></i> Vorige
  </button>
  <span id="pagina-info" class="text-xs text-[var(--text-muted)]">Pagina 1</span>
  <button id="btn-volgende" onclick="wisselPagina(1)"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-secondary)] disabled:opacity-30 transition-colors"
          style="background:var(--bg-surface);" disabled>
    Volgende <i data-lucide="chevron-right" class="w-3 h-3"></i>
  </button>
</div>

<!-- Detail-rij template (verborgen, wordt gekopieerd door JS) -->
<template id="detail-template">
  <tr class="detail-rij border-b border-[var(--border)]">
    <td colspan="6" class="p-0">
      <div class="detail-inhoud p-4 border-t border-[var(--border)]" style="background:#0a0f16;">
        <!-- Stats grid -->
        <div class="grid grid-cols-3 gap-3 mb-4 stats-grid"></div>
        <!-- Mini-grafiek -->
        <div class="rounded-lg border border-[var(--border)] p-3" style="background:var(--bg-surface);">
          <div class="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-2">Vermogen tijdens sessie</div>
          <canvas class="sessie-chart" height="80"></canvas>
        </div>
      </div>
    </td>
  </tr>
</template>

{% endblock %}

{% block scripts %}
<script src="/static/js/sessies.js"></script>
<script>
  document.addEventListener('DOMContentLoaded', () => laadSessies(1));
</script>
{% endblock %}
```

- [ ] **Stap 2: Commit**

```bash
git add templates/sessies.html
git commit -m "feat: sessies.html met uitklapbare rij-structuur en template"
```

---

## Task 8: static/js/sessies.js

**Files:**
- Create: `static/js/sessies.js`

- [ ] **Stap 1: Maak `static/js/sessies.js`**

```javascript
// sessies.js — uitklapbare sessie-rijen + mini-grafiek

let huidigePagina = 1;
let totaalPaginas = 1;
let openSessieId = null;
let sessieCharts = {};

async function laadSessies(pagina) {
  huidigePagina = pagina;
  try {
    const res = await fetch(`/api/sessies?pagina=${pagina}`);
    const data = await res.json();
    totaalPaginas = data.paginas;
    renderTabel(data.sessies);
    renderPaginering(data.pagina, data.paginas, data.totaal);
    lucide.createIcons();
  } catch (e) {
    console.warn('Sessies laden mislukt:', e);
  }
}

function renderTabel(sessies) {
  const tbody = document.getElementById('sessies-tbody');
  tbody.innerHTML = '';

  if (sessies.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-8 text-center text-[var(--text-muted)]">Geen sessies gevonden.</td></tr>';
    return;
  }

  sessies.forEach(s => {
    const rij = document.createElement('tr');
    rij.className = 'data-rij border-b border-[var(--border)] hover:bg-white/[0.02] transition-colors cursor-pointer';
    rij.dataset.sessieId = s.id;

    const scoreKleur = s.gem_score >= 75 ? '#10b981' : s.gem_score >= 50 ? '#f97316' : '#ef4444';
    const scoreBg    = s.gem_score >= 75 ? 'rgba(16,185,129,.12)' : s.gem_score >= 50 ? 'rgba(249,115,22,.12)' : 'rgba(239,68,68,.12)';
    const model      = (s.model || '').includes('solar') ? 'SolarFlow' : 'Legacy';
    const modelKleur = s.model?.includes('solar') ? '#a78bfa' : '#6b7280';
    const duur       = formatDuur(s.duur_s);
    const datum      = (s.start_tijdstip || '').substr(0, 16).replace('T', ' ');
    const kwh        = s.geladen_kwh != null ? s.geladen_kwh.toFixed(1) : '—';
    const score      = s.gem_score != null ? Math.round(s.gem_score) : '—';

    rij.innerHTML = `
      <td class="px-4 py-3 text-[var(--text-primary)]">${datum}</td>
      <td class="px-4 py-3 text-[var(--text-secondary)]">${duur}</td>
      <td class="px-4 py-3 font-semibold text-[var(--text-primary)]">${kwh}</td>
      <td class="px-4 py-3">
        <span class="px-2 py-0.5 rounded text-[10px] font-bold"
              style="background:${scoreBg};color:${scoreKleur};">${score}</span>
      </td>
      <td class="px-4 py-3">
        <span class="text-[10px]" style="color:${modelKleur};">${model}</span>
      </td>
      <td class="px-4 py-3">
        <button class="expand-btn w-6 h-6 rounded border border-[var(--border)] flex items-center justify-center transition-colors hover:border-[var(--accent)]"
                style="background:var(--bg-base);" data-sessie-id="${s.id}">
          <i data-lucide="chevron-down" class="w-3 h-3 text-[var(--text-muted)]"></i>
        </button>
      </td>
    `;

    rij.addEventListener('click', e => {
      if (!e.target.closest('.expand-btn')) toggleDetail(s);
    });
    rij.querySelector('.expand-btn').addEventListener('click', e => {
      e.stopPropagation();
      toggleDetail(s);
    });

    tbody.appendChild(rij);
  });
}

function toggleDetail(sessie) {
  const id = sessie.id;
  const tbody = document.getElementById('sessies-tbody');
  const bestaandeDetail = tbody.querySelector(`.detail-rij[data-detail-id="${id}"]`);

  if (bestaandeDetail) {
    // Sluiten
    if (sessieCharts[id]) { sessieCharts[id].destroy(); delete sessieCharts[id]; }
    bestaandeDetail.remove();
    openSessieId = null;
    updateExpandBtn(id, false);
    return;
  }

  // Sluit vorige open rij
  if (openSessieId && openSessieId !== id) {
    const oud = tbody.querySelector(`.detail-rij[data-detail-id="${openSessieId}"]`);
    if (oud) {
      if (sessieCharts[openSessieId]) { sessieCharts[openSessieId].destroy(); delete sessieCharts[openSessieId]; }
      oud.remove();
    }
    updateExpandBtn(openSessieId, false);
  }

  openSessieId = id;
  updateExpandBtn(id, true);

  // Voeg detail-rij in na data-rij
  const dataRij = tbody.querySelector(`tr[data-sessie-id="${id}"]`);
  const template = document.getElementById('detail-template');
  const clone = template.content.cloneNode(true);
  const detailRij = clone.querySelector('.detail-rij');
  detailRij.dataset.detailId = id;
  dataRij.after(detailRij);

  // Vul stats
  vulStats(detailRij.querySelector('.stats-grid'), sessie);

  // Laad grafiek
  laadSessieGrafiek(detailRij.querySelector('.sessie-chart'), id);
  lucide.createIcons();
}

function updateExpandBtn(sessieId, open) {
  const tbody = document.getElementById('sessies-tbody');
  const btn = tbody.querySelector(`.expand-btn[data-sessie-id="${sessieId}"]`);
  if (!btn) return;
  btn.style.background = open ? 'rgba(16,185,129,.15)' : 'var(--bg-base)';
  btn.style.borderColor = open ? 'rgba(16,185,129,.5)' : 'var(--border)';
  const icon = btn.querySelector('i');
  if (icon) {
    icon.setAttribute('data-lucide', open ? 'chevron-up' : 'chevron-down');
    icon.style.color = open ? 'var(--accent)' : 'var(--text-muted)';
    lucide.createIcons({ nodes: [btn] });
  }
}

function vulStats(grid, sessie) {
  const stats = [
    { val: sessie.gem_score != null ? Math.round(sessie.gem_score) + ' / 100' : '—', lbl: 'Sessiescore',
      kleur: sessie.gem_score >= 75 ? '#10b981' : sessie.gem_score >= 50 ? '#f97316' : '#ef4444' },
    { val: sessie.gem_afwijking_w != null ? '±' + Math.round(sessie.gem_afwijking_w) + ' W' : '—', lbl: 'Gem. afwijking target', kleur: '#d1d5db' },
    { val: sessie.geladen_kwh != null ? sessie.geladen_kwh.toFixed(1) + ' kWh' : '—', lbl: 'Totaal geladen', kleur: '#d1d5db' },
    { val: sessie.fase_wissel_count ?? 0, lbl: 'Fase wisselingen', kleur: '#d1d5db' },
    { val: sessie.no_import_count ?? 0, lbl: 'Noodoverride import', kleur: sessie.no_import_count > 0 ? '#f97316' : '#d1d5db' },
    { val: sessie.no_export_count ?? 0, lbl: 'Noodoverride export', kleur: '#d1d5db' },
  ];
  stats.forEach(s => {
    const kaart = document.createElement('div');
    kaart.className = 'rounded-lg p-3 border border-[var(--border)]';
    kaart.style.background = 'var(--bg-surface)';
    kaart.innerHTML = `<div class="text-lg font-bold leading-none mb-1.5" style="color:${s.kleur};">${s.val}</div>
                       <div class="text-[10px] text-[var(--text-muted)]">${s.lbl}</div>`;
    grid.appendChild(kaart);
  });
}

async function laadSessieGrafiek(canvas, sessieId) {
  try {
    const res = await fetch(`/api/sessies/${sessieId}/metingen`);
    const data = await res.json();
    const metingen = data.metingen || [];
    const events   = data.events   || [];

    if (metingen.length === 0) {
      canvas.parentElement.innerHTML += '<p class="text-[10px] text-[var(--text-muted)] mt-1">Geen meetdata beschikbaar voor deze sessie.</p>';
      return;
    }

    const spanning = 230;
    const labels  = metingen.map(m => m.tijdstip.substr(11, 5));
    const p1Data  = metingen.map(m => m.net_vermogen_w);
    const lvData  = metingen.map(m => (m.gesteld_stroom_a || 0) * spanning * (m.huidige_fasen || 1));

    // Event-annotaties (eenvoudige verticale lijn via afterDraw plugin)
    const faseWissels    = events.filter(e => e.event_type === 'fase_wissel').map(e => e.tijdstip.substr(11, 5));
    const noodoverrides  = events.filter(e => e.event_type.startsWith('noodoverride')).map(e => e.tijdstip.substr(11, 5));

    const eventPlugin = {
      id: 'eventMarkers',
      afterDraw(chart) {
        const { ctx, chartArea: { top, bottom }, scales: { x } } = chart;
        [...faseWissels, ...noodoverrides].forEach(tijdstip => {
          const idx = labels.indexOf(tijdstip);
          if (idx < 0) return;
          const xPos = x.getPixelForValue(idx);
          const isNoodoverride = noodoverrides.includes(tijdstip);
          ctx.save();
          ctx.strokeStyle = isNoodoverride ? '#f97316' : '#3b82f6';
          ctx.setLineDash([3, 3]);
          ctx.globalAlpha = 0.5;
          ctx.beginPath();
          ctx.moveTo(xPos, top);
          ctx.lineTo(xPos, bottom);
          ctx.stroke();
          ctx.restore();
        });
      }
    };

    sessieCharts[sessieId] = new Chart(canvas.getContext('2d'), {
      type: 'line',
      plugins: [eventPlugin],
      data: {
        labels,
        datasets: [
          { label: 'P1 netto (W)', data: p1Data, borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.08)', borderWidth: 1.5, pointRadius: 0, fill: true, tension: 0.3 },
          { label: 'Laadvermogen (W)', data: lvData, borderColor: '#60a5fa', backgroundColor: 'rgba(96,165,250,0.06)', borderWidth: 1.5, pointRadius: 0, fill: true, tension: 0.3 },
        ]
      },
      options: {
        responsive: true,
        animation: { duration: 300 },
        plugins: { legend: { display: false }, tooltip: { backgroundColor: '#111827', borderColor: '#1f2937', borderWidth: 1, titleColor: '#9ca3af', bodyColor: '#f9fafb' } },
        scales: {
          x: { ticks: { color: '#4b5563', font: { size: 9 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 6 }, grid: { color: '#1f2937' } },
          y: { ticks: { color: '#4b5563', font: { size: 9 }, callback: v => v + 'W' }, grid: { color: '#1f2937' } }
        }
      }
    });
  } catch (e) {
    console.warn('Sessie-grafiek laden mislukt:', e);
  }
}

function formatDuur(seconden) {
  if (!seconden) return '—';
  const u = Math.floor(seconden / 3600);
  const m = Math.floor((seconden % 3600) / 60);
  return u > 0 ? `${u}u ${m}min` : `${m}min`;
}

function renderPaginering(pagina, paginas, totaal) {
  document.getElementById('sessie-count').textContent = `${totaal} sessie${totaal !== 1 ? 's' : ''}`;
  document.getElementById('pagina-info').textContent = `Pagina ${pagina} van ${paginas}`;
  document.getElementById('btn-vorige').disabled = pagina <= 1;
  document.getElementById('btn-volgende').disabled = pagina >= paginas;
  lucide.createIcons();
}

function wisselPagina(delta) {
  laadSessies(huidigePagina + delta);
}
```

- [ ] **Stap 2: Open browser → http://localhost:5000/sessies**

Controleer:
- Sessietabel laadt
- Klik op rij of expand-knop → rij klapt open met stats-kaartjes
- Mini-grafiek verschijnt na korte laadtijd
- Sluiten werkt (rij klapt dicht)
- Paginering werkt

- [ ] **Stap 3: Commit**

```bash
git add templates/sessies.html static/js/sessies.js
git commit -m "feat: sessies-pagina met uitklapbare rijen, stats-grid en mini-grafiek"
```

---

## Task 9: laadregeling.html

**Files:**
- Create: `templates/laadregeling.html`

- [ ] **Stap 1: Maak `templates/laadregeling.html`**

De laadregeling-pagina bevat dezelfde formuliervelden als het laadregeling-gedeelte van de oude `instellingen.html`. Kopieer die velden en pas de stijl aan naar het nieuwe thema (dark, Tailwind).

Kernstructuur:

```html
{% extends "base.html" %}
{% block title %}Laadregeling — Zaptec Solarcharge{% endblock %}
{% block content %}
<div class="max-w-2xl">
  <h1 class="text-base font-semibold text-[var(--text-primary)] mb-6">Laadregeling</h1>

  {% if fouten %}
  <div class="rounded-lg border border-red-800 bg-red-900/20 p-4 mb-4">
    <ul class="text-xs text-red-400 space-y-1">
      {% for f in fouten %}<li>{{ f }}</li>{% endfor %}
    </ul>
  </div>
  {% endif %}

  <form method="post">
    <!-- Sectie: Algoritme & Doel -->
    <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
      <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-4">Algoritme &amp; Doel</h2>
      <!-- Formuliervelden: regelaar_model, doelinstelling_preset, doel_net_vermogen_w_geavanceerd -->
      <!-- Gebruik dezelfde veldnamen als in _verwerk_laadregeling() -->
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Regelaar model</label>
          <select name="regelaar_model" class="form-select w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
            <option value="solarflow" {{ 'selected' if config.laadregeling.regelaar_model == 'solarflow' }}>SolarFlow v1</option>
            <option value="legacy" {{ 'selected' if config.laadregeling.regelaar_model == 'legacy' }}>Legacy</option>
          </select>
        </div>
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Doel netvermogen</label>
          <select name="doelinstelling_preset" class="form-select w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
            <option value="50" {{ 'selected' if config.laadregeling.doel_net_vermogen_w == 50 }}>+50 W — Veiligheidsmarge</option>
            <option value="0" {{ 'selected' if config.laadregeling.doel_net_vermogen_w == 0 }}>0 W — Neutraal</option>
            <option value="-100" {{ 'selected' if config.laadregeling.doel_net_vermogen_w == -100 }}>−100 W — Licht export</option>
            <option value="-200" {{ 'selected' if config.laadregeling.doel_net_vermogen_w == -200 }}>−200 W — Meer export</option>
            <option value="aangepast" {{ 'selected' if config.laadregeling.doel_net_vermogen_w not in [50,0,-100,-200] }}>Aangepast</option>
          </select>
        </div>
      </div>
    </div>

    <!-- Sectie: Fasemodus & Stroom (spanning_v, fase_modus, min_stroom_a, max_stroom_a, etc.) -->
    <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
      <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-4">Fasemodus &amp; Stroom</h2>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Fasemodus</label>
          <select name="fase_modus" class="form-select w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
            <option value="auto" {{ 'selected' if config.laadregeling.fase_modus == 'auto' }}>Automatisch</option>
            <option value="1" {{ 'selected' if config.laadregeling.fase_modus == '1' }}>Altijd 1-fase</option>
            <option value="3" {{ 'selected' if config.laadregeling.fase_modus == '3' }}>Altijd 3-fase</option>
          </select>
        </div>
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Spanning (V)</label>
          <input type="number" name="spanning_v" value="{{ config.laadregeling.spanning_v }}" min="100" max="400"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
        </div>
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Min. laadstroom (A)</label>
          <input type="number" name="min_stroom_a" value="{{ config.laadregeling.min_stroom_a }}" min="6" max="32" step="0.5"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
        </div>
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Max. laadstroom (A)</label>
          <input type="number" name="max_stroom_a" value="{{ config.laadregeling.max_stroom_a }}" min="6" max="63" step="0.5"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
        </div>
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Veiligheidsbuffer (W)</label>
          <input type="number" name="veiligheidsbuffer_w" value="{{ config.laadregeling.veiligheidsbuffer_w }}"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
        </div>
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Fasewisselvertraging (s)</label>
          <input type="number" name="fase_wissel_wachttijd_s" value="{{ config.laadregeling.fase_wissel_wachttijd_s }}" min="60" max="3600"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
        </div>
      </div>
    </div>

    <!-- Sectie: Noodoverride -->
    <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
      <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-4">Noodoverride</h2>
      <div class="flex items-center gap-3 mb-4">
        <input type="checkbox" name="noodoverride_actief" id="noodoverride_actief"
               {{ 'checked' if config.laadregeling.noodoverride_actief }}
               class="w-4 h-4 rounded accent-[var(--accent)]">
        <label for="noodoverride_actief" class="text-sm text-[var(--text-secondary)]">Noodoverride inschakelen</label>
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Importdrempel (W)</label>
          <input type="number" name="noodoverride_drempel_w" value="{{ config.laadregeling.noodoverride_drempel_w }}"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
        </div>
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Exportdrempel (W)</label>
          <input type="number" name="noodoverride_export_drempel_w" value="{{ config.laadregeling.noodoverride_export_drempel_w }}"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
        </div>
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Cooldown (s)</label>
          <input type="number" name="noodoverride_wachttijd_s" value="{{ config.laadregeling.noodoverride_wachttijd_s }}" min="10" max="3600"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
        </div>
      </div>
    </div>

    <!-- Sectie: Huisprofiel & EMA -->
    <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
      <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-4">Huisprofiel &amp; EMA</h2>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Huisprofiel</label>
          <select name="huisprofiel" class="form-select w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
            <option value="rustig" {{ 'selected' if config.laadregeling.huisprofiel == 'rustig' }}>Rustig</option>
            <option value="normaal" {{ 'selected' if config.laadregeling.huisprofiel == 'normaal' }}>Normaal</option>
            <option value="druk" {{ 'selected' if config.laadregeling.huisprofiel == 'druk' }}>Druk</option>
          </select>
        </div>
      </div>
    </div>

    <div class="flex gap-3">
      <button type="submit" class="px-4 py-2 rounded-lg text-sm font-semibold text-white transition-colors"
              style="background:var(--accent);">Opslaan</button>
      <a href="/" class="px-4 py-2 rounded-lg text-sm text-[var(--text-muted)] border border-[var(--border)] hover:text-[var(--text-secondary)] transition-colors"
         style="background:var(--bg-base);">Annuleren</a>
    </div>
  </form>
</div>
{% endblock %}
```

- [ ] **Stap 2: Open browser → http://localhost:5000/laadregeling**

Controleer:
- Pagina laadt zonder fouten
- Huidige config-waarden zijn vooringevuld
- Formulier opslaan werkt (redirect naar `/`)

- [ ] **Stap 3: Commit**

```bash
git add templates/laadregeling.html
git commit -m "feat: laadregeling.html instellingenpagina"
```

---

## Task 10: apparaten.html

**Files:**
- Create: `templates/apparaten.html`

- [ ] **Stap 1: Maak `templates/apparaten.html`**

Zelfde structuur als laadregeling.html maar met HomeWizard en Zaptec verbindingsvelden:

```html
{% extends "base.html" %}
{% block title %}Apparaten — Zaptec Solarcharge{% endblock %}
{% block content %}
<div class="max-w-2xl">
  <h1 class="text-base font-semibold text-[var(--text-primary)] mb-6">Apparaten</h1>

  {% if fouten %}
  <div class="rounded-lg border border-red-800 bg-red-900/20 p-4 mb-4">
    <ul class="text-xs text-red-400 space-y-1">{% for f in fouten %}<li>{{ f }}</li>{% endfor %}</ul>
  </div>
  {% endif %}

  <form method="post">
    <!-- HomeWizard P1 Meter -->
    <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
      <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-4">HomeWizard P1 Meter</h2>
      <div class="grid grid-cols-2 gap-4">
        <div class="col-span-2">
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">IP-adres</label>
          <input type="text" name="hw_ip" value="{{ config.homewizard.ip }}"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
          <p class="text-[10px] text-[var(--text-muted)] mt-1">Lokaal IP-adres van de P1-meter</p>
        </div>
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Poll-interval (s)</label>
          <input type="number" name="homewizard_poll_interval_s" value="{{ config.homewizard.poll_interval_s }}" min="5" max="300"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
          <p class="text-[10px] text-[var(--text-muted)] mt-1">Hoe vaak meten (5–300s)</p>
        </div>
      </div>
    </div>

    <!-- Zaptec -->
    <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
      <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-4">Zaptec Lader</h2>
      <div class="rounded-lg border border-amber-800/50 bg-amber-900/10 p-3 mb-4 text-xs text-amber-400">
        Wachtwoord en tokens wijzigen via SSH in <code>config/.env</code>
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div class="col-span-2">
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Installation ID</label>
          <input type="text" name="zaptec_installation_id" value="{{ config.zaptec.installation_id }}"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
        </div>
        <div class="col-span-2">
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Charger ID</label>
          <input type="text" name="zaptec_charger_id" value="{{ config.zaptec.charger_id }}"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
        </div>
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Update-interval (s)</label>
          <input type="number" name="update_interval_s" value="{{ config.zaptec.update_interval_s }}" min="60" max="3600"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
        </div>
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">State-poll interval (s)</label>
          <input type="number" name="state_poll_interval_s" value="{{ config.zaptec.state_poll_interval_s }}" min="10" max="300"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
        </div>
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Live stroombron</label>
          <select name="live_stroom_bron" class="form-select w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
            {% for opt in ['auto','708','meting','uit'] %}
            <option value="{{ opt }}" {{ 'selected' if config.zaptec.live_stroom_bron == opt }}>{{ opt }}</option>
            {% endfor %}
          </select>
        </div>
      </div>
    </div>

    <div class="flex gap-3">
      <button type="submit" class="px-4 py-2 rounded-lg text-sm font-semibold text-white transition-colors" style="background:var(--accent);">Opslaan</button>
      <a href="/" class="px-4 py-2 rounded-lg text-sm text-[var(--text-muted)] border border-[var(--border)] hover:text-[var(--text-secondary)] transition-colors" style="background:var(--bg-base);">Annuleren</a>
    </div>
  </form>
</div>
{% endblock %}
```

- [ ] **Stap 2: Test in browser → http://localhost:5000/apparaten**

- [ ] **Stap 3: Commit**

```bash
git add templates/apparaten.html
git commit -m "feat: apparaten.html instellingenpagina"
```

---

## Task 11: interface.html

**Files:**
- Create: `templates/interface.html`

- [ ] **Stap 1: Maak `templates/interface.html`**

```html
{% extends "base.html" %}
{% block title %}Interface — Zaptec Solarcharge{% endblock %}
{% block content %}
<div class="max-w-2xl">
  <h1 class="text-base font-semibold text-[var(--text-primary)] mb-6">Interface &amp; Opslag</h1>

  {% if fouten %}
  <div class="rounded-lg border border-red-800 bg-red-900/20 p-4 mb-4">
    <ul class="text-xs text-red-400 space-y-1">{% for f in fouten %}<li>{{ f }}</li>{% endfor %}</ul>
  </div>
  {% endif %}

  <form method="post">
    <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
      <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-4">Webinterface</h2>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Poort</label>
          <input type="number" name="web_poort" value="{{ config.web.poort }}" min="1024" max="65535"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
          <p class="text-[10px] text-[var(--text-muted)] mt-1">Herstart vereist na wijziging</p>
        </div>
      </div>
    </div>

    <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
      <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-4">Logging &amp; Opslag</h2>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Logniveau</label>
          <select name="log_niveau" class="form-select w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
            {% for lvl in ['DEBUG','INFO','WARNING','ERROR'] %}
            <option value="{{ lvl }}" {{ 'selected' if config.opslag.log_niveau == lvl }}>{{ lvl }}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label class="block text-xs text-[var(--text-muted)] mb-1.5">Bewaarperiode (dagen)</label>
          <input type="number" name="bewaarperiode_dagen" value="{{ config.opslag.bewaarperiode_dagen }}" min="7" max="365"
                 class="w-full bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
        </div>
        <div class="col-span-2 flex items-center gap-3">
          <input type="checkbox" name="debug_modus" id="debug_modus"
                 {{ 'checked' if config.opslag.debug_modus }}
                 class="w-4 h-4 rounded accent-[var(--accent)]">
          <label for="debug_modus" class="text-sm text-[var(--text-secondary)]">Debug-modus inschakelen</label>
          <span class="text-[10px] text-[var(--text-muted)]">(toont Diagnostics-pagina in menu)</span>
        </div>
      </div>
    </div>

    <div class="flex gap-3">
      <button type="submit" class="px-4 py-2 rounded-lg text-sm font-semibold text-white transition-colors" style="background:var(--accent);">Opslaan</button>
      <a href="/" class="px-4 py-2 rounded-lg text-sm text-[var(--text-muted)] border border-[var(--border)] hover:text-[var(--text-secondary)] transition-colors" style="background:var(--bg-base);">Annuleren</a>
    </div>
  </form>
</div>
{% endblock %}
```

- [ ] **Stap 2: Test in browser → http://localhost:5000/interface**

- [ ] **Stap 3: Commit**

```bash
git add templates/interface.html
git commit -m "feat: interface.html instellingenpagina"
```

---

## Task 12: updates.html

**Files:**
- Create: `templates/updates.html`

- [ ] **Stap 1: Maak `templates/updates.html`**

Zelfde functionaliteit als het versie+update-gedeelte van `beheer.html`. Kopieer de JS-logica voor fetch `/api/versie-info`, update-knop en branch-wissel. Pas de HTML-structuur aan naar het nieuwe thema.

```html
{% extends "base.html" %}
{% block title %}Updates — Zaptec Solarcharge{% endblock %}
{% block content %}
<div class="max-w-2xl">
  <h1 class="text-base font-semibold text-[var(--text-primary)] mb-6">Updates</h1>

  <!-- Versie-kaart -->
  <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
    <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-4">Huidige versie</h2>
    <div class="grid grid-cols-2 gap-4 text-sm">
      <div>
        <div class="text-[10px] text-[var(--text-muted)] mb-1">Versie</div>
        <div id="versie-nr" class="font-semibold text-[var(--text-primary)]">Laden...</div>
      </div>
      <div>
        <div class="text-[10px] text-[var(--text-muted)] mb-1">Branch</div>
        <div id="versie-branch" class="font-semibold text-[var(--text-primary)]">—</div>
      </div>
      <div class="col-span-2">
        <div class="text-[10px] text-[var(--text-muted)] mb-1">Laatste commit</div>
        <div id="versie-commit" class="text-[var(--text-secondary)] text-xs">—</div>
      </div>
      <div class="col-span-2">
        <span id="update-badge" class="hidden px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-900/30 text-amber-400 border border-amber-800/50">
          Update beschikbaar
        </span>
        <span id="current-badge" class="px-2 py-0.5 rounded text-[10px] font-semibold bg-[var(--accent-dim)] text-[var(--accent)] border border-[var(--border-green)]">
          Up-to-date
        </span>
      </div>
    </div>
  </div>

  <!-- Acties -->
  <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
    <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-4">Acties</h2>
    <div class="flex gap-3 flex-wrap">
      <button id="btn-update" onclick="bevestigUpdate()"
              class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold text-white transition-colors"
              style="background:var(--accent);">
        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
        <span id="update-btn-tekst">Updaten</span>
      </button>
      <button id="btn-branch" onclick="bevestigBranch()"
              class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
              style="background:var(--bg-base);">
        <i data-lucide="git-branch" class="w-4 h-4"></i>
        <span id="branch-btn-tekst">Wissel branch</span>
      </button>
      <button onclick="bevestigHerstart()"
              class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
              style="background:var(--bg-base);">
        <i data-lucide="power" class="w-4 h-4"></i> Herstart
      </button>
    </div>
  </div>

  <!-- Bevestigingsdialogen (Alpine.js) -->
  <div x-data="updateDialogen()" x-cloak>
    <!-- Update bevestigen -->
    <div x-show="updateOpen" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div class="rounded-xl border border-[var(--border)] p-6 max-w-sm w-full mx-4" style="background:var(--bg-surface);">
        <h3 class="font-semibold text-[var(--text-primary)] mb-2">Update uitvoeren?</h3>
        <p class="text-xs text-[var(--text-muted)] mb-4">Maakt een backup, voert git pull uit en herstart de service.</p>
        <div class="flex gap-3">
          <button @click="voerUpdate()" class="flex-1 px-4 py-2 rounded-lg text-sm font-semibold text-white" style="background:var(--accent);">Bevestigen</button>
          <button @click="updateOpen=false" class="flex-1 px-4 py-2 rounded-lg text-sm border border-[var(--border)] text-[var(--text-muted)]" style="background:var(--bg-base);">Annuleren</button>
        </div>
      </div>
    </div>
    <!-- Branch wissel -->
    <div x-show="branchOpen" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div class="rounded-xl border border-[var(--border)] p-6 max-w-sm w-full mx-4" style="background:var(--bg-surface);">
        <h3 class="font-semibold text-[var(--text-primary)] mb-2">Branch wisselen</h3>
        <p class="text-xs text-[var(--text-muted)] mb-3" x-text="'Huidige branch: ' + huidigeBranch"></p>
        <select x-model="doelBranch" class="w-full mb-4 bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]">
          <option value="main">main</option>
          <option value="beta">beta</option>
        </select>
        <div class="flex gap-3">
          <button @click="voerBranchWissel()" class="flex-1 px-4 py-2 rounded-lg text-sm font-semibold text-white" style="background:var(--accent);">Wisselen</button>
          <button @click="branchOpen=false" class="flex-1 px-4 py-2 rounded-lg text-sm border border-[var(--border)] text-[var(--text-muted)]" style="background:var(--bg-base);">Annuleren</button>
        </div>
      </div>
    </div>
  </div>
  <div id="actie-melding" class="hidden mt-4 rounded-lg border p-3 text-sm"></div>
</div>
{% endblock %}

{% block scripts %}
<script>
  // Laad versie-info
  fetch('/api/versie-info').then(r => r.json()).then(d => {
    document.getElementById('versie-nr').textContent = d.versie;
    document.getElementById('versie-branch').textContent = d.branch;
    document.getElementById('versie-commit').textContent = d.laatste_commit;
    if (d.update_beschikbaar) {
      document.getElementById('update-badge').classList.remove('hidden');
      document.getElementById('current-badge').classList.add('hidden');
      document.getElementById('update-btn-tekst').textContent = 'Nu updaten';
    }
    window._branch = d.branch;
  });

  function bevestigUpdate() { window._updateOpen = true; document.querySelector('[x-data]').__x.$data.updateOpen = true; }
  function bevestigBranch() { document.querySelector('[x-data]').__x.$data.branchOpen = true; }
  function bevestigHerstart() {
    if (!confirm('Service herstarten?')) return;
    fetch('/herstart', {method:'POST'}).then(() => {
      toonMelding('Service herstart...', 'groen');
      setTimeout(() => location.reload(), 6000);
    });
  }

  function updateDialogen() {
    return {
      updateOpen: false, branchOpen: false,
      huidigeBranch: window._branch || '—', doelBranch: 'main',
      voerUpdate() {
        this.updateOpen = false;
        fetch('/update', {method:'POST'}).then(() => {
          toonMelding('Update gestart — pagina herlaadt zodra de service terugkomt...', 'groen');
          setTimeout(() => wachtOpHerstart(), 8000);
        });
      },
      voerBranchWissel() {
        const b = this.doelBranch; this.branchOpen = false;
        fetch('/wissel-branch', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({branch:b})})
          .then(() => { toonMelding(`Wisselen naar ${b}...`, 'groen'); setTimeout(() => wachtOpHerstart(), 8000); });
      }
    };
  }

  function toonMelding(tekst, kleur) {
    const el = document.getElementById('actie-melding');
    el.textContent = tekst;
    el.className = kleur === 'groen'
      ? 'mt-4 rounded-lg border border-[var(--border-green)] bg-[var(--accent-dim)] text-[var(--accent)] p-3 text-sm'
      : 'mt-4 rounded-lg border border-red-800 bg-red-900/20 text-red-400 p-3 text-sm';
  }

  function wachtOpHerstart() {
    fetch('/').then(() => location.reload()).catch(() => setTimeout(wachtOpHerstart, 2000));
  }
</script>
{% endblock %}
```

- [ ] **Stap 2: Test in browser → http://localhost:5000/updates**

- [ ] **Stap 3: Commit**

```bash
git add templates/updates.html
git commit -m "feat: updates.html met versie-info, update-knop en branch-wissel"
```

---

## Task 13: backups.html

**Files:**
- Create: `templates/backups.html`

- [ ] **Stap 1: Maak `templates/backups.html`**

```html
{% extends "base.html" %}
{% block title %}Backups — Zaptec Solarcharge{% endblock %}
{% block content %}
<div class="max-w-2xl">
  <h1 class="text-base font-semibold text-[var(--text-primary)] mb-6">Backups</h1>

  <!-- Backup aanmaken -->
  <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
    <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-4">Backup aanmaken</h2>
    <p class="text-xs text-[var(--text-muted)] mb-4">Slaat config.yaml, .env en de database op in een timestamped map.</p>
    <button onclick="maakBackup()"
            class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold text-white transition-colors"
            style="background:var(--accent);">
      <i data-lucide="hard-drive" class="w-4 h-4"></i> Backup maken
    </button>
  </div>

  <!-- Backup-lijst -->
  <div class="rounded-xl border border-[var(--border)] overflow-hidden" style="background:var(--bg-surface);">
    <div class="px-5 py-3 border-b border-[var(--border)]">
      <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">Beschikbare backups</h2>
    </div>
    <div id="backup-lijst" class="divide-y divide-[var(--border)]">
      <div class="px-5 py-4 text-xs text-[var(--text-muted)]">Laden...</div>
    </div>
  </div>

  <div id="backup-melding" class="hidden mt-4 rounded-lg border p-3 text-sm"></div>

  <!-- Rollback bevestiging -->
  <div x-data="{ open: false, naam: '' }" x-cloak>
    <div x-show="open" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div class="rounded-xl border border-[var(--border)] p-6 max-w-sm w-full mx-4" style="background:var(--bg-surface);">
        <h3 class="font-semibold text-[var(--text-primary)] mb-2">Rollback uitvoeren?</h3>
        <p class="text-xs text-[var(--text-muted)] mb-1">Backup: <code x-text="naam" class="text-amber-400"></code></p>
        <p class="text-xs text-red-400 mb-4">Overschrijft de huidige config en database. Niet te herstellen.</p>
        <div class="flex gap-3">
          <button @click="voerRollbackUit(naam); open=false"
                  class="flex-1 px-4 py-2 rounded-lg text-sm font-semibold bg-red-600 text-white">Rollback</button>
          <button @click="open=false"
                  class="flex-1 px-4 py-2 rounded-lg text-sm border border-[var(--border)] text-[var(--text-muted)]"
                  style="background:var(--bg-base);">Annuleren</button>
        </div>
      </div>
    </div>
    <div id="rollback-trigger" style="display:none;"
         @rollback.window="open=true; naam=$event.detail"></div>
  </div>
</div>
{% endblock %}

{% block scripts %}
<script>
  laadBackups();

  function laadBackups() {
    fetch('/api/versie-info').then(r => r.json()).then(d => {
      const lijst = document.getElementById('backup-lijst');
      if (!d.backups || d.backups.length === 0) {
        lijst.innerHTML = '<div class="px-5 py-4 text-xs text-[var(--text-muted)]">Geen backups aanwezig.</div>';
        return;
      }
      lijst.innerHTML = d.backups.map(b => `
        <div class="flex items-center justify-between px-5 py-3">
          <div>
            <div class="text-sm text-[var(--text-primary)]">${b.naam}</div>
            <div class="text-[10px] text-[var(--text-muted)] mt-0.5">${b.bestanden.join(', ')}</div>
          </div>
          <button onclick="bevestigRollback('${b.naam}')"
                  class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border border-[var(--border)] text-[var(--text-muted)] hover:text-red-400 hover:border-red-800 transition-colors"
                  style="background:var(--bg-base);">
            <i data-lucide="rotate-ccw" class="w-3 h-3"></i> Terugzetten
          </button>
        </div>
      `).join('');
      lucide.createIcons();
    });
  }

  function maakBackup() {
    fetch('/backup', {method:'POST'}).then(r => r.json()).then(d => {
      toonMelding(`Backup aangemaakt: ${d.naam}`, 'groen');
      laadBackups();
    }).catch(() => toonMelding('Backup mislukt', 'rood'));
  }

  function bevestigRollback(naam) {
    window.dispatchEvent(new CustomEvent('rollback', {detail: naam}));
  }

  function voerRollbackUit(naam) {
    fetch('/rollback', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({naam})})
      .then(() => {
        toonMelding('Rollback gestart — pagina herlaadt zodra de service terugkomt...', 'groen');
        setTimeout(() => wachtOpHerstart(), 6000);
      });
  }

  function toonMelding(tekst, kleur) {
    const el = document.getElementById('backup-melding');
    el.textContent = tekst;
    el.className = kleur === 'groen'
      ? 'mt-4 rounded-lg border border-[var(--border-green)] bg-[var(--accent-dim)] text-[var(--accent)] p-3 text-sm'
      : 'mt-4 rounded-lg border border-red-800 bg-red-900/20 text-red-400 p-3 text-sm';
  }

  function wachtOpHerstart() {
    fetch('/').then(() => location.reload()).catch(() => setTimeout(wachtOpHerstart, 2000));
  }
</script>
{% endblock %}
```

- [ ] **Stap 2: Test in browser → http://localhost:5000/backups**

- [ ] **Stap 3: Commit**

```bash
git add templates/backups.html
git commit -m "feat: backups.html met backup-lijst en rollback"
```

---

## Task 14: debug.html herschreven

**Files:**
- Rewrite: `templates/debug.html`

- [ ] **Stap 1: Herschrijf `templates/debug.html`**

```html
{% extends "base.html" %}
{% block title %}Diagnostics — Zaptec Solarcharge{% endblock %}
{% block content %}
<div class="max-w-3xl">
  <h1 class="text-base font-semibold text-[var(--text-primary)] mb-6">Diagnostics</h1>

  <!-- Huidige staat -->
  <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
    <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-4">Huidige staat</h2>
    <div class="grid grid-cols-2 gap-2 text-xs">
      {% for k, v in [
        ('Actief', state.actief),
        ('Auto aangesloten', state.auto_aangesloten),
        ('Laadstroom', state.huidig_stroom_a ~ ' A'),
        ('Fasen', state.huidige_fasen),
        ('Netvermogen', state.net_vermogen_w ~ ' W'),
        ('EMA', state.ema_net_vermogen_w ~ ' W' if state.ema_net_vermogen_w else '—'),
        ('Laadmodus', state.laadmodus),
        ('Standby', state.standby_modus),
        ('Fout HW', state.fout_hw or '—'),
        ('Fout Zaptec state', state.fout_zaptec_state or '—'),
      ] %}
      <div class="flex justify-between py-1.5 border-b border-[var(--border)]">
        <span class="text-[var(--text-muted)]">{{ k }}</span>
        <span class="text-[var(--text-primary)] font-medium">{{ v }}</span>
      </div>
      {% endfor %}
    </div>
  </div>

  <!-- Log viewer -->
  <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
    <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-3">Logbestand (laatste 50 regels)</h2>
    <pre class="text-[10px] leading-relaxed overflow-x-auto rounded-lg p-3"
         style="background:#0d1117;color:#9ca3af;max-height:300px;overflow-y:auto;">{% for regel in log_regels %}{{ regel }}
{% endfor %}</pre>
  </div>

  <!-- API-tester (zelfde functionaliteit als oud debug.html) -->
  <div class="rounded-xl border border-[var(--border)] p-5 mb-4" style="background:var(--bg-surface);">
    <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-4">Zaptec API-tester</h2>
    <div class="flex flex-wrap gap-2 mb-4">
      {% for call in ['get_charger_state','get_charger_operation_mode','get_current_phases','is_car_connected','get_installation_mode'] %}
      <button onclick="apiCall('{{ call }}')"
              class="px-3 py-1.5 rounded-lg text-xs border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
              style="background:var(--bg-base);">{{ call }}</button>
      {% endfor %}
    </div>
    <div class="flex gap-2 mb-4">
      <input id="api-current" type="number" placeholder="Stroom (A)" value="-1" step="0.5" min="-1" max="63"
             class="w-32 bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs text-[var(--text-primary)]">
      <input id="api-3to1" type="number" placeholder="3→1 drempel" step="1"
             class="w-32 bg-[var(--bg-base)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs text-[var(--text-primary)]">
      <button onclick="bevestigSchrijf()"
              class="px-3 py-1.5 rounded-lg text-xs border border-amber-800/60 text-amber-400 hover:bg-amber-900/20 transition-colors"
              style="background:var(--bg-base);">set_installation_settings</button>
    </div>
    <pre id="api-result" class="text-[10px] rounded-lg p-3 min-h-[60px]"
         style="background:#0d1117;color:#9ca3af;">(klik op een knop)</pre>
  </div>

  <div class="flex gap-3">
    <button onclick="fetch('/reload-config',{method:'POST'}).then(()=>location.reload())"
            class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
            style="background:var(--bg-base);">
      <i data-lucide="refresh-cw" class="w-4 h-4"></i> Config herladen
    </button>
    <a href="/" class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
       style="background:var(--bg-base);">
      <i data-lucide="arrow-left" class="w-4 h-4"></i> Dashboard
    </a>
  </div>
</div>
{% endblock %}

{% block scripts %}
<script>
  function apiCall(call, params = {}, bevestigd = false) {
    fetch('/api/debug/call', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({call, params, bevestigd})
    }).then(r => r.json()).then(d => {
      document.getElementById('api-result').textContent = JSON.stringify(d, null, 2);
    });
  }

  function bevestigSchrijf() {
    const stroom = parseFloat(document.getElementById('api-current').value);
    const drempel = document.getElementById('api-3to1').value;
    const params = {available_current: stroom};
    if (drempel !== '') params.drie_naar_een_fase_stroom = parseFloat(drempel);
    if (!confirm(`set_installation_settings(${stroom}A)?`)) return;
    apiCall('set_installation_settings', params, true);
  }
</script>
{% endblock %}
```

- [ ] **Stap 2: Test in browser → http://localhost:5000/debug** (alleen als debug_modus=true)

- [ ] **Stap 3: Commit**

```bash
git add templates/debug.html
git commit -m "feat: debug.html herschreven met nieuw thema"
```

---

## Task 15: Eindcontrole + volledig testen

- [ ] **Stap 1: Syntax check alle Python-bestanden**

```bash
python -m py_compile src/homewizard.py src/zaptec.py src/controller.py src/database.py src/web.py src/config_migratie.py src/config_validatie.py main.py && echo "Syntax OK"
```

- [ ] **Stap 2: Alle tests draaien**

```bash
python -m pytest tests/ -q
```

Verwacht: alle tests PASS

- [ ] **Stap 3: Handmatige browser-controle (checklist)**

Start server: `python main.py`

Controleer elke pagina:

| Pagina | URL | Controleer |
|---|---|---|
| Dashboard | `/` | Sidebar inklapbaar, 3 KPI-kaarten live, grafiek met lijnen, controls strip werkt |
| Sessies | `/sessies` | Tabel laadt, rij uitklappen toont stats + mini-grafiek |
| Laadregeling | `/laadregeling` | Formulier ingevuld, opslaan werkt |
| Apparaten | `/apparaten` | Formulier ingevuld, opslaan werkt |
| Interface | `/interface` | Formulier ingevuld, opslaan werkt |
| Updates | `/updates` | Versie-info laadt, knoppen aanwezig |
| Backups | `/backups` | Backup-lijst laadt, backup aanmaken werkt |
| Diagnostics | `/debug` | Alleen zichtbaar bij debug_modus=true |

- [ ] **Stap 4: Eindcommit**

```bash
git add .
git commit -m "feat: dashboard overhaul compleet — Tailwind/Alpine/Chart.js, sidebar, alle paginas"
```

---

## Zelf-review: spec-dekking

| Spec-eis | Gedekt in taak |
|---|---|
| Groen/zwart thema + CSS-variabelen | Task 4 (base.html) |
| Tailwind + Alpine.js + Chart.js + Lucide | Task 4 |
| Inklapbare sidebar met accordion | Task 4 |
| Dashboard KPI-kaarten (P1, Laadstroom, Trend) | Task 5 |
| Cirkel-gauge + fase-balken in Laadstroom-kaart | Task 5 |
| Sparklines in P1 en Trend kaart | Task 6 |
| Controls strip (toggle, algoritme, doel, profiel) | Task 5 + Task 6 |
| Quick-settings AJAX (geen paginaverversing) | Task 2 + Task 6 |
| Grafiek met 4 datasets + export/import zones | Task 6 |
| Grafiek tijdselectie 15m/30m/1u/3u | Task 6 |
| Grafiek lijn-toggles | Task 6 |
| Event-markers in grafiek (fase, noodoverride) | Task 6 |
| `/api/metingen?minuten=N` | Task 2 |
| Sessies uitklapbare rijen + stats-grid | Task 7 + Task 8 |
| Mini-grafiek per sessie | Task 8 |
| `/api/sessies/<id>/metingen` | Task 2 |
| Laadregeling/Apparaten/Interface sub-pagina's | Task 3, 9, 10, 11 |
| Updates/Backups sub-pagina's | Task 3, 12, 13 |
| Debug pagina erft base.html | Task 14 |
| `ema_net_vermogen_w` in api_status | Task 2 |
| Sidebar Diagnostics verborgen als debug uit | Task 4 |
