"""
Zaptec Solarcharge — Hoofdprogramma

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
from src.zaptec import (ZaptecClient, ZaptecError,
                        OBS_CHARGER_OPERATION_MODE, OBS_SET_PHASES,
                        OBS_CHARGE_CURRENT_SET,
                        OBS_CURRENT_PHASE1, OBS_CURRENT_PHASE2, OBS_CURRENT_PHASE3,
                        OBS_NEXT_SCHEDULE_EVENT)
from src.controller import bereken_laadmodus, moet_stroom_bijwerken, moet_fase_wisselen
from src import database as db
from src.web import maak_app, start_web_server
from src.config_migratie import migreer_config
from src.config_validatie import valideer_config


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
    volgende_noodoverride   = 0.0 # Noodoverride mag direct triggeren als nodig

    # Fasewisseling bevestigingstracking
    # Na een fase-switch commando bewaren we de geopdragen fase en het tijdstip.
    # Timer 2 overschrijft huidige_fasen NIET zolang obs519 de nieuwe fase nog niet
    # bevestigt — de fysieke schakelaar + OCPP-heronderhandeling duurt >120s.
    laatste_fase_wissel_commandotijd = 0.0  # epoch-tijd van het laatste fase-switch commando
    geopdragen_fasen = None                 # fase die we opdroegen (None = geen lopende switch)

    # Vorige auto-status bijhouden om connect/disconnect te detecteren
    vorige_auto_aangesloten = False

    # Sessietracking: tijdstip van sessiestart (voor duur-berekening bij afsluiten)
    sessie_start_tijd = None

    # Stabilisatieperiode: eerste 30 seconden geen Zaptec-updates sturen.
    # Voorkomt dat het systeem direct een verkeerde waarde stuurt bij herstart
    # terwijl er al een auto aangesloten is.
    state["stabilisatie_tot"] = time.time() + 30

    logger.info("Zaptec Solarcharge gestart. Hoofdlus actief.")

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

                # ── Noodoverride: directe correctie bij groot energiedeficit of -surplus ──
                # Triggert buiten het normale update-interval om als het import- of
                # exportvermogen de ingestelde drempel overschrijdt.
                cfg_laad_no = config["laadregeling"]
                nood_drempel        = cfg_laad_no.get("noodoverride_drempel_w", 500)
                nood_export_drempel = cfg_laad_no.get("noodoverride_export_drempel_w", -600)
                nood_wacht          = cfg_laad_no.get("noodoverride_wachttijd_s", 60)

                nood_import_triggered = net_vermogen_w > nood_drempel
                nood_export_triggered = net_vermogen_w < nood_export_drempel

                if (cfg_laad_no.get("noodoverride_actief", True)
                        and state["actief"] and state["auto_aangesloten"]
                        and not state.get("standby_modus")
                        and nu >= state.get("stabilisatie_tot", 0)
                        and (nood_import_triggered or nood_export_triggered)
                        and nu >= volgende_noodoverride):

                    no_huidig_stroom_a = state["huidig_stroom_a"] or cfg_laad_no["min_stroom_a"]
                    no_huidige_fasen   = state["huidige_fasen"] or 1

                    try:
                        no_doel_stroom_a, no_doel_fasen = bereken_laadmodus(
                            net_vermogen_w      = net_vermogen_w,
                            huidig_stroom_a     = no_huidig_stroom_a,
                            huidige_fasen       = no_huidige_fasen,
                            fase_modus          = cfg_laad_no["fase_modus"],
                            spanning_v          = cfg_laad_no["spanning_v"],
                            min_stroom_a        = cfg_laad_no["min_stroom_a"],
                            max_stroom_a        = cfg_laad_no["max_stroom_a"],
                            veiligheidsbuffer_w = cfg_laad_no["veiligheidsbuffer_w"],
                            hysterese_w         = cfg_laad_no["fase_wissel_hysterese_w"],
                        )

                        # Fasewisseling alleen als de fase_wissel_timer het toestaat
                        no_fase_wisselt = moet_fase_wisselen(no_doel_fasen, no_huidige_fasen)
                        no_fase_wissel_toegestaan = nu >= volgende_fase_wissel

                        if no_fase_wisselt and not no_fase_wissel_toegestaan:
                            no_doel_fasen = no_huidige_fasen
                            from src.controller import _clamp
                            no_surplus_w = -(net_vermogen_w + cfg_laad_no["veiligheidsbuffer_w"])
                            no_huidig_w  = no_huidig_stroom_a * cfg_laad_no["spanning_v"] * no_huidige_fasen
                            no_doel_w    = max(0.0, no_huidig_w + no_surplus_w)
                            no_doel_stroom_a = _clamp(
                                no_doel_w / (cfg_laad_no["spanning_v"] * no_doel_fasen),
                                cfg_laad_no["min_stroom_a"],
                                cfg_laad_no["max_stroom_a"],
                            )
                            no_fase_wisselt = False

                        no_stroom_veranderd = moet_stroom_bijwerken(no_doel_stroom_a, no_huidig_stroom_a)

                        if no_stroom_veranderd or no_fase_wisselt:
                            no_drie_naar_een = None
                            if no_fase_wisselt and no_fase_wissel_toegestaan:
                                no_drie_naar_een = 0.0 if no_doel_fasen == 3 else 32.0
                                volgende_fase_wissel = nu + cfg_laad_no["fase_wissel_wachttijd_s"]

                            zaptec_client.set_installation_settings(
                                installation_id, no_doel_stroom_a, no_drie_naar_een
                            )
                            if nood_import_triggered:
                                logger.warning(
                                    "Noodoverride import: %dW > drempel %dW — "
                                    "stroom: %.1fA → %.1fA (%d fase(n))",
                                    net_vermogen_w, nood_drempel,
                                    no_huidig_stroom_a, no_doel_stroom_a, no_doel_fasen,
                                )
                                db.sla_event_op(
                                    db_pad,
                                    "noodoverride_import",
                                    f"import: {net_vermogen_w:.0f}W → stroom: "
                                    f"{no_doel_stroom_a:.1f}A op {no_doel_fasen} fase(n)",
                                )
                                state["sessie_no_import"] = state.get("sessie_no_import", 0) + 1
                            else:
                                logger.warning(
                                    "Noodoverride export: %dW < drempel %dW — "
                                    "stroom: %.1fA → %.1fA (%d fase(n))",
                                    net_vermogen_w, nood_export_drempel,
                                    no_huidig_stroom_a, no_doel_stroom_a, no_doel_fasen,
                                )
                                db.sla_event_op(
                                    db_pad,
                                    "noodoverride_export",
                                    f"export: {net_vermogen_w:.0f}W → stroom: "
                                    f"{no_doel_stroom_a:.1f}A op {no_doel_fasen} fase(n)",
                                )
                                state["sessie_no_export"] = state.get("sessie_no_export", 0) + 1
                            state["huidig_stroom_a"] = no_doel_stroom_a
                            if no_fase_wisselt:
                                state["huidige_fasen"] = no_doel_fasen
                                state["fase_wissel_bezig"] = True
                                # Start bevestigingstracking voor de fase-switch
                                geopdragen_fasen = no_doel_fasen
                                laatste_fase_wissel_commandotijd = nu
                                state["sessie_fase_wisselingen"] = state.get("sessie_fase_wisselingen", 0) + 1
                            if state.get("fout_zaptec_update"):
                                state["fout_zaptec_update"] = None

                            # Versnelde herpolling na elk noodoverride-commando
                            volgende_zaptec_state = nu + 5

                    except ZaptecError as e:
                        if "527" in str(e):
                            state["standby_modus"] = True
                            logger.warning("Laadmodus conflict (code 527) bij noodoverride — standby geactiveerd.")
                            db.sla_event_op(
                                db_pad, "standby_activatie",
                                "Laadmodus conflict bij noodoverride (code 527)",
                            )
                        logger.error("Noodoverride Zaptec update mislukt: %s", e)
                        state["fout_zaptec_update"] = str(e)
                        db.sla_event_op(db_pad, "fout", f"Noodoverride Zaptec: {e}")

                    volgende_noodoverride = nu + nood_wacht

            except HomeWizardError as e:
                logger.warning("HomeWizard fout: %s", e)
                state["fout_hw"] = str(e)
                db.sla_event_op(db_pad, "fout", f"HomeWizard: {e}")

        # ── Timer 2: Zaptec laderstatus controleren ────────────────────────
        if nu >= volgende_zaptec_state:
            volgende_zaptec_state = nu + cfg_zaptec["state_poll_interval_s"]
            try:
                # Één API-call voor alle observations (voorkomt dubbele GET /state)
                observations = zaptec_client.get_charger_state(charger_id)

                # Operatiemodus (obs 710)
                mode_raw = observations.get(OBS_CHARGER_OPERATION_MODE)
                if mode_raw is None:
                    logger.warning(
                        "Zaptec observation %d (ChargerOperationMode) niet gevonden — "
                        "neem aan dat er geen auto is aangesloten.",
                        OBS_CHARGER_OPERATION_MODE,
                    )
                mode = int(mode_raw) if mode_raw is not None else 1
                auto_aangesloten = mode in (2, 3, 5)

                # Fasen (obs 519)
                raw_phases = observations.get(OBS_SET_PHASES)
                if raw_phases is None:
                    logger.warning(
                        "Zaptec observation %d (SetPhases) niet gevonden — neem aan 1-fase.",
                        OBS_SET_PHASES,
                    )
                huidige_fasen_van_obs = 3 if raw_phases is not None and int(raw_phases) == 4 else 1

                # Settle-logica: na een fase-switch commando duurt het >120s voordat
                # obs519 de fysieke schakelaar bevestigt. Overschrijf huidige_fasen
                # pas als obs519 de geopdragen fase bevestigt of de wachtperiode voorbij is.
                fase_settle = cfg_zaptec.get("fase_wissel_bevestig_wacht_s", 120)
                if geopdragen_fasen is not None and (nu - laatste_fase_wissel_commandotijd) < fase_settle:
                    if huidige_fasen_van_obs == geopdragen_fasen:
                        # Bevestigd door Zaptec
                        state["huidige_fasen"] = huidige_fasen_van_obs
                        state["fase_wissel_geblokkeerd"] = False
                        state["fase_wissel_bezig"] = False
                        geopdragen_fasen = None
                        logger.info(
                            "Fasewisseling bevestigd door Zaptec (obs519): %df",
                            huidige_fasen_van_obs,
                        )
                    else:
                        # Nog niet bevestigd — gebruik de geopdragen fase
                        state["huidige_fasen"] = geopdragen_fasen
                        logger.debug(
                            "Fase wacht op bevestiging: obs519=%df, opgedragen=%df, nog ~%ds",
                            huidige_fasen_van_obs,
                            geopdragen_fasen,
                            int(fase_settle - (nu - laatste_fase_wissel_commandotijd)),
                        )
                else:
                    # Geen lopende switch of wachtperiode verlopen → vertrouw obs519
                    state["huidige_fasen"] = huidige_fasen_van_obs
                    if geopdragen_fasen is not None:
                        logger.warning(
                            "Fasewisseling niet bevestigd na %ds — obs519 geaccepteerd: %df",
                            fase_settle,
                            huidige_fasen_van_obs,
                        )
                        state["fase_wissel_geblokkeerd"] = True
                        state["fase_wissel_bezig"] = False
                        geopdragen_fasen = None

                state["auto_aangesloten"] = auto_aangesloten

                # Live laadstroom synchroniseren vanuit Zaptec
                live_bron = cfg_zaptec.get("live_stroom_bron", "auto")
                if auto_aangesloten and live_bron != "uit":
                    live_stroom = None
                    obs708_waarde = None

                    raw708 = observations.get(OBS_CHARGE_CURRENT_SET)
                    if raw708 is not None:
                        try:
                            obs708_waarde = float(raw708)
                        except (ValueError, TypeError):
                            pass

                    if live_bron == "708":
                        if obs708_waarde is not None and obs708_waarde > 0:
                            live_stroom = obs708_waarde

                    elif live_bron == "meting" and mode == 3:
                        try:
                            p1 = float(observations.get(OBS_CURRENT_PHASE1, 0) or 0)
                            p2 = float(observations.get(OBS_CURRENT_PHASE2, 0) or 0)
                            p3 = float(observations.get(OBS_CURRENT_PHASE3, 0) or 0)
                            meting = max(p1, p2, p3)
                            if meting > 0:
                                live_stroom = meting
                        except (ValueError, TypeError):
                            pass

                    elif live_bron == "auto":
                        if mode == 3:
                            # In actieve laadmodus: min van Zaptec-limiet en gemeten stroom.
                            # Vangt zowel de desync-bug als auto's die intern de stroom beperken
                            # (bijv. Opel Ampera: max 10A ongeacht wat Zaptec stuurt).
                            try:
                                p1 = float(observations.get(OBS_CURRENT_PHASE1, 0) or 0)
                                p2 = float(observations.get(OBS_CURRENT_PHASE2, 0) or 0)
                                p3 = float(observations.get(OBS_CURRENT_PHASE3, 0) or 0)
                                meting = max(p1, p2, p3)
                                if meting > 0 and obs708_waarde is not None and obs708_waarde > 0:
                                    live_stroom = min(obs708_waarde, meting)
                                elif obs708_waarde is not None and obs708_waarde > 0:
                                    live_stroom = obs708_waarde
                            except (ValueError, TypeError):
                                live_stroom = obs708_waarde
                        else:
                            # Mode 2/5: auto aangesloten maar laadt niet actief — geen meting,
                            # gebruik obs 708 als referentie voor wanneer laden hervatten
                            live_stroom = obs708_waarde

                    if live_stroom is not None and live_stroom > 0:
                        if geopdragen_fasen is not None:
                            # Settle-periode actief na fase-switch commando: Zaptec rapporteert
                            # tijdelijk kleine of nul-waarden (OCPP-heronderhandeling). Bewaar
                            # de net-gestuurde waarde zodat het dashboard geen 0A toont.
                            logger.debug(
                                "Live stroom sync overgeslagen (fasewisseling settle actief): %.1fA",
                                live_stroom,
                            )
                        else:
                            oud = state["huidig_stroom_a"]
                            if oud is None or abs(oud - live_stroom) >= 0.5:
                                logger.debug(
                                    "Live stroom gesynchroniseerd (bron: %s): %.1fA (was: %s)",
                                    live_bron,
                                    live_stroom,
                                    f"{oud:.1f}A" if oud is not None else "onbekend",
                                )
                            state["huidig_stroom_a"] = live_stroom

                # Lader-tijdschema check (obs 763: NextScheduleEvent)
                # Tijdschema's ingesteld via de Zaptec app werken op laderniveau en veranderen
                # availableCurrentMode NIET — detecteer ze hier los van de laadmodus.
                # Obs 763 retourneert een tijdstempel/waarde als schema actief is, of is
                # afwezig/"0" als er geen schema loopt. "0" is géén actief schema.
                next_schedule_raw = observations.get(OBS_NEXT_SCHEDULE_EVENT, "")
                next_schedule = next_schedule_raw.strip() not in ("", "0", "0.0")

                # Laadmodus ophalen (0=standaard, 1=gepland, 2=automatisch)
                _laadmodus_namen = {0: "Standaard laden", 1: "Gepland laden", 2: "Automatisch opladen"}
                laadmodus = zaptec_client.get_installation_mode(installation_id)
                state["laadmodus"] = laadmodus

                standby_was = state["standby_modus"]
                nieuwe_standby = (laadmodus != 0) or next_schedule
                state["standby_modus"] = nieuwe_standby

                if nieuwe_standby and not standby_was:
                    if laadmodus != 0:
                        reden = f"Laadmodus: {_laadmodus_namen.get(laadmodus, str(laadmodus))}"
                        logger.warning(
                            "Standby geactiveerd: laadmodus is '%s' — updates overgeslagen.",
                            _laadmodus_namen.get(laadmodus, str(laadmodus)),
                        )
                    else:
                        reden = f"Lader-tijdschema actief (NextScheduleEvent={next_schedule})"
                        logger.warning(
                            "Standby geactiveerd: tijdschema actief op lader — updates overgeslagen.",
                        )
                    db.sla_event_op(db_pad, "standby_activatie", reden)
                    # Geef de stroominstelling terug aan Zaptec zodat het schema of de
                    # laadmodus ongehinderd kan werken (availableCurrent=-1 herstelt standaard)
                    if state.get("auto_aangesloten"):
                        try:
                            zaptec_client.set_installation_settings(installation_id, -1.0)
                            logger.info("Laadstroom hersteld naar Zaptec-standaard (standby geactiveerd).")
                        except ZaptecError as e:
                            logger.warning("Kon stroom niet herstellen bij standby-activatie: %s", e)
                elif not nieuwe_standby and standby_was:
                    logger.info("Standby verlaten: laadmodus Standaard en geen tijdschema actief.")
                    db.sla_event_op(db_pad, "standby_verlaten", "Laadmodus Standaard en geen tijdschema actief")
                    volgende_zaptec_update = nu  # direct solar-regeling hervatten, niet wachten op interval

                # Lader-eigenschappen: max schakelingen per sessie
                try:
                    state["max_fase_schakelingen"] = zaptec_client.get_installation_schakelingen(installation_id)
                except ZaptecError as e:
                    logger.warning("max_fase_schakelingen niet ophaalbaar: %s", e)

                if state.get("fout_zaptec_state"):
                    logger.info("Zaptec verbinding hersteld (state).")
                    state["fout_zaptec_state"] = None

                # Detecteer aansluiten van auto
                if auto_aangesloten and not vorige_auto_aangesloten:
                    logger.info("Auto aangesloten (mode 2/3/5).")
                    db.sla_event_op(db_pad, "auto_aangesloten", "Auto is zojuist aangesloten")
                    # Nieuwe sessie starten in database
                    sessie_model = config["laadregeling"].get("regelaar_model", "legacy")
                    sessie_id = db.start_sessie(db_pad, sessie_model)
                    state["sessie_id"] = sessie_id
                    sessie_start_tijd = nu
                    state["sessie_no_import"] = 0
                    state["sessie_no_export"] = 0
                    state["sessie_fase_wisselingen"] = 0
                    # Forceer een snelle update bij de volgende update-cyclus
                    volgende_zaptec_update = nu

                # Detecteer loskoppelen van auto
                elif not auto_aangesloten and vorige_auto_aangesloten:
                    logger.info("Auto losgekoppeld — Zaptec hersteld naar standaard.")
                    db.sla_event_op(db_pad, "auto_losgekoppeld", "Auto losgekoppeld, Zaptec standaard hersteld")
                    # Sessie afsluiten in database
                    if state.get("sessie_id") is not None:
                        _duur = int(nu - sessie_start_tijd) if sessie_start_tijd else 0
                        db.sluit_sessie(db_pad, state["sessie_id"], {
                            "duur_s":            _duur,
                            "no_import_count":   state.get("sessie_no_import", 0),
                            "no_export_count":   state.get("sessie_no_export", 0),
                            "fase_wissel_count": state.get("sessie_fase_wisselingen", 0),
                        })
                        state["sessie_id"] = None
                        sessie_start_tijd = None
                    state["huidig_stroom_a"] = None
                    state["huidige_fasen"]   = None
                    state["fase_wissel_geblokkeerd"] = False
                    state["fase_wissel_bezig"] = False
                    geopdragen_fasen = None  # Bevestigingstracking resetten bij loskoppelen
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

            # Sla update over als laadmodus niet op Standaard staat of tijdschema actief is
            if state.get("standby_modus"):
                _laadmodus_namen = {1: "Gepland laden", 2: "Automatisch opladen"}
                _laadmodus_val = state.get("laadmodus", 0)
                if _laadmodus_val != 0:
                    logger.info(
                        "Standby actief (laadmodus: %s) — sla Zaptec-update over.",
                        _laadmodus_namen.get(_laadmodus_val, str(_laadmodus_val)),
                    )
                else:
                    logger.info("Standby actief (lader-tijdschema) — sla Zaptec-update over.")
                continue

            # Sla update over tijdens de stabilisatieperiode na herstart
            if nu < state.get("stabilisatie_tot", 0):
                logger.info(
                    "Stabilisatie actief (nog %.0fs) — sla Zaptec-update over.",
                    state["stabilisatie_tot"] - nu,
                )
                continue

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
                        state["fase_wissel_bezig"] = True
                        state["sessie_fase_wisselingen"] = state.get("sessie_fase_wisselingen", 0) + 1
                        # Start bevestigingstracking: obs519 bevestigt pas na 60-120s
                        geopdragen_fasen = doel_fasen
                        laatste_fase_wissel_commandotijd = nu
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

                    # Versnelde herpolling: 5s na het commando heeft Zaptec tijd om
                    # de OCPP-bevestiging te verwerken, zodat obs708 en obs519 vers zijn
                    volgende_zaptec_state = nu + 5

                except ZaptecError as e:
                    # Code 527: laadmodus conflict — activeer standby als vangnet
                    if "527" in str(e):
                        state["standby_modus"] = True
                        logger.warning("Laadmodus conflict (code 527) — standby geactiveerd.")
                        db.sla_event_op(
                            db_pad, "standby_activatie",
                            "Laadmodus conflict gedetecteerd (code 527)",
                        )
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

    # Versienummer inlezen
    versie = "onbekend"
    versie_pad = Path(__file__).parent / "VERSION"
    if versie_pad.exists():
        versie = versie_pad.read_text(encoding="utf-8").strip()

    # Huidige git-branch inlezen (best-effort, geen crash bij fout)
    branch = "onbekend"
    try:
        import subprocess as _sp
        _res = _sp.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent), timeout=5,
        )
        if _res.stdout.strip():
            branch = _res.stdout.strip()
    except Exception:
        pass

    # Config-migratie: voeg eventuele nieuwe velden toe vóór inladen
    migreer_config("config/config.yaml", "config/config.yaml.example")

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
    valideer_config(config)

    logger.info("=" * 60)
    logger.info("Zaptec Solarcharge opstarten  (versie %s)", versie)
    logger.info("HomeWizard IP:   %s", config["homewizard"]["ip"])
    logger.info("Zaptec lader:    %s", config["zaptec"]["charger_id"][:8] + "...")
    logger.info("Fase modus:      %s", config["laadregeling"]["fase_modus"])
    logger.info("Update interval: %ds", config["zaptec"]["update_interval_s"])
    logger.info("=" * 60)

    # Database initialiseren
    db.init_database(cfg_opslag["db_pad"])

    # Opschoning: verwijder metingen en events ouder dan de bewaarperiode
    _bewaarperiode = cfg_opslag.get("bewaarperiode_dagen", 30)
    db.verwijder_oude_data(cfg_opslag["db_pad"], _bewaarperiode)

    # API-clients aanmaken
    hw_client     = HomeWizardClient(config["homewizard"]["ip"], hw_token)
    zaptec_client = ZaptecClient(zaptec_username, zaptec_password)

    # Gedeelde state (main loop schrijft, Flask leest)
    state = {
        "versie":            versie,
        "branch":            branch,
        "actief":            True,
        "auto_aangesloten":  False,
        "huidig_stroom_a":   None,
        "huidige_fasen":     None,
        "net_vermogen_w":    None,
        "laatste_hw_update": None,
        "fout_hw":           None,
        "fout_zaptec_state": None,
        "fout_zaptec_update": None,
        "laadmodus":         None,   # None=onbekend, 0=standaard, 1=gepland, 2=auto
        "standby_modus":     False,  # True als laadmodus != 0 (API-updates worden overgeslagen)
        "stabilisatie_tot":  0.0,    # epoch-tijd tot wanneer stabilisatieperiode actief is
        "max_fase_schakelingen":  None,   # propertySessionMaxStopCount van de installatie
        "fase_wissel_geblokkeerd": False, # True als settle-periode verstreek zonder bevestiging
        "fase_wissel_bezig":       False, # True tijdens settle-periode na een fase-switch commando
        "sessie_id":               None,  # huidig sessie-ID in database, of None als geen sessie actief
        "sessie_no_import":        0,     # noodoverride import-teller voor lopende sessie
        "sessie_no_export":        0,     # noodoverride export-teller voor lopende sessie
        "sessie_fase_wisselingen": 0,     # fase-wissel teller voor lopende sessie
    }

    # Flask webserver starten in een achtergrond-thread
    app = maak_app(state, config, cfg_opslag["db_pad"], zaptec=zaptec_client)
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
        logger.info("Zaptec Solarcharge gestopt door gebruiker (Ctrl+C).")


if __name__ == "__main__":
    main()
