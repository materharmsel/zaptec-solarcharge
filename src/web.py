"""
Flask webinterface voor Solarcharge.

Biedt een eenvoudig mobiel-vriendelijk dashboard met:
    /           — Status-overzicht
    /api/status — JSON-snapshot van de huidige staat
    /toggle     — Regelaar aan/uit zetten
    /instellingen — Configuratie bekijken en aanpassen
    /debug      — Debug-informatie en recente logs
    /reload-config — Herlaad config.yaml van schijf
"""

import logging
import os
import threading
from pathlib import Path

import yaml
from flask import Flask, render_template, request, redirect, url_for, jsonify

from src.database import haal_recente_metingen_op, haal_recente_events_op

logger = logging.getLogger(__name__)


def maak_app(state: dict, config: dict, db_pad: str) -> Flask:
    """
    Maakt en configureert de Flask-applicatie.

    Args:
        state:   Gedeelde state-dict (wordt live bijgewerkt door de hoofdlus).
        config:  Gedeelde config-dict (in-place bijgewerkt bij instellingen opslaan).
        db_pad:  Pad naar het SQLite-databasebestand.

    Returns:
        Geconfigureerde Flask-app.
    """
    templates_pad = Path(__file__).parent.parent / "templates"
    app = Flask(__name__, template_folder=str(templates_pad))
    app.secret_key = os.urandom(24)

    # Gebruik een lock voor veilig lezen/schrijven van de config vanuit meerdere threads
    config_lock = threading.Lock()

    # ── Dashboard ─────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        metingen = haal_recente_metingen_op(db_pad, limiet=20)
        events   = haal_recente_events_op(db_pad, limiet=10)
        return render_template(
            "index.html",
            state=state,
            config=config,
            metingen=metingen,
            events=events,
        )

    @app.route("/api/status")
    def api_status():
        """JSON-eindpunt voor AJAX-polling vanuit de webpagina."""
        return jsonify({
            "actief":            state["actief"],
            "auto_aangesloten":  state["auto_aangesloten"],
            "huidig_stroom_a":   state["huidig_stroom_a"],
            "huidige_fasen":     state["huidige_fasen"],
            "net_vermogen_w":    state["net_vermogen_w"],
            "laatste_hw_update": state["laatste_hw_update"],
            "fout_hw":           state.get("fout_hw"),
            "fout_zaptec_state": state.get("fout_zaptec_state"),
            "fout_zaptec_update": state.get("fout_zaptec_update"),
        })

    # ── Regelaar aan/uit ──────────────────────────────────────────────────────

    @app.route("/toggle", methods=["POST"])
    def toggle():
        """Zet de regelaar aan of uit."""
        state["actief"] = not state["actief"]
        status = "aan" if state["actief"] else "uit"
        logger.info("Regelaar %s gezet via webinterface.", status)
        return redirect(url_for("index"))

    # ── Instellingen ──────────────────────────────────────────────────────────

    @app.route("/instellingen", methods=["GET", "POST"])
    def instellingen():
        """Toont en verwerkt het instellingenformulier."""
        fouten = []

        if request.method == "POST":
            fouten = _verwerk_instellingen(request.form, config, config_lock)
            if not fouten:
                # Schrijf ook naar schijf zodat de instelling bewaard blijft na herstart
                _schrijf_config(config)
                logger.info("Instellingen opgeslagen via webinterface.")
                return redirect(url_for("index"))

        return render_template("instellingen.html", config=config, fouten=fouten)

    @app.route("/reload-config", methods=["POST"])
    def reload_config():
        """Herlaadt config.yaml van schijf (handig na handmatige SSH-aanpassing)."""
        try:
            nieuw = _lees_config_van_schijf()
            with config_lock:
                config.update(nieuw)
            logger.info("Config herladen van schijf via webinterface.")
        except Exception as e:
            logger.error("Herladen config mislukt: %s", e)
        return redirect(url_for("index"))

    # ── Debug ─────────────────────────────────────────────────────────────────

    @app.route("/debug")
    def debug():
        """Toont debug-informatie: recente logs, events en huidige state."""
        log_regels = _lees_laatste_logregels(
            config.get("opslag", {}).get("log_pad", "logs/solarcharge.log"),
            aantal=50,
        )
        events = haal_recente_events_op(db_pad, limiet=50)
        return render_template(
            "debug.html",
            state=state,
            config=config,
            log_regels=log_regels,
            events=events,
        )

    return app


def start_web_server(app: Flask, host: str = "0.0.0.0", port: int = 5000) -> None:
    """
    Start de Flask-webserver in een daemon-thread.

    Een daemon-thread stopt automatisch als het hoofdprogramma stopt.
    """
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, use_reloader=False, threaded=True),
        daemon=True,
        name="web-server",
    )
    thread.start()
    logger.debug("Webserver gestart op %s:%d", host, port)


# ─── Hulpfuncties ─────────────────────────────────────────────────────────────

def _verwerk_instellingen(form: dict, config: dict, lock: threading.Lock) -> list[str]:
    """
    Valideert en verwerkt formuliergegevens van /instellingen.

    Valide waarden worden direct in de gedeelde config-dict bijgewerkt.
    Ongeldige waarden worden als foutmelding teruggegeven.

    Returns:
        Lijst met foutmeldingen (leeg als alles geldig is).
    """
    fouten = []

    def lees_float(veldnaam: str, minimum: float, maximum: float) -> float | None:
        waarde = form.get(veldnaam, "").strip()
        try:
            getal = float(waarde)
            if not (minimum <= getal <= maximum):
                fouten.append(
                    f"{veldnaam}: waarde {getal} moet tussen {minimum} en {maximum} liggen."
                )
                return None
            return getal
        except ValueError:
            fouten.append(f"{veldnaam}: '{waarde}' is geen geldig getal.")
            return None

    def lees_int(veldnaam: str, minimum: int, maximum: int) -> int | None:
        waarde = form.get(veldnaam, "").strip()
        try:
            getal = int(waarde)
            if not (minimum <= getal <= maximum):
                fouten.append(
                    f"{veldnaam}: waarde {getal} moet tussen {minimum} en {maximum} liggen."
                )
                return None
            return getal
        except ValueError:
            fouten.append(f"{veldnaam}: '{waarde}' is geen geldig geheel getal.")
            return None

    # Verzamel nieuwe waarden (None = validatiefout)
    hw_ip               = form.get("hw_ip", "").strip() or None
    zaptec_install_id   = form.get("zaptec_installation_id", "").strip() or None
    zaptec_charger_id   = form.get("zaptec_charger_id", "").strip() or None
    fase_modus          = form.get("fase_modus", "").strip()
    spanning_v          = lees_float("spanning_v", 100, 400)
    min_stroom_a        = lees_float("min_stroom_a", 6, 32)
    max_stroom_a        = lees_float("max_stroom_a", 6, 63)
    veiligheidsbuffer_w = lees_float("veiligheidsbuffer_w", 0, 10000)
    fase_wissel_wacht   = lees_int("fase_wissel_wachttijd_s", 60, 3600)
    fase_wissel_hyst    = lees_float("fase_wissel_hysterese_w", 0, 5000)
    update_interval_s   = lees_int("update_interval_s", 60, 3600)
    state_poll_s        = lees_int("state_poll_interval_s", 10, 300)
    hw_poll_s           = lees_int("homewizard_poll_interval_s", 5, 300)
    web_poort           = lees_int("web_poort", 1024, 65535)

    # Valideer fase_modus
    if fase_modus not in ("auto", "1", "3"):
        fouten.append("fase_modus: moet 'auto', '1' of '3' zijn.")
        fase_modus = None

    # Valideer min ≤ max stroom
    if min_stroom_a is not None and max_stroom_a is not None:
        if min_stroom_a > max_stroom_a:
            fouten.append("min_stroom_a mag niet groter zijn dan max_stroom_a.")

    if fouten:
        return fouten

    # Alles geldig: update de gedeelde config dict in-place
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
        if fase_modus is not None:
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
        if web_poort is not None:
            config["web"]["poort"] = web_poort

    return []


def _schrijf_config(config: dict) -> None:
    """Schrijft de huidige config-dict terug naar config/config.yaml."""
    pad = Path("config/config.yaml")
    with open(pad, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _lees_config_van_schijf() -> dict:
    """Leest config.yaml van schijf en retourneert de inhoud als dict."""
    with open("config/config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _lees_laatste_logregels(log_pad: str, aantal: int = 50) -> list[str]:
    """Leest de laatste N regels van het logbestand."""
    pad = Path(log_pad)
    if not pad.exists():
        return ["(logbestand nog niet aangemaakt)"]
    try:
        with open(pad, encoding="utf-8") as f:
            regels = f.readlines()
        return [r.rstrip() for r in regels[-aantal:]]
    except OSError as e:
        return [f"(logbestand kan niet gelezen worden: {e})"]
