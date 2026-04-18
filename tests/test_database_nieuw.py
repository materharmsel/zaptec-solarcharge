import os
import sqlite3
import tempfile
import pytest
from src.database import init_database, sla_meting_op, sla_event_op, start_sessie, sluit_sessie
from src.database import haal_metingen_tijdvenster, haal_sessie_metingen


@pytest.fixture
def db():
    fd, pad = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_database(pad)
    yield pad
    try:
        os.unlink(pad)
    except PermissionError:
        pass  # Windows houdt het bestand soms kort vast — geen probleem op de Pi.


def test_haal_metingen_tijdvenster_leeg(db):
    result = haal_metingen_tijdvenster(db, minuten=30)
    assert result == []


def test_haal_metingen_tijdvenster_geeft_rijen(db):
    sla_meting_op(db, net_vermogen_w=-200.0, auto_aangesloten=True,
                  gesteld_stroom_a=11.0, huidige_fasen=1, controller_actief=True)
    result = haal_metingen_tijdvenster(db, minuten=5)
    assert len(result) == 1
    assert "tijdstip" in result[0]
    assert "net_vermogen_w" in result[0]
    assert "gesteld_stroom_a" in result[0]
    assert "huidige_fasen" in result[0]


def test_haal_sessie_metingen_onbekende_sessie(db):
    result = haal_sessie_metingen(db, sessie_id=9999)
    assert result["metingen"] == []
    assert result["events"] == []


def test_haal_sessie_metingen_geeft_data(db):
    sessie_id = start_sessie(db, model="solarflow")
    sla_meting_op(db, net_vermogen_w=-150.0, auto_aangesloten=True,
                  gesteld_stroom_a=8.0, huidige_fasen=1, controller_actief=True)
    sla_event_op(db, event_type="fase_wissel", details="1->3")
    sluit_sessie(db, sessie_id, {
        "duur_s": 3600, "no_import_count": 0, "no_export_count": 1,
        "fase_wissel_count": 1, "gem_score": 85.0, "gem_afwijking_w": 90.0,
        "geladen_kwh": 5.2,
    })
    result = haal_sessie_metingen(db, sessie_id=sessie_id)
    assert len(result["metingen"]) >= 1
    assert len(result["events"]) >= 1


def test_haal_metingen_tijdvenster_filtert_oude_data(db):
    """Metingen ouder dan het tijdvenster mogen NIET worden teruggegeven."""
    import sqlite3 as _sqlite3
    # Voeg een meting in die 2 uur geleden was (buiten het venster)
    with _sqlite3.connect(db) as conn:
        conn.row_factory = _sqlite3.Row
        conn.execute(
            """INSERT INTO metingen
               (tijdstip, net_vermogen_w, auto_aangesloten, gesteld_stroom_a,
                huidige_fasen, controller_actief)
               VALUES (datetime('now', '-120 minutes'), -999.0, 0, 0.0, 1, 0)"""
        )
        conn.commit()

    # Met venster van 5 minuten mag de 2 uur oude meting er NIET in zitten
    result = haal_metingen_tijdvenster(db, minuten=5)
    assert result == [], f"Verwachtte lege lijst, maar kreeg: {result}"

    # Met venster van 180 minuten moet de meting er WEL in zitten
    result_breed = haal_metingen_tijdvenster(db, minuten=180)
    assert len(result_breed) == 1
    assert result_breed[0]["net_vermogen_w"] == -999.0
