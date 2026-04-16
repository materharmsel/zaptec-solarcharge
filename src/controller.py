"""
Laadregelingsalgoritme voor Solarcharge.

Pure functies zonder netwerkaanroepen of neveneffecten.
Alle berekeningen voor laadstroom en fasekeuze staan hier centraal.
"""

import logging
import math

logger = logging.getLogger(__name__)

# Drempelwaarden voor fasewisseling
FASE_WAARDE_3_FASE = 4   # Observation 519 waarde voor 3-fase (TN all phases)


def bereken_ema(
    oud_ema: float | None,
    meting: float,
    alpha_min: float,
    alpha_max: float,
    drempel_w: float,
) -> float:
    """
    Berekent een exponential moving average (EMA) met adaptieve alpha.

    De gevoeligheid (alpha) past zich aan op basis van hoe verrassend de nieuwe
    meting is ten opzichte van de huidige EMA:
        - Kleine afwijking (stabiel): alpha dicht bij alpha_min → EMA beweegt traag
        - Grote afwijking (schommeling): alpha dicht bij alpha_max → EMA reageert sneller

    Args:
        oud_ema:    Vorige EMA-waarde, of None bij de eerste meting (seeden)
        meting:     Nieuwste ruwe meting (bijv. net_vermogen_w)
        alpha_min:  Minimale gevoeligheid bij stabiele situatie (bijv. 0.1)
        alpha_max:  Maximale gevoeligheid bij grote afwijking (bijv. 0.6)
        drempel_w:  Afwijking in Watt waarboven alpha op alpha_max uitkomt (bijv. 300W)

    Returns:
        Nieuwe EMA-waarde.
    """
    if oud_ema is None:
        return float(meting)  # Eerste meting: EMA seeden met ruwe waarde

    afwijking = abs(meting - oud_ema)
    verhouding = (afwijking / drempel_w) if drempel_w > 0 else 1.0
    verhouding = max(0.0, min(1.0, verhouding))  # begrenzen op [0, 1]
    alpha = alpha_min + verhouding * (alpha_max - alpha_min)
    return alpha * meting + (1.0 - alpha) * oud_ema


def bereken_laadmodus_solarflow(
    *,
    ema_net_vermogen_w: float,
    huidig_stroom_a: float,
    huidige_fasen: int,
    fase_modus: str,
    spanning_v: float,
    min_stroom_a: float,
    max_stroom_a: float,
    doel_net_vermogen_w: float,
    veiligheidsbuffer_w: float,
    hysterese_w: float,
    wissel_budget_ratio: float | None,
    scoring_sigma_w: float,
) -> tuple[float, int, float]:
    """
    SolarFlow v1: berekent laadstroom, fasen en kwaliteitsscore via EMA-gebaseerde sturing.

    Gebruikt dezelfde absolute berekeningsstructuur als bereken_laadmodus(), maar met
    EMA als input (in plaats van ruwe P1) en een expliciet configureerbaar target.
    Dynamische fase-hysterese op basis van wisselbudget en Gaussische kwaliteitsscore
    per cyclus zijn SolarFlow-specifiek.

    Args:
        ema_net_vermogen_w:  Al bijgewerkte EMA van het netvermogen (output van bereken_ema())
        huidig_stroom_a:     Huidige laadstroom (Ampere)
        huidige_fasen:       Huidig aantal fases (1 of 3)
        fase_modus:          "auto", "1" of "3"
        spanning_v:          Spanning per fase in Volt
        min_stroom_a:        Minimale laadstroom (Ampere)
        max_stroom_a:        Maximale laadstroom (Ampere)
        doel_net_vermogen_w: Streefwaarde voor netvermogen (bijv. 0W, of -100W voor lichte export)
        veiligheidsbuffer_w: Extra buffer in Watt bovenop het target
        hysterese_w:         Basis hysterese voor fasewisseling (Watt)
        wissel_budget_ratio: Resterende wisselruimte als fractie (0.0–1.0), of None als onbekend
        scoring_sigma_w:     Breedte van de Gaussische scorecurve in Watt

    Returns:
        tuple (doel_stroom_a, doel_fasen, cyclus_score):
            doel_stroom_a  — gewenste laadstroom, begrensd op [min, max]
            doel_fasen     — gewenst aantal fases (1 of 3)
            cyclus_score   — Gaussische kwaliteitsscore (0.0–1.0; 1.0 = perfect op doel)
    """
    # Stap 1: Gaussische score — meet hoe ver de EMA van het target zit.
    # Berekend vóór de rest zodat de echte afwijking gemeten wordt, niet de gecorrigeerde.
    fout_w = ema_net_vermogen_w - doel_net_vermogen_w
    if scoring_sigma_w > 0:
        score = math.exp(-(fout_w ** 2) / (2.0 * scoring_sigma_w ** 2))
    else:
        score = 1.0 if fout_w == 0.0 else 0.0

    # Stap 2: Beschikbaar surplus op basis van EMA en target.
    # Zelfde formule als legacy, maar met EMA als input en doel_net_vermogen_w als target.
    # Effectief: hoe ver zit de EMA van het target, uitgedrukt in Watt laadruimte.
    beschikbaar_surplus_w = -(ema_net_vermogen_w + veiligheidsbuffer_w - doel_net_vermogen_w)

    # Stap 3: Doelvermogen — huidig laadvermogen + beschikbaar surplus
    huidig_laad_vermogen_w = huidig_stroom_a * spanning_v * huidige_fasen
    doel_vermogen_w = max(0.0, huidig_laad_vermogen_w + beschikbaar_surplus_w)

    # Stap 4: Dynamische hysterese op basis van wisselbudget.
    # Hoe minder wisselruimte over, hoe hoger de drempel voor een nieuwe fasewisseling.
    if wissel_budget_ratio is None or wissel_budget_ratio > 0.5:
        hysterese_factor = 1.0
    elif wissel_budget_ratio > 0.25:
        hysterese_factor = 1.5
    elif wissel_budget_ratio > 0.0:
        hysterese_factor = 3.0
    else:
        # Budget uitgeput: geen fasewisseling meer — bereken stroom voor huidige fasen
        logger.debug(
            "SolarFlow: wisselbudget uitgeput (ratio=%.2f) — fasewisseling geblokkeerd",
            wissel_budget_ratio if wissel_budget_ratio is not None else -1,
        )
        drempel_1f = min_stroom_a * spanning_v
        if doel_vermogen_w < drempel_1f:
            return (min_stroom_a, huidige_fasen, score)
        doel_stroom_a = _clamp(
            doel_vermogen_w / (spanning_v * huidige_fasen), min_stroom_a, max_stroom_a
        )
        return (doel_stroom_a, huidige_fasen, score)

    hysterese_dynamisch = hysterese_w * hysterese_factor

    # Stap 5: Fasebeslissing op basis van doel_vermogen_w met dynamische hysterese.
    drempel_1f = min_stroom_a * spanning_v
    drempel_3f = min_stroom_a * spanning_v * 3

    if fase_modus == "1":
        doel_fasen = 1
    elif fase_modus == "3":
        doel_fasen = 3
    else:
        if huidige_fasen == 3:
            doel_fasen = 3 if doel_vermogen_w >= drempel_3f else 1
        else:
            doel_fasen = 3 if doel_vermogen_w >= (drempel_3f + hysterese_dynamisch) else 1

    # Stap 6: Doelstroom voor het gekozen aantal fases
    if doel_vermogen_w < drempel_1f:
        doel_stroom_a = min_stroom_a
        # Fase alleen overschrijven in auto-modus; een geforceerde fase_modus respecteren
        if fase_modus == "auto":
            doel_fasen = 1
    else:
        doel_stroom_a = _clamp(
            doel_vermogen_w / (spanning_v * doel_fasen), min_stroom_a, max_stroom_a
        )

    logger.debug(
        "SolarFlow: ema=%.0fW, surplus=%.0fW, doelverm=%.0fW, "
        "doel=%.1fA×%df, score=%.3f (hysterese_factor=%.1f)",
        ema_net_vermogen_w,
        beschikbaar_surplus_w,
        doel_vermogen_w,
        doel_stroom_a,
        doel_fasen,
        score,
        hysterese_factor,
    )

    return (doel_stroom_a, doel_fasen, score)


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
    doel_net_vermogen_w: float = 0.0,
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
        doel_net_vermogen_w: Streefwaarde voor netvermogen in Watt (standaard 0W).
                             Negatief = bewust exporteren (bijv. -100W).

    Returns:
        tuple (doel_stroom_a, doel_fasen):
            doel_stroom_a — gewenste laadstroom in Ampere (al begrensd)
            doel_fasen    — gewenst aantal fases (1 of 3)
    """
    # Stap 1: Bereken hoeveel vermogen we in totaal willen gebruiken voor laden.
    # beschikbaar_surplus_w: positief = we hebben overschot t.o.v. het target.
    # Met doel_net_vermogen_w=-100: 100W extra surplus beschikbaar (streef naar lichte export).
    beschikbaar_surplus_w = -(net_vermogen_w + veiligheidsbuffer_w - doel_net_vermogen_w)

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
        # Fase alleen overschrijven in auto-modus; een geforceerde fase_modus respecteren
        if fase_modus == "auto":
            doel_fasen = 1
    else:
        doel_stroom_a = doel_vermogen_w / (spanning_v * doel_fasen)
        doel_stroom_a = _clamp(doel_stroom_a, min_stroom_a, max_stroom_a)

    logger.debug(
        "Controller: net=%dW, huidig=%.1fA×%df → doel=%.1fA×%df "
        "(surplus=%.0fW, doelvermogen=%.0fW, target=%dW)",
        net_vermogen_w,
        huidig_stroom_a,
        huidige_fasen,
        doel_stroom_a,
        doel_fasen,
        beschikbaar_surplus_w,
        doel_vermogen_w,
        doel_net_vermogen_w,
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
