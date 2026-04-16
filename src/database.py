"""
SQLite database voor het loggen van metingen en gebeurtenissen.

Alle API-metingen en statuswijzigingen worden opgeslagen zodat je later
grafieken kunt toevoegen of problemen kunt terugzoeken.
"""

import logging
import sqlite3
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _verbinding(db_pad: str) -> sqlite3.Connection:
    """Opent een SQLite-verbinding met row_factory voor dict-resultaten."""
    conn = sqlite3.connect(db_pad)
    conn.row_factory = sqlite3.Row
    return conn


def init_database(db_pad: str) -> None:
    """
    Maakt de database-tabellen aan als ze nog niet bestaan.
    Veilig om meerdere keren aan te roepen (idempotent).

    Args:
        db_pad: Pad naar het SQLite-databasebestand.
    """
    try:
        with _verbinding(db_pad) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS metingen (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    tijdstip          TEXT    NOT NULL,
                    net_vermogen_w    REAL    NOT NULL,
                    auto_aangesloten  INTEGER NOT NULL,
                    gesteld_stroom_a  REAL,
                    huidige_fasen     INTEGER,
                    controller_actief INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    tijdstip    TEXT NOT NULL,
                    event_type  TEXT NOT NULL,
                    details     TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_metingen_tijdstip
                    ON metingen(tijdstip);

                CREATE INDEX IF NOT EXISTS idx_events_tijdstip
                    ON events(tijdstip);

                CREATE TABLE IF NOT EXISTS sessies (
                    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_tijdstip              TEXT    NOT NULL,
                    eind_tijdstip               TEXT,
                    model                       TEXT,
                    duur_s                      INTEGER,
                    no_import_count             INTEGER DEFAULT 0,
                    no_export_count             INTEGER DEFAULT 0,
                    fase_wissel_count           INTEGER DEFAULT 0,
                    gem_afwijking_w             REAL,
                    gem_score                   REAL,
                    geladen_kwh                 REAL,
                    beste_kwartier_tijdstip     TEXT,
                    slechtste_kwartier_tijdstip TEXT
                );

                CREATE TABLE IF NOT EXISTS cyclus_scores (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    sessie_id   INTEGER NOT NULL,
                    tijdstip    TEXT    NOT NULL,
                    score       REAL    NOT NULL,
                    afwijking_w REAL    NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sessies_start
                    ON sessies(start_tijdstip);

                CREATE INDEX IF NOT EXISTS idx_cyclus_sessie
                    ON cyclus_scores(sessie_id);
            """)
            # Kolom popup_getoond toevoegen als die nog niet bestaat (idempotent).
            try:
                conn.execute(
                    "ALTER TABLE sessies ADD COLUMN popup_getoond INTEGER DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass  # Kolom bestaat al — geen actie nodig.
        logger.debug("Database geïnitialiseerd: %s", db_pad)
    except sqlite3.Error as e:
        logger.error("Database initialisatie mislukt: %s", e)
        raise


def sla_meting_op(
    db_pad: str,
    net_vermogen_w: float,
    auto_aangesloten: bool,
    gesteld_stroom_a: float | None,
    huidige_fasen: int | None,
    controller_actief: bool,
) -> None:
    """
    Slaat een meting op in de database.

    Args:
        db_pad:            Pad naar het databasebestand.
        net_vermogen_w:    Huidig netvermogen (positief=import, negatief=export).
        auto_aangesloten:  True als een auto aangesloten is.
        gesteld_stroom_a:  Ingestelde laadstroom in Ampere, of None als niet actief.
        huidige_fasen:     Actief aantal fases (1 of 3), of None als niet laden.
        controller_actief: True als de regelaar actief is.
    """
    tijdstip = datetime.now().isoformat(timespec="seconds")
    try:
        with _verbinding(db_pad) as conn:
            conn.execute(
                """
                INSERT INTO metingen
                    (tijdstip, net_vermogen_w, auto_aangesloten,
                     gesteld_stroom_a, huidige_fasen, controller_actief)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tijdstip,
                    net_vermogen_w,
                    1 if auto_aangesloten else 0,
                    gesteld_stroom_a,
                    huidige_fasen,
                    1 if controller_actief else 0,
                ),
            )
    except sqlite3.Error as e:
        logger.warning("Kon meting niet opslaan in database: %s", e)


def sla_event_op(db_pad: str, event_type: str, details: str = "") -> None:
    """
    Slaat een gebeurtenis op in de database.

    Event types die gebruikt worden:
        auto_aangesloten     — auto is zojuist aangesloten
        auto_losgekoppeld    — auto is losgekoppeld
        stroom_bijgesteld    — laadstroom is aangepast
        fase_gewisseld       — aantal fases is gewijzigd
        controller_aan       — regelaar is ingeschakeld
        controller_uit       — regelaar is uitgeschakeld
        fout                 — er is een fout opgetreden

    Args:
        db_pad:     Pad naar het databasebestand.
        event_type: Soort gebeurtenis (zie boven).
        details:    Beschrijving in leesbare tekst.
    """
    tijdstip = datetime.now().isoformat(timespec="seconds")
    try:
        with _verbinding(db_pad) as conn:
            conn.execute(
                "INSERT INTO events (tijdstip, event_type, details) VALUES (?, ?, ?)",
                (tijdstip, event_type, details),
            )
        logger.debug("Event opgeslagen: %s — %s", event_type, details)
    except sqlite3.Error as e:
        logger.warning("Kon event niet opslaan in database: %s", e)


def haal_recente_metingen_op(db_pad: str, limiet: int = 60) -> list[dict]:
    """
    Haalt de meest recente metingen op uit de database.

    Args:
        db_pad:  Pad naar het databasebestand.
        limiet:  Maximum aantal rijen om terug te geven.

    Returns:
        Lijst van dicts, nieuwste meting eerst.
    """
    try:
        with _verbinding(db_pad) as conn:
            rows = conn.execute(
                """
                SELECT tijdstip, net_vermogen_w, auto_aangesloten,
                       gesteld_stroom_a, huidige_fasen, controller_actief
                FROM metingen
                ORDER BY id DESC
                LIMIT ?
                """,
                (limiet,),
            ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        logger.warning("Kon metingen niet ophalen: %s", e)
        return []


def haal_recente_events_op(db_pad: str, limiet: int = 50) -> list[dict]:
    """
    Haalt de meest recente gebeurtenissen op uit de database.

    Args:
        db_pad:  Pad naar het databasebestand.
        limiet:  Maximum aantal rijen om terug te geven.

    Returns:
        Lijst van dicts, nieuwste event eerst.
    """
    try:
        with _verbinding(db_pad) as conn:
            rows = conn.execute(
                """
                SELECT tijdstip, event_type, details
                FROM events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limiet,),
            ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        logger.warning("Kon events niet ophalen: %s", e)
        return []


def start_sessie(db_pad: str, model: str) -> int | None:
    """
    Start een nieuwe laadsessie in de database.

    Args:
        db_pad: Pad naar het databasebestand.
        model:  Naam van het gebruikte regelaarmodel (bijv. "legacy" of "solarflow").

    Returns:
        Het sessie-ID (integer) van de nieuwe sessie, of None bij een fout.
    """
    tijdstip = datetime.now().isoformat(timespec="seconds")
    try:
        with _verbinding(db_pad) as conn:
            cursor = conn.execute(
                "INSERT INTO sessies (start_tijdstip, model) VALUES (?, ?)",
                (tijdstip, model),
            )
            sessie_id = cursor.lastrowid
        logger.info("Sessie %d gestart (model: %s)", sessie_id, model)
        return sessie_id
    except sqlite3.Error as e:
        logger.warning("Kon sessie niet starten: %s", e)
        return None


def sluit_sessie(db_pad: str, sessie_id: int, eind_data: dict) -> None:
    """
    Sluit een bestaande laadsessie af met eindwaarden.

    gem_score en gem_afwijking_w worden berekend uit de cyclus_scores-tabel.
    geladen_kwh wordt verwacht als eind_data["geladen_kwh"] (float, in kWh).

    Args:
        db_pad:     Pad naar het databasebestand.
        sessie_id:  ID van de te sluiten sessie.
        eind_data:  Dict met eindwaarden. Verwachte sleutels:
                      duur_s, no_import_count, no_export_count,
                      fase_wissel_count, geladen_kwh
    """
    tijdstip = datetime.now().isoformat(timespec="seconds")
    try:
        with _verbinding(db_pad) as conn:
            conn.execute(
                """
                UPDATE sessies SET
                    eind_tijdstip     = ?,
                    duur_s            = ?,
                    no_import_count   = ?,
                    no_export_count   = ?,
                    fase_wissel_count = ?
                WHERE id = ?
                """,
                (
                    tijdstip,
                    eind_data.get("duur_s"),
                    eind_data.get("no_import_count", 0),
                    eind_data.get("no_export_count", 0),
                    eind_data.get("fase_wissel_count", 0),
                    sessie_id,
                ),
            )
            # Bereken gemiddelden uit cyclus_scores en sla ze op.
            rij = conn.execute(
                "SELECT AVG(score), AVG(afwijking_w) FROM cyclus_scores WHERE sessie_id = ?",
                (sessie_id,),
            ).fetchone()
            gem_score = round(rij[0], 4) if rij[0] is not None else None
            gem_afwijking_w = round(rij[1], 1) if rij[1] is not None else None
            conn.execute(
                """
                UPDATE sessies SET
                    gem_score       = ?,
                    gem_afwijking_w = ?,
                    geladen_kwh     = ?
                WHERE id = ?
                """,
                (gem_score, gem_afwijking_w, eind_data.get("geladen_kwh"), sessie_id),
            )
        logger.info(
            "Sessie %d afgesloten (duur: %ds, score: %s, kWh: %s, "
            "NO import: %d, export: %d, fasewissel: %d)",
            sessie_id,
            eind_data.get("duur_s", 0),
            f"{gem_score:.4f}" if gem_score is not None else "—",
            f"{eind_data.get('geladen_kwh', 0):.3f}",
            eind_data.get("no_import_count", 0),
            eind_data.get("no_export_count", 0),
            eind_data.get("fase_wissel_count", 0),
        )
    except sqlite3.Error as e:
        logger.warning("Kon sessie %d niet afsluiten: %s", sessie_id, e)


def sla_cyclus_score_op(
    db_pad: str,
    sessie_id: int,
    score: float,
    afwijking_w: float,
) -> None:
    """
    Slaat de Gaussische score van één meetcyclus op.

    Wordt aangeroepen door het SolarFlow-algoritme (sessie 4+).
    Functie bestaat al zodat sessie 4 hem direct kan aanroepen zonder databasewijziging.

    Args:
        db_pad:      Pad naar het databasebestand.
        sessie_id:   ID van de lopende sessie.
        score:       Gaussische score (0.0–1.0).
        afwijking_w: Afwijking van het doelvermogen in Watt.
    """
    tijdstip = datetime.now().isoformat(timespec="seconds")
    try:
        with _verbinding(db_pad) as conn:
            conn.execute(
                """
                INSERT INTO cyclus_scores (sessie_id, tijdstip, score, afwijking_w)
                VALUES (?, ?, ?, ?)
                """,
                (sessie_id, tijdstip, score, afwijking_w),
            )
    except sqlite3.Error as e:
        logger.warning("Kon cyclusscore niet opslaan: %s", e)


def haal_ongeziene_sessie_op(db_pad: str) -> dict | None:
    """
    Haalt de meest recente afgesloten sessie op die nog niet als gezien is gemarkeerd.

    Wordt gebruikt door het dashboard om de eenmalige sessie-popup te tonen.

    Args:
        db_pad: Pad naar het databasebestand.

    Returns:
        Dict met sessiegegevens, of None als er geen ongeziene sessie is.
    """
    try:
        with _verbinding(db_pad) as conn:
            row = conn.execute(
                """
                SELECT id, start_tijdstip, eind_tijdstip, model, duur_s,
                       no_import_count, no_export_count, fase_wissel_count,
                       gem_score, gem_afwijking_w, geladen_kwh,
                       beste_kwartier_tijdstip, slechtste_kwartier_tijdstip
                FROM sessies
                WHERE popup_getoond = 0 AND eind_tijdstip IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error as e:
        logger.warning("Kon ongeziene sessie niet ophalen: %s", e)
        return None


def markeer_popup_getoond(db_pad: str, sessie_id: int) -> None:
    """
    Markeert een sessie als gezien zodat de popup niet meer automatisch verschijnt.

    Args:
        db_pad:     Pad naar het databasebestand.
        sessie_id:  ID van de sessie.
    """
    try:
        with _verbinding(db_pad) as conn:
            conn.execute(
                "UPDATE sessies SET popup_getoond = 1 WHERE id = ?",
                (sessie_id,),
            )
    except sqlite3.Error as e:
        logger.warning("Kon popup-status niet bijwerken voor sessie %d: %s", sessie_id, e)


def haal_sessies_op(db_pad: str, pagina: int = 1, per_pagina: int = 10) -> dict:
    """
    Haalt een gepagineerde lijst van afgesloten sessies op, nieuwste eerst.

    Args:
        db_pad:      Pad naar het databasebestand.
        pagina:      Paginanummer (1-gebaseerd).
        per_pagina:  Aantal sessies per pagina.

    Returns:
        Dict met sleutels: sessies (lijst), totaal, pagina, per_pagina, paginas.
    """
    import math

    pagina = max(1, pagina)
    offset = (pagina - 1) * per_pagina
    try:
        with _verbinding(db_pad) as conn:
            totaal = conn.execute(
                "SELECT COUNT(*) FROM sessies WHERE eind_tijdstip IS NOT NULL"
            ).fetchone()[0]
            rows = conn.execute(
                """
                SELECT id, start_tijdstip, eind_tijdstip, model, duur_s,
                       no_import_count, no_export_count, fase_wissel_count,
                       gem_score, gem_afwijking_w, geladen_kwh,
                       beste_kwartier_tijdstip, slechtste_kwartier_tijdstip
                FROM sessies
                WHERE eind_tijdstip IS NOT NULL
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (per_pagina, offset),
            ).fetchall()
        return {
            "sessies": [dict(row) for row in rows],
            "totaal": totaal,
            "pagina": pagina,
            "per_pagina": per_pagina,
            "paginas": max(1, math.ceil(totaal / per_pagina)),
        }
    except sqlite3.Error as e:
        logger.warning("Kon sessies niet ophalen: %s", e)
        return {"sessies": [], "totaal": 0, "pagina": pagina, "per_pagina": per_pagina, "paginas": 1}


def verwijder_oude_data(db_pad: str, bewaarperiode_dagen: int) -> None:
    """
    Verwijdert metingen en events ouder dan de opgegeven bewaarperiode.

    Sessies en cyclus_scores worden nooit verwijderd — die blijven altijd bewaard.

    Args:
        db_pad:               Pad naar het databasebestand.
        bewaarperiode_dagen:  Aantal dagen dat data bewaard blijft.
    """
    grens = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    grens_str = (grens - timedelta(days=bewaarperiode_dagen)).isoformat()
    try:
        with _verbinding(db_pad) as conn:
            r_metingen = conn.execute(
                "DELETE FROM metingen WHERE tijdstip < ?", (grens_str,)
            )
            r_events = conn.execute(
                "DELETE FROM events WHERE tijdstip < ?", (grens_str,)
            )
            verwijderd = r_metingen.rowcount + r_events.rowcount
        if verwijderd > 0:
            logger.info(
                "Opschoning: %d rijen verwijderd (ouder dan %d dagen, grens: %s)",
                verwijderd,
                bewaarperiode_dagen,
                grens_str,
            )
        else:
            logger.debug(
                "Opschoning: niets te verwijderen (grens: %s)", grens_str
            )
    except sqlite3.Error as e:
        logger.warning("Opschoning mislukt: %s", e)
