"""
Laadregelingsalgoritme voor Solarcharge.

Pure functies zonder netwerkaanroepen of neveneffecten.
Alle berekeningen voor laadstroom en fasekeuze staan hier centraal.
"""

import logging

logger = logging.getLogger(__name__)

# Drempelwaarden voor fasewisseling
FASE_WAARDE_3_FASE = 4   # Observation 519 waarde voor 3-fase (TN all phases)


def _clamp(waarde: float, minimum: float, maximum: float) -> float:
    """Begrenst een waarde tussen minimum en maximum."""
    return max(minimum, min(maximum, waarde))


def bereken_laadmodus(
    net_vermogen_w: float,
    huidig_stroom_a: float,
    huidige_fasen: int,
    fase_modus: str,
    spanning_v: float,
    min_stroom_a: float,
    max_stroom_a: float,
    veiligheidsbuffer_w: float,
    hysterese_w: float,
) -> tuple[float, int]:
    """
    Berekent de doellaadstroom en het gewenste aantal fases.

    De HomeWizard P1 Meter meet het totale huisverbruik inclusief de lader.
    Een negatief net_vermogen_w betekent dat er zonnepanelen-overschot is
    dat we kunnen gebruiken om de auto te laden.

    Fasekeuze (in 'auto' modus):
        - Als het doelvermogen ≥ min_stroom_a × spanning_v × 3: gebruik 3 fases
        - Als het doelvermogen ≥ min_stroom_a × spanning_v × 1: gebruik 1 fase
        - Anders: houd vast aan min_stroom_a op 1 fase (kan niet lager)

    De hysterese voorkomt snel heen-en-weer wisselen: om te upgraden naar
    3-fase moet er hysterese_w extra vermogen beschikbaar zijn bovenop de
    3-fase drempelwaarde.

    Args:
        net_vermogen_w:      Huidig netvermogen in Watt (positief=import, negatief=export)
        huidig_stroom_a:     Laadstroom die we de vorige cyclus hebben ingesteld (Ampere)
        huidige_fasen:       Huidig aantal fases (1 of 3, gelezen van Zaptec)
        fase_modus:          "auto", "1" of "3"
        spanning_v:          Spanning per fase in Volt (standaard 230V)
        min_stroom_a:        Minimale laadstroom in Ampere (IEC 61851: minimaal 6A)
        max_stroom_a:        Maximale laadstroom / groepsbeveiliging in Ampere
        veiligheidsbuffer_w: Extra buffer in Watt (positief = bewust iets importeren)
        hysterese_w:         Extra vermogen nodig voor upgrade van 1-fase naar 3-fase

    Returns:
        tuple (doel_stroom_a, doel_fasen):
            doel_stroom_a — gewenste laadstroom in Ampere (al begrensd)
            doel_fasen    — gewenst aantal fases (1 of 3)
    """
    # Stap 1: Bereken hoeveel vermogen we in totaal willen gebruiken voor laden
    # beschikbaar_surplus_w: positief = we hebben overschot, negatief = we importeren
    beschikbaar_surplus_w = -(net_vermogen_w + veiligheidsbuffer_w)

    # Huidig laadvermogen op basis van wat we de vorige cyclus hebben ingesteld
    huidig_laad_vermogen_w = huidig_stroom_a * spanning_v * huidige_fasen

    # Doelvermogen = huidig laadvermogen + het surplus dat beschikbaar is
    doel_vermogen_w = max(0.0, huidig_laad_vermogen_w + beschikbaar_surplus_w)

    # Stap 2: Bepaal het gewenste aantal fases
    drempel_1f = min_stroom_a * spanning_v          # bijv. 6A × 230V = 1380W
    drempel_3f = min_stroom_a * spanning_v * 3      # bijv. 6A × 230V × 3 = 4140W

    if fase_modus == "1":
        doel_fasen = 1
    elif fase_modus == "3":
        doel_fasen = 3
    else:
        # "auto": beslis op basis van beschikbaar vermogen
        if huidige_fasen == 3:
            # We laden al op 3-fase: schakel terug naar 1-fase alleen als
            # het vermogen onder de 3-fase drempel zakt
            doel_fasen = 3 if doel_vermogen_w >= drempel_3f else 1
        else:
            # We laden op 1-fase: upgrade naar 3-fase alleen als er
            # duidelijk genoeg vermogen is (drempel + hysterese)
            doel_fasen = 3 if doel_vermogen_w >= (drempel_3f + hysterese_w) else 1

    # Stap 3: Bereken de laadstroom voor het gekozen aantal fases
    if doel_vermogen_w < drempel_1f:
        # Niet genoeg surplus voor minimale laadstroom: houd vast aan minimum
        doel_stroom_a = min_stroom_a
        doel_fasen = 1
    else:
        doel_stroom_a = doel_vermogen_w / (spanning_v * doel_fasen)
        doel_stroom_a = _clamp(doel_stroom_a, min_stroom_a, max_stroom_a)

    logger.debug(
        "Controller: net=%dW, huidig=%.1fA×%df → doel=%.1fA×%df "
        "(surplus=%.0fW, doelvermogen=%.0fW)",
        net_vermogen_w,
        huidig_stroom_a,
        huidige_fasen,
        doel_stroom_a,
        doel_fasen,
        beschikbaar_surplus_w,
        doel_vermogen_w,
    )

    return doel_stroom_a, doel_fasen


def moet_stroom_bijwerken(
    doel_stroom_a: float,
    huidig_stroom_a: float,
    drempel_a: float = 0.5,
) -> bool:
    """
    Bepaalt of de laadstroom naar Zaptec gestuurd moet worden.

    We sturen alleen een update als de gewenste stroom minstens drempel_a
    Ampere verschilt van de huidige instelling. Dit voorkomt onnodige API-aanroepen
    bij kleine schommelingen.

    Args:
        doel_stroom_a:  Berekende doellaadstroom
        huidig_stroom_a: Stroom die we de vorige keer hebben ingesteld
        drempel_a:       Minimale verandering voor een update (standaard 0.5A)

    Returns:
        True als een update zinvol is.
    """
    return abs(doel_stroom_a - huidig_stroom_a) >= drempel_a


def moet_fase_wisselen(doel_fasen: int, huidige_fasen: int) -> bool:
    """
    Bepaalt of het aantal fases veranderd moet worden.

    Args:
        doel_fasen:    Gewenst aantal fases
        huidige_fasen: Huidig aantal fases

    Returns:
        True als de fase gewijzigd moet worden.
    """
    return doel_fasen != huidige_fasen
