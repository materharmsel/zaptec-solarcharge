"""
SQLite database voor het loggen van metingen en gebeurtenissen.

Alle API-metingen en statuswijzigingen worden opgeslagen zodat je later
grafieken kunt toevoegen of problemen kunt terugzoeken.
"""

import logging
import sqlite3
from datetime import datetime

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
            """)
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
