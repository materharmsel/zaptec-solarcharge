"""
Solarcharge — Hoofdprogramma

Past automatisch het laadvermogen van de Zaptec Go 2 aan op basis van
de HomeWizard P1 Meter, zodat de auto laadt met zonne-energie zonder
teruglevering aan het net.

Opstarten:
    python main.py

Of als systemd-service (zie README.md voor installatie).
"""

import logging
import logging.handlers
import os
import sys
import time
import threading
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.homewizard import HomeWizardClient, HomeWizardError
from src.zaptec import ZaptecClient, ZaptecError
from src.controller import bereken_laadmodus, moet_stroom_bijwerken, moet_fase_wisselen
from src import database as db
from src.web import maak_app, start_web_server


# ─── Configuratie laden ───────────────────────────────────────────────────────

def laad_env(env_pad: str) -> None:
    """Laadt de credentials uit het .env bestand in omgevingsvariabelen."""
    if not Path(env_pad).exists():
        print(f"FOUT: Credentials bestand niet gevonden: {env_pad}")
        print("Maak het bestand aan en vul je Zaptec en HomeWizard gegevens in.")
        print("Zie README.md voor instructies.")
        sys.exit(1)
    load_dotenv(env_pad)


def laad_config(config_pad: str) -> dict:
    """
    Laadt de instellingen uit het YAML-configuratiebestand.

    Returns:
        dict met alle instellingen.

    Exits:
        Stopt het programma als het bestand niet bestaat of onleesbaar is.
    """
    if not Path(config_pad).exists():
        print(f"FOUT: Configuratiebestand niet gevonden: {config_pad}")
        print("Kopieer config/config.yaml.example naar config/config.yaml")
        print("en pas de instellingen aan.")
        sys.exit(1)

    try:
        with open(config_pad, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"FOUT: Kan config.yaml niet lezen: {e}")
        sys.exit(1)

    if not isinstance(config, dict):
        print("FOUT: config.yaml is leeg of heeft een ongeldig formaat.")
        sys.exit(1)

    return config


# ─── Logging instellen ────────────────────────────────────────────────────────

def setup_logging(log_pad: str, log_niveau: str) -> None:
    """
    Configureert het logging-systeem naar bestand en console.

    Logbestanden roteren automatisch bij 1 MB, met maximaal 5 bestanden.
    """
    Path(log_pad).parent.mkdir(parents=True, exist_ok=True)

    niveau = getattr(logging, log_niveau.upper(), logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler voor het logbestand met rotatie
    bestand_handler = logging.handlers.RotatingFileHandler(
        log_pad,
        maxBytes=1_000_000,   # 1 MB
        backupCount=5,
        encoding="utf-8",
    )
    bestand_handler.setFormatter(formatter)

    # Handler voor de console (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(niveau)
    root_logger.addHandler(bestand_handler)
    root_logger.addHandler(console_handler)

    # Onderdruk verbose logging van externe bibliotheken
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


# ─── Hoofdlus ─────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)


def hoofd_lus(
    config: dict,
    state: dict,
    hw_client: HomeWizardClient,
    zaptec_client: ZaptecClient,
    db_pad: str,
) -> None:
    """
    De centrale regelaar. Draait oneindig in de hoofdthread.

    Vier onafhankelijke timers:
        1. homewizard_timer:    Lees P1 Meter (elke poll_interval_s seconden)
        2. zaptec_state_timer:  Controleer lader-status (elke state_poll_interval_s)
        3. zaptec_update_timer: Stuur update naar Zaptec (elke update_interval_s)
        4. fase_wissel_timer:   Bewaker voor te snelle fasewisseling

    Alle timers zijn geïmplementeerd als `volgende_X = time.time() + interval`.
    """
    cfg_hw      = config["homewizard"]
    cfg_zaptec  = config["zaptec"]
    cfg_laad    = config["laadregeling"]

    installation_id = cfg_zaptec["installation_id"]
    charger_id      = cfg_zaptec["charger_id"]

    # Initialiseer timers zodat ze direct afvuren bij de eerste iteratie
    nu = time.time()
    volgende_hw_poll        = nu
    volgende_zaptec_state   = nu
    volgende_zaptec_update  = nu + cfg_zaptec["update_interval_s"]  # Wacht even voor de eerste update
    volgende_fase_wissel    = nu  # Eerste fasewisseling mag direct

    # Vorige auto-status bijhouden om connect/disconnect te detecteren
    vorige_auto_aangesloten = False

    logger.info("Solarcharge gestart. Hoofdlus actief.")

    while True:
        time.sleep(1)  # Voorkomt een druk-lus; 1 seconde resolutie is ruim voldoende
        nu = time.time()

        # ── Timer 1: HomeWizard P1 Meter uitlezen ──────────────────────────
        if nu >= volgende_hw_poll:
            volgende_hw_poll = nu + cfg_hw["poll_interval_s"]
            try:
                net_vermogen_w = hw_client.get_power_watts()
                state["net_vermogen_w"] = net_vermogen_w
                state["laatste_hw_update"] = time.strftime("%H:%M:%S")
                if state.get("fout_hw"):
                    logger.info("HomeWizard verbinding hersteld.")
                    state["fout_hw"] = None

                # Log elke meting naar de database
                db.sla_meting_op(
                    db_pad,
                    net_vermogen_w=net_vermogen_w,
                    auto_aangesloten=state["auto_aangesloten"],
                    gesteld_stroom_a=state["huidig_stroom_a"],
                    huidige_fasen=state["huidige_fasen"],
                    controller_actief=state["actief"],
                )

            except HomeWizardError as e:
                logger.warning("HomeWizard fout: %s", e)
                state["fout_hw"] = str(e)
                db.sla_event_op(db_pad, "fout", f"HomeWizard: {e}")

        # ── Timer 2: Zaptec laderstatus controleren ────────────────────────
        if nu >= volgende_zaptec_state:
            volgende_zaptec_state = nu + cfg_zaptec["state_poll_interval_s"]
            try:
                auto_aangesloten = zaptec_client.is_car_connected(charger_id)
                huidige_fasen    = zaptec_client.get_current_phases(charger_id)
                state["auto_aangesloten"] = auto_aangesloten
                state["huidige_fasen"]    = huidige_fasen
                if state.get("fout_zaptec_state"):
                    logger.info("Zaptec verbinding hersteld (state).")
                    state["fout_zaptec_state"] = None

                # Detecteer aansluiten van auto
                if auto_aangesloten and not vorige_auto_aangesloten:
                    logger.info("Auto aangesloten (mode 2/3/5).")
                    db.sla_event_op(db_pad, "auto_aangesloten", "Auto is zojuist aangesloten")
                    # Forceer een snelle update bij de volgende update-cyclus
                    volgende_zaptec_update = nu

                # Detecteer loskoppelen van auto
                elif not auto_aangesloten and vorige_auto_aangesloten:
                    logger.info("Auto losgekoppeld — Zaptec hersteld naar standaard.")
                    db.sla_event_op(db_pad, "auto_losgekoppeld", "Auto losgekoppeld, Zaptec standaard hersteld")
                    state["huidig_stroom_a"] = None
                    state["huidige_fasen"]   = None
                    try:
                        zaptec_client.set_installation_settings(installation_id, -1.0)
                    except ZaptecError as e:
                        logger.error("Kon Zaptec niet herstellen na loskoppelen: %s", e)

                vorige_auto_aangesloten = auto_aangesloten

            except ZaptecError as e:
                logger.warning("Zaptec state fout: %s", e)
                state["fout_zaptec_state"] = str(e)

        # ── Timer 3: Laadvermogen aanpassen ────────────────────────────────
        if nu >= volgende_zaptec_update and state["actief"] and state["auto_aangesloten"]:
            volgende_zaptec_update = nu + cfg_zaptec["update_interval_s"]

            if state["net_vermogen_w"] is None:
                logger.warning("Geen HomeWizard meting beschikbaar — sla Zaptec-update over.")
                continue

            # Gebruik de huidige waarden uit de config (kan live bijgewerkt zijn via webinterface)
            cfg_laad = config["laadregeling"]

            huidig_stroom_a = state["huidig_stroom_a"] or cfg_laad["min_stroom_a"]
            huidige_fasen   = state["huidige_fasen"] or 1

            try:
                doel_stroom_a, doel_fasen = bereken_laadmodus(
                    net_vermogen_w      = state["net_vermogen_w"],
                    huidig_stroom_a     = huidig_stroom_a,
                    huidige_fasen       = huidige_fasen,
                    fase_modus          = cfg_laad["fase_modus"],
                    spanning_v          = cfg_laad["spanning_v"],
                    min_stroom_a        = cfg_laad["min_stroom_a"],
                    max_stroom_a        = cfg_laad["max_stroom_a"],
                    veiligheidsbuffer_w = cfg_laad["veiligheidsbuffer_w"],
                    hysterese_w         = cfg_laad["fase_wissel_hysterese_w"],
                )
            except Exception as e:
                logger.error("Controller berekeningsfout: %s", e)
                continue

            # Bepaal of fasewisseling nodig en toegestaan is
            fase_wisselt = moet_fase_wisselen(doel_fasen, huidige_fasen)
            fase_wissel_toegestaan = nu >= volgende_fase_wissel

            if fase_wisselt and not fase_wissel_toegestaan:
                # Fasewisseling gewenst maar nog niet toegestaan (beschermingstimer)
                # Herbereken de stroom zonder fase te wisselen
                logger.debug(
                    "Fasewisseling uitgesteld (bewaker actief nog %.0fs)",
                    volgende_fase_wissel - nu,
                )
                doel_fasen = huidige_fasen
                # Herbereken stroom voor het huidige aantal fases
                from src.controller import _clamp
                if state["net_vermogen_w"] is not None:
                    beschikbaar_surplus_w = -(state["net_vermogen_w"] + cfg_laad["veiligheidsbuffer_w"])
                    huidig_laad_vermogen_w = huidig_stroom_a * cfg_laad["spanning_v"] * huidige_fasen
                    doel_vermogen_w = max(0.0, huidig_laad_vermogen_w + beschikbaar_surplus_w)
                    doel_stroom_a = _clamp(
                        doel_vermogen_w / (cfg_laad["spanning_v"] * doel_fasen),
                        cfg_laad["min_stroom_a"],
                        cfg_laad["max_stroom_a"],
                    )
                fase_wisselt = False

            # Stuur update naar Zaptec als er iets veranderd is
            stroom_veranderd = moet_stroom_bijwerken(doel_stroom_a, huidig_stroom_a)

            if stroom_veranderd or fase_wisselt:
                drie_naar_een = None
                if fase_wisselt and fase_wissel_toegestaan:
                    drie_naar_een = 0.0 if doel_fasen == 3 else 32.0
                    volgende_fase_wissel = nu + cfg_laad["fase_wissel_wachttijd_s"]

                try:
                    zaptec_client.set_installation_settings(
                        installation_id, doel_stroom_a, drie_naar_een
                    )

                    if fase_wisselt:
                        logger.info(
                            "Fasewisseling: %d → %d fase(n), stroom: %.1fA",
                            huidige_fasen, doel_fasen, doel_stroom_a,
                        )
                        db.sla_event_op(
                            db_pad,
                            "fase_gewisseld",
                            f"{huidige_fasen}→{doel_fasen} fase(n), stroom: {doel_stroom_a:.1f}A",
                        )
                        state["huidige_fasen"] = doel_fasen
                    else:
                        logger.info(
                            "Stroom bijgesteld: %.1fA → %.1fA (net: %dW, %d fase(n))",
                            huidig_stroom_a, doel_stroom_a,
                            state["net_vermogen_w"], doel_fasen,
                        )
                        db.sla_event_op(
                            db_pad,
                            "stroom_bijgesteld",
                            f"{huidig_stroom_a:.1f}A → {doel_stroom_a:.1f}A "
                            f"(net: {state['net_vermogen_w']:.0f}W, {doel_fasen} fase(n))",
                        )

                    state["huidig_stroom_a"] = doel_stroom_a
                    if state.get("fout_zaptec_update"):
                        state["fout_zaptec_update"] = None

                except ZaptecError as e:
                    logger.error("Zaptec update mislukt: %s", e)
                    state["fout_zaptec_update"] = str(e)
                    db.sla_event_op(db_pad, "fout", f"Zaptec update: {e}")
            else:
                logger.debug(
                    "Geen update nodig: doel=%.1fA≈huidig=%.1fA, fasen ongewijzigd",
                    doel_stroom_a, huidig_stroom_a,
                )


# ─── Opstarten ────────────────────────────────────────────────────────────────

def main() -> None:
    """Laadt de configuratie, initialiseert componenten en start de service."""

    # Bepaal het werkpad (zodat relatieve paden werken vanuit de projectmap)
    project_pad = Path(__file__).parent
    os.chdir(project_pad)

    # Laad credentials en configuratie
    laad_env("config/.env")
    config = laad_config("config/config.yaml")

    # Controleer verplichte omgevingsvariabelen
    zaptec_username = os.environ.get("ZAPTEC_USERNAME", "")
    zaptec_password = os.environ.get("ZAPTEC_PASSWORD", "")
    hw_token        = os.environ.get("HOMEWIZARD_TOKEN", "")

    if not zaptec_username or zaptec_username.startswith("vul_hier"):
        print("FOUT: ZAPTEC_USERNAME is niet ingevuld in config/.env")
        sys.exit(1)
    if not zaptec_password or zaptec_password.startswith("vul_hier"):
        print("FOUT: ZAPTEC_PASSWORD is niet ingevuld in config/.env")
        sys.exit(1)
    if not hw_token or hw_token.startswith("vul_hier"):
        print("FOUT: HOMEWIZARD_TOKEN is niet ingevuld in config/.env")
        sys.exit(1)

    # Logging instellen
    cfg_opslag = config["opslag"]
    Path(cfg_opslag["db_pad"]).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg_opslag["log_pad"]).parent.mkdir(parents=True, exist_ok=True)
    setup_logging(cfg_opslag["log_pad"], cfg_opslag.get("log_niveau", "INFO"))

    logger.info("=" * 60)
    logger.info("Solarcharge opstarten")
    logger.info("HomeWizard IP:   %s", config["homewizard"]["ip"])
    logger.info("Zaptec lader:    %s", config["zaptec"]["charger_id"][:8] + "...")
    logger.info("Fase modus:      %s", config["laadregeling"]["fase_modus"])
    logger.info("Update interval: %ds", config["zaptec"]["update_interval_s"])
    logger.info("=" * 60)

    # Database initialiseren
    db.init_database(cfg_opslag["db_pad"])

    # API-clients aanmaken
    hw_client     = HomeWizardClient(config["homewizard"]["ip"], hw_token)
    zaptec_client = ZaptecClient(zaptec_username, zaptec_password)

    # Gedeelde state (main loop schrijft, Flask leest)
    state = {
        "actief":            True,
        "auto_aangesloten":  False,
        "huidig_stroom_a":   None,
        "huidige_fasen":     None,
        "net_vermogen_w":    None,
        "laatste_hw_update": None,
        "fout_hw":           None,
        "fout_zaptec_state": None,
        "fout_zaptec_update": None,
    }

    # Flask webserver starten in een achtergrond-thread
    app = maak_app(state, config, cfg_opslag["db_pad"])
    start_web_server(
        app,
        host=config["web"]["host"],
        port=config["web"]["poort"],
    )
    logger.info(
        "Webinterface bereikbaar op http://0.0.0.0:%d", config["web"]["poort"]
    )

    # Hoofdlus starten (blokkeert de main-thread)
    try:
        hoofd_lus(config, state, hw_client, zaptec_client, cfg_opslag["db_pad"])
    except KeyboardInterrupt:
        logger.info("Solarcharge gestopt door gebruiker (Ctrl+C).")


if __name__ == "__main__":
    main()
