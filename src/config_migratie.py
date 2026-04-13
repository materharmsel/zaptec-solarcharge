"""
Configuratie-migratiemodule voor Zaptec Solarcharge.

Bij elke update kan config.yaml.example nieuwe velden krijgen.
Deze module zorgt ervoor dat ontbrekende velden worden toegevoegd aan de
bestaande config.yaml, zonder bestaande waarden ooit te overschrijven.

Gebruik (in main.py):
    from src.config_migratie import migreer_config
    toegevoegd = migreer_config()
    if toegevoegd:
        config = laad_config("config/config.yaml")  # opnieuw inladen
"""

import re
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def _lees_yaml(pad: str) -> dict:
    """Laadt een YAML-bestand als dict."""
    with open(pad, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _waarde_naar_yaml_string(waarde) -> str:
    """
    Converteert een Python-waarde naar een inline YAML-representatie.
    Let op: isinstance(bool) vóór isinstance(str/int/float) — bool is subclass van int.
    """
    if isinstance(waarde, bool):
        return "true" if waarde else "false"
    elif isinstance(waarde, str):
        return f'"{waarde}"'
    elif isinstance(waarde, float) and waarde == int(waarde):
        return str(int(waarde))   # 6.0 → "6"
    else:
        return str(waarde)


def _vind_einde_sectie(inhoud: str, sectie_naam: str) -> int:
    """
    Vindt de invoegpositie aan het einde van een sectie in de YAML-tekst.
    Retourneert de positie van de lege scheidingsregel voor de volgende sectie,
    zodat nieuwe velden netjes vóór de witruimte komen.
    Retourneert -1 als de sectie niet gevonden wordt.
    """
    match = re.search(rf'^{re.escape(sectie_naam)}:', inhoud, re.MULTILINE)
    if not match:
        return -1

    # Zoek het eerste \n dat direct gevolgd wordt door een niet-spatie-teken
    # (= begin van de volgende top-level sectie of einde bestand)
    rest = inhoud[match.end():]
    volgende = re.search(r'\n(?=\S)', rest)
    if volgende:
        # Positie vóór de scheidende lege regel — zodat die lege regel bewaard blijft
        return match.end() + volgende.start()
    else:
        return len(inhoud)


def _extraheer_sectie_uit_voorbeeld(voorbeeld_tekst: str, sectie_naam: str) -> str:
    """
    Extraheert een complete sectie inclusief direct erboven staande commentaarregels
    uit de voorbeeldtekst.
    """
    regels = voorbeeld_tekst.splitlines(keepends=True)
    header_index = None

    for i, regel in enumerate(regels):
        if re.match(rf'^{re.escape(sectie_naam)}:', regel):
            header_index = i
            break

    if header_index is None:
        return ""

    # Commentaarregels direct boven de header ophalen (stop bij lege regel)
    start_index = header_index
    for j in range(header_index - 1, -1, -1):
        if regels[j].strip().startswith("#"):
            start_index = j
        else:
            break

    # Einde van de sectie: volgende top-level sectie (niet ingesprongen, geen commentaar)
    einde_index = len(regels)
    for k in range(header_index + 1, len(regels)):
        regel = regels[k]
        if regel and not regel[0].isspace() and not regel.startswith("#") and ":" in regel:
            einde_index = k
            break

    return "".join(regels[start_index:einde_index])


def migreer_config(config_pad: str = "config/config.yaml",
                   voorbeeld_pad: str = "config/config.yaml.example") -> list:
    """
    Vergelijkt config.yaml met config.yaml.example en voegt ontbrekende keys toe.
    Bestaande waarden worden NOOIT overschreven. Commentaar in config.yaml blijft bewaard.

    Args:
        config_pad:   Pad naar de lokale configuratie (standaard: config/config.yaml).
        voorbeeld_pad: Pad naar het voorbeeld/schema-bestand (standaard: config/config.yaml.example).

    Returns:
        Lijst van volledige sleutelpaden die zijn toegevoegd,
        bijv. ["zaptec.nieuw_veld", "laadregeling.extra_buffer_w"].
        Lege lijst als alles al up-to-date is.
    """
    if not Path(voorbeeld_pad).exists():
        logger.debug("config.yaml.example niet gevonden — migratie overgeslagen.")
        return []
    if not Path(config_pad).exists():
        logger.debug("config.yaml niet gevonden — migratie overgeslagen.")
        return []

    config = _lees_yaml(config_pad)
    voorbeeld = _lees_yaml(voorbeeld_pad)

    with open(config_pad, encoding="utf-8") as f:
        inhoud = f.read()
    with open(voorbeeld_pad, encoding="utf-8") as f:
        voorbeeld_tekst = f.read()

    toegevoegd = []

    for sectie, sectie_waarden in voorbeeld.items():
        if sectie == "config_versie":
            # Versieveld wordt niet via migratie bijgehouden
            continue

        if not isinstance(sectie_waarden, dict):
            # Top-level scalaire waarde (komt zelden voor in dit project)
            if sectie not in config:
                inhoud = inhoud.rstrip("\n") + f"\n{sectie}: {_waarde_naar_yaml_string(sectie_waarden)}\n"
                toegevoegd.append(sectie)
                logger.info("Config-migratie: nieuw veld toegevoegd: %s = %s", sectie, sectie_waarden)
            continue

        if sectie not in config:
            # Hele sectie ontbreekt: voeg inclusief commentaar toe vanuit voorbeeld
            sectie_blok = _extraheer_sectie_uit_voorbeeld(voorbeeld_tekst, sectie)
            if sectie_blok:
                inhoud = inhoud.rstrip("\n") + "\n\n" + sectie_blok
                for sleutel in sectie_waarden:
                    if not isinstance(sectie_waarden[sleutel], dict):
                        vol_pad = f"{sectie}.{sleutel}"
                        toegevoegd.append(vol_pad)
                        logger.info("Config-migratie: nieuwe sectie met veld: %s", vol_pad)
            continue

        # Sectie bestaat — controleer op ontbrekende sleutels
        huidige_sectie = config.get(sectie, {})
        for sleutel, standaard in sectie_waarden.items():
            if isinstance(standaard, dict):
                # Geneste sub-secties worden in dit project niet gebruikt — sla over
                continue
            if sleutel in huidige_sectie:
                continue

            einde_pos = _vind_einde_sectie(inhoud, sectie)
            if einde_pos == -1:
                logger.warning(
                    "Sectie '%s' niet gevonden in config.yaml — kan '%s' niet toevoegen.",
                    sectie, sleutel,
                )
                continue

            nieuwe_regel = f"  {sleutel}: {_waarde_naar_yaml_string(standaard)}\n"
            inhoud = inhoud[:einde_pos] + nieuwe_regel + inhoud[einde_pos:]

            vol_pad = f"{sectie}.{sleutel}"
            toegevoegd.append(vol_pad)
            logger.info(
                "Config-migratie: nieuw veld toegevoegd: %s = %s", vol_pad, standaard
            )

    if toegevoegd:
        with open(config_pad, "w", encoding="utf-8") as f:
            f.write(inhoud)
        logger.info(
            "Config-migratie klaar: %d veld(en) toegevoegd aan config.yaml.",
            len(toegevoegd),
        )

    return toegevoegd
