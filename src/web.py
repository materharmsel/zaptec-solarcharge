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

import datetime
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml
from flask import Flask, render_template, request, redirect, url_for, jsonify

from src.database import (
    haal_recente_metingen_op,
    haal_recente_events_op,
    haal_ongeziene_sessie_op,
    markeer_popup_getoond,
    haal_sessies_op,
    haal_metingen_tijdvenster,
    haal_sessie_metingen,
)

logger = logging.getLogger(__name__)

# Projectroot: twee niveaus omhoog vanuit src/web.py
_PROJECT_PAD = Path(__file__).parent.parent


def _herstart_service() -> None:
    """
    Herstart de systemd-service via sudo systemctl.
    Werkt alleen als de sudoers-regel is aangemaakt (via setup.sh of update.sh).
    Roep dit altijd vanuit een daemon-thread aan zodat Flask eerst de response kan sturen.
    """
    time.sleep(0.5)
    subprocess.run(
        ["sudo", "systemctl", "restart", "zaptec-solarcharge"],
        capture_output=True,
    )


def _git(args: list, timeout: int = 30) -> str:
    """
    Voert een git-commando uit in de projectmap en geeft stdout terug.
    Bij fout: geeft lege string terug (nooit crash).
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True,
            cwd=str(_PROJECT_PAD), timeout=timeout,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _maak_backup() -> str:
    """
    Maakt een backup van config.yaml, .env en de database.
    Retourneert de naam van de backup-map (niet het volledige pad).
    """
    naam = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_pad = _PROJECT_PAD / "backups" / naam
    backup_pad.mkdir(parents=True, exist_ok=True)

    for bron, doel in [
        (_PROJECT_PAD / "config" / "config.yaml",           backup_pad / "config.yaml"),
        (_PROJECT_PAD / "config" / ".env",                  backup_pad / ".env"),
        (_PROJECT_PAD / "data"   / "zaptec-solarcharge.db", backup_pad / "zaptec-solarcharge.db"),
    ]:
        if bron.exists():
            shutil.copy2(str(bron), str(doel))

    return naam


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
            "regelaar_model":       config["laadregeling"].get("regelaar_model", "legacy"),
            "doel_net_vermogen_w":  config["laadregeling"].get("doel_net_vermogen_w", 0),
            "huisprofiel":          config["laadregeling"].get("huisprofiel", "normaal"),
            "standby_modus":        state.get("standby_modus", False),
            "stabilisatie_actief": state.get("stabilisatie_tot", 0) > time.time(),
            "max_fase_schakelingen":   state.get("max_fase_schakelingen"),
            "fase_wissel_geblokkeerd": state.get("fase_wissel_geblokkeerd", False),
            "fase_wissel_bezig":       state.get("fase_wissel_bezig", False),
            "ema_net_vermogen_w":      state.get("ema_net_vermogen_w"),
            "metingen":          haal_recente_metingen_op(db_pad, limiet=20),
            "events":            haal_recente_events_op(db_pad, limiet=10),
            "nieuwe_sessie":     haal_ongeziene_sessie_op(db_pad),
        })

    @app.route("/api/metingen")
    def api_metingen():
        """JSON-lijst van metingen over een tijdvenster (voor grafiek tijdselectie)."""
        minuten = request.args.get("minuten", 30, type=int)
        minuten = max(5, min(minuten, 180))  # clamp: 5–180 minuten
        return jsonify(haal_metingen_tijdvenster(db_pad, minuten=minuten))

    # ── Regelaar aan/uit ──────────────────────────────────────────────────────

    @app.route("/toggle", methods=["POST"])
    def toggle():
        """Zet de regelaar aan of uit."""
        state["actief"] = not state["actief"]
        status = "aan" if state["actief"] else "uit"
        logger.info("Regelaar %s gezet via webinterface.", status)
        return redirect(url_for("index"))

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
            model = str(data.get("regelaar_model", "")).strip()
            if model in ("legacy", "solarflow"):
                config["laadregeling"]["regelaar_model"] = model

            preset = str(data.get("doelinstelling_preset", "")).strip()
            if preset in _preset_map:
                config["laadregeling"]["doel_net_vermogen_w"] = _preset_map[preset]

            profiel = str(data.get("huisprofiel", "")).strip()
            if profiel in _profiel_map:
                config["laadregeling"]["huisprofiel"] = profiel
                for k, v in _profiel_map[profiel].items():
                    config["laadregeling"][k] = v

        _schrijf_config(config)
        logger.info("Quick-settings bijgewerkt via dashboard: %s", data)
        return jsonify({"ok": True})

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
        Herstart de systemd-service via sudo systemctl restart.

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
        threading.Thread(target=_herstart_service, daemon=True).start()
        return jsonify({"status": "herstarten"})

    # ── Versiebeheer ──────────────────────────────────────────────────────────

    @app.route("/beheer")
    def beheer():
        """Versiebeheer-pagina: versie, update, branch, backup en rollback."""
        return render_template("beheer.html", state=state, config=config)

    @app.route("/api/versie-info")
    def versie_info():
        """
        Geeft versie-informatie en beschikbare backups terug als JSON.
        Gebruik ?test=1 om update_beschikbaar=true te forceren (voor UI-tests).
        """
        test_modus = request.args.get("test") == "1"

        # Versienummer
        versie_pad = _PROJECT_PAD / "VERSION"
        versie = versie_pad.read_text(encoding="utf-8").strip() if versie_pad.exists() else "onbekend"

        # Huidige branch
        branch = _git(["branch", "--show-current"]) or "onbekend"

        # Laatste commit
        commit_raw = _git(["log", "-1", "--format=%ci|%s"])
        if "|" in commit_raw:
            commit_datum, commit_bericht = commit_raw.split("|", 1)
            commit_datum = commit_datum[:16]  # "YYYY-MM-DD HH:MM"
        else:
            commit_datum, commit_bericht = "", commit_raw

        # Update-check: vergelijk HEAD met origin/<branch>
        update_beschikbaar = False
        if test_modus:
            update_beschikbaar = True
        elif branch not in ("onbekend", ""):
            # Stil fetchen (geen fout als er geen internet is)
            try:
                subprocess.run(
                    ["git", "fetch", "origin", branch],
                    capture_output=True, cwd=str(_PROJECT_PAD), timeout=10,
                )
                log_result = _git(["log", f"HEAD..origin/{branch}", "--oneline"])
                update_beschikbaar = bool(log_result)
            except Exception:
                pass

        # Backups ophalen
        backups = []
        backups_pad = _PROJECT_PAD / "backups"
        if backups_pad.exists():
            for map_pad in sorted(backups_pad.iterdir(), reverse=True):
                if map_pad.is_dir():
                    bestanden = []
                    if (map_pad / "config.yaml").exists():
                        bestanden.append("config.yaml")
                    if (map_pad / ".env").exists():
                        bestanden.append(".env")
                    if (map_pad / "zaptec-solarcharge.db").exists():
                        bestanden.append("database")
                    backups.append({"naam": map_pad.name, "bestanden": bestanden})
            backups = backups[:20]  # max 20 tonen

        return jsonify({
            "versie": versie,
            "branch": branch,
            "laatste_commit": f"{commit_datum} — {commit_bericht}" if commit_datum else commit_bericht,
            "update_beschikbaar": update_beschikbaar,
            "backups": backups,
        })

    @app.route("/backup", methods=["POST"])
    def backup():
        """Maakt direct een backup van config, .env en database."""
        try:
            naam = _maak_backup()
            logger.info("Handmatige backup aangemaakt: %s", naam)
            return jsonify({"status": "ok", "naam": naam})
        except Exception as e:
            logger.error("Backup mislukt: %s", e)
            return jsonify({"status": "fout", "fout": str(e)}), 500

    @app.route("/update", methods=["POST"])
    def update():
        """Maakt een backup, voert git pull uit en herstart de service."""
        def _uitvoeren():
            try:
                naam = _maak_backup()
                logger.info("Update: backup aangemaakt: %s", naam)
            except Exception as e:
                logger.warning("Update: backup mislukt: %s", e)

            pip_pad = _PROJECT_PAD / "venv" / "bin" / "pip"
            _git(["pull"])
            if pip_pad.exists():
                try:
                    subprocess.run(
                        [str(pip_pad), "install", "-r",
                         str(_PROJECT_PAD / "requirements.txt"), "-q"],
                        capture_output=True, cwd=str(_PROJECT_PAD), timeout=120,
                    )
                except Exception as e:
                    logger.warning("Update: pip install mislukt: %s", e)

            logger.info("Update uitgevoerd via webinterface — service herstart.")
            _herstart_service()

        threading.Thread(target=_uitvoeren, daemon=True).start()
        return jsonify({"status": "bezig"})

    @app.route("/wissel-branch", methods=["POST"])
    def wissel_branch():
        """Wisselt naar een andere git-branch, maakt een backup en herstart."""
        data = request.get_json(force=True, silent=True) or {}
        doel_branch = data.get("branch", "").strip()

        if doel_branch not in ("main", "beta"):
            return jsonify({"status": "fout", "fout": "Ongeldige branch (alleen 'main' of 'beta')"}), 400

        def _uitvoeren():
            try:
                naam = _maak_backup()
                logger.info("Branch-wissel: backup aangemaakt: %s", naam)
            except Exception as e:
                logger.warning("Branch-wissel: backup mislukt: %s", e)

            _git(["fetch", "origin"])
            _git(["checkout", doel_branch])
            _git(["pull"])

            pip_pad = _PROJECT_PAD / "venv" / "bin" / "pip"
            if pip_pad.exists():
                try:
                    subprocess.run(
                        [str(pip_pad), "install", "-r",
                         str(_PROJECT_PAD / "requirements.txt"), "-q"],
                        capture_output=True, cwd=str(_PROJECT_PAD), timeout=120,
                    )
                except Exception as e:
                    logger.warning("Branch-wissel: pip install mislukt: %s", e)

            logger.info("Branch gewisseld naar '%s' via webinterface — service herstart.", doel_branch)
            _herstart_service()

        threading.Thread(target=_uitvoeren, daemon=True).start()
        return jsonify({"status": "bezig", "branch": doel_branch})

    @app.route("/rollback", methods=["POST"])
    def rollback():
        """Herstelt een backup en herstart de service."""
        data = request.get_json(force=True, silent=True) or {}
        backup_naam = data.get("naam", "").strip()

        # Beveiligingscheck: geen padtraversal
        if not backup_naam or "/" in backup_naam or "\\" in backup_naam or ".." in backup_naam:
            return jsonify({"status": "fout", "fout": "Ongeldige backup-naam"}), 400

        backup_pad = _PROJECT_PAD / "backups" / backup_naam
        if not backup_pad.is_dir():
            return jsonify({"status": "fout", "fout": "Backup niet gevonden"}), 404

        def _uitvoeren():
            for bron, doel in [
                (backup_pad / "config.yaml",           _PROJECT_PAD / "config" / "config.yaml"),
                (backup_pad / ".env",                  _PROJECT_PAD / "config" / ".env"),
                (backup_pad / "zaptec-solarcharge.db", _PROJECT_PAD / "data"   / "zaptec-solarcharge.db"),
            ]:
                if bron.exists():
                    shutil.copy2(str(bron), str(doel))

            logger.info("Rollback naar backup '%s' uitgevoerd via webinterface — service herstart.", backup_naam)
            _herstart_service()

        threading.Thread(target=_uitvoeren, daemon=True).start()
        return jsonify({"status": "bezig", "naam": backup_naam})

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
            elif call == "get_charger_details":
                result = zaptec.get_charger_details(charger_id)
            elif call == "get_charger_max_phases":
                result = zaptec.get_charger_max_phases(charger_id)
            elif call == "get_circuit_details":
                charger_data = zaptec.get_charger_details(charger_id)
                circuit_id = charger_data.get("CircuitId")
                if circuit_id:
                    result = zaptec._get(f"/api/circuits/{circuit_id}")
                else:
                    result = {"fout": "geen CircuitId in charger-details"}
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

    # ── Sessies ───────────────────────────────────────────────────────────────

    @app.route("/sessies")
    def sessies_pagina():
        """Sessies-overzichtspagina."""
        return render_template("sessies.html")

    @app.route("/api/sessies")
    def api_sessies():
        """JSON-lijst van afgesloten sessies, gepagineerd."""
        pagina = request.args.get("pagina", 1, type=int)
        return jsonify(haal_sessies_op(db_pad, pagina))

    @app.route("/api/sessies/<int:sessie_id>/gezien", methods=["POST"])
    def sessie_gezien(sessie_id):
        """Markeert een sessie als gezien zodat de popup niet meer automatisch verschijnt."""
        markeer_popup_getoond(db_pad, sessie_id)
        return jsonify({"ok": True})

    @app.route("/api/sessies/<int:sessie_id>/metingen")
    def api_sessie_metingen(sessie_id):
        """Metingen en events van één sessie, voor de mini-grafiek op de sessies-pagina."""
        return jsonify(haal_sessie_metingen(db_pad, sessie_id=sessie_id))

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

def _lees_float(form: dict, fouten: list, veld: str, minimum: float, maximum: float) -> float | None:
    """Leest en valideert een float-veld uit een formulier."""
    waarde = form.get(veld, "").strip()
    try:
        getal = float(waarde)
        if not (minimum <= getal <= maximum):
            fouten.append(
                f"{veld}: waarde {getal} moet tussen {minimum} en {maximum} liggen."
            )
            return None
        return getal
    except ValueError:
        fouten.append(f"{veld}: '{waarde}' is geen geldig getal.")
        return None


def _lees_int(form: dict, fouten: list, veld: str, minimum: int, maximum: int) -> int | None:
    """Leest en valideert een integer-veld uit een formulier."""
    waarde = form.get(veld, "").strip()
    try:
        getal = int(waarde)
        if not (minimum <= getal <= maximum):
            fouten.append(
                f"{veld}: waarde {getal} moet tussen {minimum} en {maximum} liggen."
            )
            return None
        return getal
    except ValueError:
        fouten.append(f"{veld}: '{waarde}' is geen geldig geheel getal.")
        return None


def _verwerk_instellingen(form: dict, config: dict, lock: threading.Lock) -> list[str]:
    """
    Valideert en verwerkt formuliergegevens van /instellingen.

    Valide waarden worden direct in de gedeelde config-dict bijgewerkt.
    Ongeldige waarden worden als foutmelding teruggegeven.

    Returns:
        Lijst met foutmeldingen (leeg als alles geldig is).
    """
    fouten = []

    # Verzamel nieuwe waarden (None = validatiefout)
    hw_ip               = form.get("hw_ip", "").strip() or None
    zaptec_install_id   = form.get("zaptec_installation_id", "").strip() or None
    zaptec_charger_id   = form.get("zaptec_charger_id", "").strip() or None
    fase_modus          = form.get("fase_modus", "").strip()
    spanning_v          = _lees_float(form, fouten, "spanning_v", 100, 400)
    min_stroom_a        = _lees_float(form, fouten, "min_stroom_a", 6, 32)
    max_stroom_a        = _lees_float(form, fouten, "max_stroom_a", 6, 63)
    veiligheidsbuffer_w = _lees_float(form, fouten, "veiligheidsbuffer_w", 0, 10000)
    fase_wissel_wacht   = _lees_int(form, fouten, "fase_wissel_wachttijd_s", 60, 3600)
    fase_wissel_hyst    = _lees_float(form, fouten, "fase_wissel_hysterese_w", 0, 5000)
    noodoverride_actief          = "noodoverride_actief" in form
    noodoverride_drempel         = _lees_float(form, fouten, "noodoverride_drempel_w", 0, 100000)
    noodoverride_wacht           = _lees_int(form, fouten, "noodoverride_wachttijd_s", 10, 3600)
    noodoverride_export_drempel  = _lees_float(form, fouten, "noodoverride_export_drempel_w", -100000, -1)
    update_interval_s   = _lees_int(form, fouten, "update_interval_s", 60, 3600)
    state_poll_s        = _lees_int(form, fouten, "state_poll_interval_s", 10, 300)
    hw_poll_s           = _lees_int(form, fouten, "homewizard_poll_interval_s", 5, 300)
    live_stroom_bron    = form.get("live_stroom_bron", "").strip()
    if live_stroom_bron not in ("auto", "708", "meting", "uit"):
        fouten.append("live_stroom_bron: moet 'auto', '708', 'meting' of 'uit' zijn.")
        live_stroom_bron = None
    fase_bevestig_wacht = _lees_int(form, fouten, "fase_wissel_bevestig_wacht_s", 30, 600)
    web_poort           = _lees_int(form, fouten, "web_poort", 1024, 65535)
    bewaarperiode_dagen = _lees_int(form, fouten, "bewaarperiode_dagen", 7, 365)
    debug_modus         = "debug_modus" in form
    log_niveau          = form.get("log_niveau", "").strip().upper()
    if log_niveau not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        fouten.append("log_niveau: moet DEBUG, INFO, WARNING of ERROR zijn.")
        log_niveau = None

    regelaar_model = form.get("regelaar_model", "").strip()
    if regelaar_model not in ("legacy", "solarflow"):
        fouten.append("regelaar_model: moet 'legacy' of 'solarflow' zijn.")
        regelaar_model = None

    # Doelinstelling preset → doel_net_vermogen_w
    # Bij "aangepast": gebruik het handmatig ingevulde geavanceerde veld
    _preset_map = {"50": 50, "0": 0, "-100": -100, "-200": -200}
    doelinstelling_preset = form.get("doelinstelling_preset", "").strip()
    if doelinstelling_preset in _preset_map:
        doel_net_vermogen_w = _preset_map[doelinstelling_preset]
    elif doelinstelling_preset == "aangepast":
        doel_net_vermogen_w = _lees_int(form, fouten, "doel_net_vermogen_w_geavanceerd", -500, 300)
    else:
        doel_net_vermogen_w = None

    # Huisprofiel preset → drie EMA-parameters tegelijk
    _profiel_map = {
        "rustig":  dict(ema_alpha_min=0.08, ema_alpha_max=0.4, ema_adaptief_drempel_w=500),
        "normaal": dict(ema_alpha_min=0.10, ema_alpha_max=0.6, ema_adaptief_drempel_w=400),
        "druk":    dict(ema_alpha_min=0.10, ema_alpha_max=0.7, ema_adaptief_drempel_w=350),
    }
    huisprofiel = form.get("huisprofiel", "").strip()
    if huisprofiel and huisprofiel not in _profiel_map:
        fouten.append("huisprofiel: moet 'rustig', 'normaal' of 'druk' zijn.")
        huisprofiel = None

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
        if regelaar_model is not None:
            config["laadregeling"]["regelaar_model"] = regelaar_model
        if doel_net_vermogen_w is not None:
            config["laadregeling"]["doel_net_vermogen_w"] = doel_net_vermogen_w
        if huisprofiel in _profiel_map:
            # Geldig profiel: gebruik preset-waarden
            config["laadregeling"]["huisprofiel"] = huisprofiel
            for k, v in _profiel_map[huisprofiel].items():
                config["laadregeling"][k] = v
        else:
            # "aangepast" of leeg: lees individuele EMA-velden uit het formulier
            config["laadregeling"]["huisprofiel"] = "aangepast"
            ema_alpha_min = _lees_float(form, fouten, "ema_alpha_min", 0.01, 0.5)
            ema_alpha_max = _lees_float(form, fouten, "ema_alpha_max", 0.1, 1.0)
            ema_drempel   = _lees_int(form, fouten, "ema_adaptief_drempel_w", 100, 2000)
            scoring_sigma = _lees_int(form, fouten, "scoring_sigma_w", 50, 1000)
            if ema_alpha_min is not None:
                config["laadregeling"]["ema_alpha_min"] = ema_alpha_min
            if ema_alpha_max is not None:
                config["laadregeling"]["ema_alpha_max"] = ema_alpha_max
            if ema_drempel is not None:
                config["laadregeling"]["ema_adaptief_drempel_w"] = ema_drempel
            if scoring_sigma is not None:
                config["laadregeling"]["scoring_sigma_w"] = scoring_sigma
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
        if noodoverride_export_drempel is not None:
            config["laadregeling"]["noodoverride_export_drempel_w"] = noodoverride_export_drempel
        if web_poort is not None:
            config["web"]["poort"] = web_poort
        if bewaarperiode_dagen is not None:
            config["opslag"]["bewaarperiode_dagen"] = bewaarperiode_dagen
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
    inhoud = vervang(inhoud, "regelaar_model",           config["laadregeling"].get("regelaar_model", "legacy"))
    inhoud = vervang(inhoud, "doel_net_vermogen_w",      config["laadregeling"].get("doel_net_vermogen_w", 0))
    inhoud = vervang(inhoud, "huisprofiel",              config["laadregeling"].get("huisprofiel", "normaal"))
    inhoud = vervang(inhoud, "ema_alpha_min",            config["laadregeling"].get("ema_alpha_min", 0.1))
    inhoud = vervang(inhoud, "ema_alpha_max",            config["laadregeling"].get("ema_alpha_max", 0.6))
    inhoud = vervang(inhoud, "ema_adaptief_drempel_w",   config["laadregeling"].get("ema_adaptief_drempel_w", 400))
    inhoud = vervang(inhoud, "scoring_sigma_w",          config["laadregeling"].get("scoring_sigma_w", 150))
    inhoud = vervang(inhoud, "fase_modus",              config["laadregeling"]["fase_modus"])
    inhoud = vervang(inhoud, "spanning_v",              config["laadregeling"]["spanning_v"])
    inhoud = vervang(inhoud, "min_stroom_a",            config["laadregeling"]["min_stroom_a"])
    inhoud = vervang(inhoud, "max_stroom_a",            config["laadregeling"]["max_stroom_a"])
    inhoud = vervang(inhoud, "veiligheidsbuffer_w",     config["laadregeling"]["veiligheidsbuffer_w"])
    inhoud = vervang(inhoud, "fase_wissel_wachttijd_s", config["laadregeling"]["fase_wissel_wachttijd_s"])
    inhoud = vervang(inhoud, "fase_wissel_hysterese_w", config["laadregeling"]["fase_wissel_hysterese_w"])
    inhoud = vervang(inhoud, "noodoverride_actief",     config["laadregeling"]["noodoverride_actief"])
    inhoud = vervang(inhoud, "noodoverride_drempel_w",  config["laadregeling"]["noodoverride_drempel_w"])
    inhoud = vervang(inhoud, "noodoverride_wachttijd_s",    config["laadregeling"]["noodoverride_wachttijd_s"])
    inhoud = vervang(inhoud, "noodoverride_export_drempel_w", config["laadregeling"]["noodoverride_export_drempel_w"])
    inhoud = vervang(inhoud, "poort",                       config["web"]["poort"])
    inhoud = vervang(inhoud, "debug_modus",             config["opslag"]["debug_modus"])
    inhoud = vervang(inhoud, "log_niveau",              config["opslag"]["log_niveau"])
    inhoud = vervang(inhoud, "bewaarperiode_dagen",     config["opslag"].get("bewaarperiode_dagen", 30))

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


def _verwerk_laadregeling(form: dict, config: dict, lock: threading.Lock) -> list[str]:
    """Valideert en slaat laadregeling-instellingen op."""
    fouten = []

    regelaar_model = form.get("regelaar_model", "").strip()
    if regelaar_model not in ("legacy", "solarflow"):
        fouten.append("regelaar_model: moet 'legacy' of 'solarflow' zijn.")
        regelaar_model = None

    fase_modus = form.get("fase_modus", "").strip()
    if fase_modus not in ("auto", "1", "3"):
        fouten.append("fase_modus: moet 'auto', '1' of '3' zijn.")
        fase_modus = None

    spanning_v          = _lees_float(form, fouten, "spanning_v", 100, 400)
    min_stroom_a        = _lees_float(form, fouten, "min_stroom_a", 6, 32)
    max_stroom_a        = _lees_float(form, fouten, "max_stroom_a", 6, 63)
    veiligheidsbuffer_w = _lees_float(form, fouten, "veiligheidsbuffer_w", 0, 10000)
    fase_wissel_wacht   = _lees_int(form, fouten, "fase_wissel_wachttijd_s", 60, 3600)
    fase_wissel_hyst    = _lees_float(form, fouten, "fase_wissel_hysterese_w", 0, 5000)
    fase_bevestig_wacht = _lees_int(form, fouten, "fase_wissel_bevestig_wacht_s", 30, 600)
    noodoverride_actief         = "noodoverride_actief" in form
    noodoverride_drempel        = _lees_float(form, fouten, "noodoverride_drempel_w", 0, 100000)
    noodoverride_wacht          = _lees_int(form, fouten, "noodoverride_wachttijd_s", 10, 3600)
    noodoverride_export_drempel = _lees_float(form, fouten, "noodoverride_export_drempel_w", -100000, -1)

    _preset_map = {"50": 50, "0": 0, "-100": -100, "-200": -200}
    doelinstelling_preset = form.get("doelinstelling_preset", "").strip()
    if doelinstelling_preset in _preset_map:
        doel_net_vermogen_w = _preset_map[doelinstelling_preset]
    elif doelinstelling_preset == "aangepast":
        doel_net_vermogen_w = _lees_int(form, fouten, "doel_net_vermogen_w_geavanceerd", -500, 300)
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
            for veld, mn, mx in [("ema_alpha_min", 0.01, 0.5), ("ema_alpha_max", 0.1, 1.0)]:
                val = _lees_float(form, fouten, veld, mn, mx)
                if val is not None:
                    config["laadregeling"][veld] = val
            for veld, mn, mx in [("ema_adaptief_drempel_w", 100, 2000), ("scoring_sigma_w", 50, 1000)]:
                val = _lees_int(form, fouten, veld, mn, mx)
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

    hw_ip             = form.get("hw_ip", "").strip() or None
    hw_poll_s         = _lees_int(form, fouten, "homewizard_poll_interval_s", 5, 300)
    zaptec_install_id = form.get("zaptec_installation_id", "").strip() or None
    zaptec_charger_id = form.get("zaptec_charger_id", "").strip() or None
    update_interval_s = _lees_int(form, fouten, "update_interval_s", 60, 3600)
    state_poll_s      = _lees_int(form, fouten, "state_poll_interval_s", 10, 300)
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

    web_poort           = _lees_int(form, fouten, "web_poort", 1024, 65535)
    bewaarperiode_dagen = _lees_int(form, fouten, "bewaarperiode_dagen", 7, 365)
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
