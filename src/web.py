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
import sys
import threading
import time
from pathlib import Path

import yaml
from flask import Flask, render_template, request, redirect, url_for, jsonify

from src.database import haal_recente_metingen_op, haal_recente_events_op

logger = logging.getLogger(__name__)


def maak_app(state: dict, config: dict, db_pad: str, zaptec=None) -> Flask:
    """
    Maakt en configureert de Flask-applicatie.

    Args:
        state:   Gedeelde state-dict (wordt live bijgewerkt door de hoofdlus).
        config:  Gedeelde config-dict (in-place bijgewerkt bij instellingen opslaan).
        db_pad:  Pad naar het SQLite-databasebestand.
        zaptec:  Optionele ZaptecClient-instantie (voor herstart-veiligheidsstap en API-tester).

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
            "laadmodus":         state.get("laadmodus"),
            "standby_modus":     state.get("standby_modus", False),
            "stabilisatie_actief": state.get("stabilisatie_tot", 0) > time.time(),
            "metingen":          haal_recente_metingen_op(db_pad, limiet=20),
            "events":            haal_recente_events_op(db_pad, limiet=10),
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

    # ── Herstart ──────────────────────────────────────────────────────────────

    @app.route("/herstart", methods=["POST"])
    def herstart():
        """
        Herstart het Python-proces via os.execv.

        Veiligheidsstap: als er een auto laadt en het systeem actief is,
        wordt eerst availableCurrent=-1 gestuurd zodat Zaptec de standaard
        hervat tijdens de herstart.
        """
        # Veiligheidsstap: herstel Zaptec-standaard zodat de lader niet vastloopt
        if state.get("auto_aangesloten") and state.get("actief") and zaptec:
            try:
                zaptec.set_installation_settings(
                    config["zaptec"]["installation_id"], -1.0
                )
                logger.info("Herstart: Zaptec-standaard hersteld (availableCurrent=-1).")
            except Exception as e:
                logger.warning("Herstart-veiligheidsstap mislukt: %s", e)

        logger.info("Herstart gestart via webinterface.")

        def _herstart():
            time.sleep(1)
            os.execv(sys.executable, [sys.executable] + sys.argv)

        threading.Thread(target=_herstart, daemon=True).start()
        return jsonify({"status": "herstarten"})

    # ── Debug ─────────────────────────────────────────────────────────────────

    @app.route("/debug")
    def debug():
        """Toont debug-informatie: recente logs, events en huidige state.
        Alleen toegankelijk als debug_modus ingeschakeld is."""
        if not config.get("opslag", {}).get("debug_modus", False):
            return redirect(url_for("index"))
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

    @app.route("/api/debug/call", methods=["POST"])
    def debug_call():
        """
        Voert een Zaptec API-aanroep uit voor diagnose en handmatig testen.
        Alleen toegankelijk als debug_modus ingeschakeld is.
        Schrijf-calls vereisen {"bevestigd": true} in de request body.
        """
        if not config.get("opslag", {}).get("debug_modus", False):
            return jsonify({"fout": "debug_modus_uit"}), 403
        if not zaptec:
            return jsonify({"fout": "zaptec_niet_beschikbaar"}), 503

        data = request.get_json(force=True, silent=True) or {}
        call = data.get("call", "")
        params = data.get("params", {})
        bevestigd = data.get("bevestigd", False)

        SCHRIJF_CALLS = {"set_installation_settings"}

        if call in SCHRIJF_CALLS and not bevestigd:
            return jsonify({"fout": "bevestiging_vereist",
                            "bericht": "Voeg 'bevestigd: true' toe om te schrijven."}), 400

        charger_id      = config["zaptec"]["charger_id"]
        installation_id = config["zaptec"]["installation_id"]

        try:
            if call == "get_charger_state":
                result = zaptec.get_charger_state(charger_id)
            elif call == "get_charger_operation_mode":
                result = zaptec.get_charger_operation_mode(charger_id)
            elif call == "get_current_phases":
                result = zaptec.get_current_phases(charger_id)
            elif call == "is_car_connected":
                result = zaptec.is_car_connected(charger_id)
            elif call == "get_installation_mode":
                result = zaptec.get_installation_mode(installation_id)
            elif call == "set_installation_settings":
                available_current = float(params.get("available_current", -1))
                drie_naar_een = params.get("drie_naar_een_fase_stroom")
                if drie_naar_een is not None:
                    drie_naar_een = float(drie_naar_een)
                zaptec.set_installation_settings(installation_id, available_current, drie_naar_een)
                result = {"ok": True, "available_current": available_current,
                          "drie_naar_een_fase_stroom": drie_naar_een}
            else:
                return jsonify({"fout": f"onbekende call: {call!r}"}), 400

            return jsonify({"call": call, "resultaat": result})

        except Exception as e:
            logger.warning("API-tester fout bij call %r: %s", call, e)
            return jsonify({"fout": str(e)}), 500

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
    noodoverride_actief  = "noodoverride_actief" in form
    noodoverride_drempel = lees_float("noodoverride_drempel_w", 0, 100000)
    noodoverride_wacht   = lees_int("noodoverride_wachttijd_s", 10, 3600)
    update_interval_s   = lees_int("update_interval_s", 60, 3600)
    state_poll_s        = lees_int("state_poll_interval_s", 10, 300)
    hw_poll_s           = lees_int("homewizard_poll_interval_s", 5, 300)
    live_stroom_bron    = form.get("live_stroom_bron", "").strip()
    if live_stroom_bron not in ("auto", "708", "meting", "uit"):
        fouten.append("live_stroom_bron: moet 'auto', '708', 'meting' of 'uit' zijn.")
        live_stroom_bron = None
    fase_bevestig_wacht = lees_int("fase_wissel_bevestig_wacht_s", 30, 600)
    web_poort           = lees_int("web_poort", 1024, 65535)
    debug_modus         = "debug_modus" in form
    log_niveau          = form.get("log_niveau", "").strip().upper()
    if log_niveau not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        fouten.append("log_niveau: moet DEBUG, INFO, WARNING of ERROR zijn.")
        log_niveau = None

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
        if live_stroom_bron is not None:
            config["zaptec"]["live_stroom_bron"] = live_stroom_bron
        if fase_bevestig_wacht is not None:
            config["zaptec"]["fase_wissel_bevestig_wacht_s"] = fase_bevestig_wacht
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
        config["laadregeling"]["noodoverride_actief"] = noodoverride_actief
        if noodoverride_drempel is not None:
            config["laadregeling"]["noodoverride_drempel_w"] = noodoverride_drempel
        if noodoverride_wacht is not None:
            config["laadregeling"]["noodoverride_wachttijd_s"] = noodoverride_wacht
        if web_poort is not None:
            config["web"]["poort"] = web_poort
        config["opslag"]["debug_modus"] = debug_modus
        if log_niveau:
            config["opslag"]["log_niveau"] = log_niveau
            logging.getLogger().setLevel(getattr(logging, log_niveau))

    return []


def _schrijf_config(config: dict) -> None:
    """Schrijft gewijzigde waarden terug naar config/config.yaml, met behoud van commentaar."""
    import re

    pad = Path("config/config.yaml")
    with open(pad, encoding="utf-8") as f:
        inhoud = f.read()

    def vervang(tekst: str, sleutel: str, waarde) -> str:
        """Vervangt de waarde van `sleutel:` op zijn regel, behoudt trailing commentaar."""
        if isinstance(waarde, bool):
            nieuw = "true" if waarde else "false"
        elif isinstance(waarde, str):
            nieuw = f"'{waarde}'"
        elif isinstance(waarde, float) and waarde == int(waarde):
            nieuw = str(int(waarde))   # 6.0 → 6, 230.0 → 230
        else:
            nieuw = str(waarde)
        patroon = rf'^(\s*{re.escape(sleutel)}:\s*)([^\n#]+?)(\s*(?:#[^\n]*)?)$'
        return re.sub(patroon, rf'\g<1>{nieuw}\3', tekst, flags=re.MULTILINE)

    inhoud = vervang(inhoud, "ip",                      config["homewizard"]["ip"])
    inhoud = vervang(inhoud, "poll_interval_s",         config["homewizard"]["poll_interval_s"])
    inhoud = vervang(inhoud, "installation_id",         config["zaptec"]["installation_id"])
    inhoud = vervang(inhoud, "charger_id",              config["zaptec"]["charger_id"])
    inhoud = vervang(inhoud, "update_interval_s",       config["zaptec"]["update_interval_s"])
    inhoud = vervang(inhoud, "state_poll_interval_s",   config["zaptec"]["state_poll_interval_s"])
    inhoud = vervang(inhoud, "live_stroom_bron",        config["zaptec"].get("live_stroom_bron", "auto"))
    inhoud = vervang(inhoud, "fase_wissel_bevestig_wacht_s", config["zaptec"].get("fase_wissel_bevestig_wacht_s", 120))
    inhoud = vervang(inhoud, "fase_modus",              config["laadregeling"]["fase_modus"])
    inhoud = vervang(inhoud, "spanning_v",              config["laadregeling"]["spanning_v"])
    inhoud = vervang(inhoud, "min_stroom_a",            config["laadregeling"]["min_stroom_a"])
    inhoud = vervang(inhoud, "max_stroom_a",            config["laadregeling"]["max_stroom_a"])
    inhoud = vervang(inhoud, "veiligheidsbuffer_w",     config["laadregeling"]["veiligheidsbuffer_w"])
    inhoud = vervang(inhoud, "fase_wissel_wachttijd_s", config["laadregeling"]["fase_wissel_wachttijd_s"])
    inhoud = vervang(inhoud, "fase_wissel_hysterese_w", config["laadregeling"]["fase_wissel_hysterese_w"])
    inhoud = vervang(inhoud, "noodoverride_actief",     config["laadregeling"]["noodoverride_actief"])
    inhoud = vervang(inhoud, "noodoverride_drempel_w",  config["laadregeling"]["noodoverride_drempel_w"])
    inhoud = vervang(inhoud, "noodoverride_wachttijd_s", config["laadregeling"]["noodoverride_wachttijd_s"])
    inhoud = vervang(inhoud, "poort",                   config["web"]["poort"])
    inhoud = vervang(inhoud, "debug_modus",             config["opslag"]["debug_modus"])
    inhoud = vervang(inhoud, "log_niveau",              config["opslag"]["log_niveau"])

    with open(pad, "w", encoding="utf-8") as f:
        f.write(inhoud)


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
