"""
Config-validatiemodule voor Zaptec Solarcharge.

Controleert bij startup op conflicterende instellingen in de geladen config.
Logt een WARNING per gedetecteerd conflict. Crasht nooit — systeem start altijd.

Gebruik (in main.py):
    from src.config_validatie import valideer_config
    valideer_config(config)
"""

import logging

logger = logging.getLogger(__name__)


def valideer_config(config: dict) -> list:
    """
    Valideert de geladen configuratie op conflicterende instellingen.

    Logt een WARNING per gedetecteerd conflict met uitleg welke velden
    conflicteren en wat het gevolg is. Crasht niet — het systeem start
    altijd, ook als er conflicten zijn.

    Args:
        config: De geladen config-dict (zoals teruggegeven door laad_config).

    Returns:
        Lijst van conflict-omschrijvingen (strings). Lege lijst als alles OK is.
    """
    conflicten = []

    def waarschuw(bericht: str) -> None:
        conflicten.append(bericht)
        logger.warning("Config-conflict: %s", bericht)

    cfg_hw     = config.get("homewizard", {})
    cfg_zaptec = config.get("zaptec", {})
    cfg_laad   = config.get("laadregeling", {})

    poll_interval_s              = cfg_hw.get("poll_interval_s")
    update_interval_s            = cfg_zaptec.get("update_interval_s")
    fase_wissel_bevestig_wacht_s = cfg_zaptec.get("fase_wissel_bevestig_wacht_s")
    fase_wissel_wachttijd_s      = cfg_laad.get("fase_wissel_wachttijd_s")
    min_stroom_a                 = cfg_laad.get("min_stroom_a")
    max_stroom_a                 = cfg_laad.get("max_stroom_a")
    noodoverride_drempel_w       = cfg_laad.get("noodoverride_drempel_w")
    noodoverride_wachttijd_s     = cfg_laad.get("noodoverride_wachttijd_s")
    veiligheidsbuffer_w          = cfg_laad.get("veiligheidsbuffer_w")
    noodoverride_export_drempel_w = cfg_laad.get("noodoverride_export_drempel_w")

    # 1. poll_interval_s mag niet groter zijn dan update_interval_s
    if poll_interval_s is not None and update_interval_s is not None:
        if poll_interval_s > update_interval_s:
            waarschuw(
                f"homewizard.poll_interval_s ({poll_interval_s}s) is groter dan "
                f"zaptec.update_interval_s ({update_interval_s}s). "
                "De P1 Meter wordt minder vaak uitgelezen dan Zaptec bijgewerkt — "
                "elke Zaptec-update is dan gebaseerd op een verouderde meting."
            )

    # 2. noodoverride_wachttijd_s mag niet groter zijn dan update_interval_s
    if noodoverride_wachttijd_s is not None and update_interval_s is not None:
        if noodoverride_wachttijd_s > update_interval_s:
            waarschuw(
                f"laadregeling.noodoverride_wachttijd_s ({noodoverride_wachttijd_s}s) is groter dan "
                f"zaptec.update_interval_s ({update_interval_s}s). "
                "De noodoverride-cooldown duurt langer dan het reguliere update-interval — "
                "een noodoverride kan dan nooit sneller reageren dan een normale update."
            )

    # 3. fase_wissel_bevestig_wacht_s mag niet groter zijn dan fase_wissel_wachttijd_s
    if fase_wissel_bevestig_wacht_s is not None and fase_wissel_wachttijd_s is not None:
        if fase_wissel_bevestig_wacht_s > fase_wissel_wachttijd_s:
            waarschuw(
                f"zaptec.fase_wissel_bevestig_wacht_s ({fase_wissel_bevestig_wacht_s}s) is groter dan "
                f"laadregeling.fase_wissel_wachttijd_s ({fase_wissel_wachttijd_s}s). "
                "De bevestigingsperiode na een fasewisseling duurt langer dan de minimale "
                "wachttijd tussen twee wissels — een volgende wissel kan nooit op tijd starten."
            )

    # 4. min_stroom_a moet kleiner zijn dan max_stroom_a
    if min_stroom_a is not None and max_stroom_a is not None:
        if min_stroom_a >= max_stroom_a:
            waarschuw(
                f"laadregeling.min_stroom_a ({min_stroom_a}A) is niet kleiner dan "
                f"max_stroom_a ({max_stroom_a}A). "
                "Laden is onmogelijk: het algoritme kan geen geldige laadstroom berekenen."
            )

    # 5. noodoverride_drempel_w moet groter zijn dan veiligheidsbuffer_w
    if noodoverride_drempel_w is not None and veiligheidsbuffer_w is not None:
        if noodoverride_drempel_w <= veiligheidsbuffer_w:
            waarschuw(
                f"laadregeling.noodoverride_drempel_w ({noodoverride_drempel_w}W) is niet groter dan "
                f"veiligheidsbuffer_w ({veiligheidsbuffer_w}W). "
                "De noodoverride triggert nooit: de veiligheidsbuffer is al groter dan de drempel."
            )

    # 6. noodoverride_export_drempel_w moet negatief zijn (alleen als het veld aanwezig is)
    if noodoverride_export_drempel_w is not None:
        if noodoverride_export_drempel_w >= 0:
            waarschuw(
                f"laadregeling.noodoverride_export_drempel_w ({noodoverride_export_drempel_w}W) "
                "is niet negatief. Dit veld is een exportdrempel en moet een negatieve waarde "
                "hebben (bijv. -600). Bij een positieve waarde triggert de export-noodoverride nooit."
            )

    if conflicten:
        logger.warning(
            "Config-validatie: %d conflict(en) gevonden. "
            "Systeem start wel, maar werkt mogelijk niet optimaal.",
            len(conflicten),
        )
    else:
        logger.info("Config-validatie: geen conflicten gevonden.")

    return conflicten
