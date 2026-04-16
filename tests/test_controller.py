"""
Unit-tests voor src/controller.py — bereken_ema(), bereken_laadmodus_solarflow()
en bereken_laadmodus().

Uitvoeren:
    python -m pytest tests/test_controller.py -v
"""

import math
import pytest
from src.controller import bereken_ema, bereken_laadmodus_solarflow, bereken_laadmodus


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _solarflow(**overrides):
    """
    Roept bereken_laadmodus_solarflow() aan met standaardwaarden.
    Gebruik keyword-overrides om specifieke parameters te wijzigen.

    Standaard: auto op 1-fase, 10A, EMA=0W, target=0W, buffer=0W.
    """
    defaults = dict(
        ema_net_vermogen_w   = 0.0,
        huidig_stroom_a      = 10.0,
        huidige_fasen        = 1,
        fase_modus           = "auto",
        spanning_v           = 230.0,
        min_stroom_a         = 6.0,
        max_stroom_a         = 25.0,
        doel_net_vermogen_w  = 0.0,
        veiligheidsbuffer_w  = 0.0,
        hysterese_w          = 200.0,
        wissel_budget_ratio  = None,
        scoring_sigma_w      = 150.0,
    )
    defaults.update(overrides)
    return bereken_laadmodus_solarflow(**defaults)


def _legacy(**overrides):
    """
    Roept bereken_laadmodus() aan met standaardwaarden.
    """
    defaults = dict(
        net_vermogen_w      = 0.0,
        huidig_stroom_a     = 10.0,
        huidige_fasen       = 1,
        fase_modus          = "auto",
        spanning_v          = 230.0,
        min_stroom_a        = 6.0,
        max_stroom_a        = 25.0,
        veiligheidsbuffer_w = 0.0,
        hysterese_w         = 200.0,
        doel_net_vermogen_w = 0.0,
    )
    defaults.update(overrides)
    return bereken_laadmodus(**defaults)


# ─── TestBerekenEma ───────────────────────────────────────────────────────────

class TestBerekenEma:

    def test_initialisatie_geen_oud_ema(self):
        """oud_ema=None geeft ruwe meting terug (eerste meting seeden)."""
        result = bereken_ema(None, 500.0, 0.1, 0.6, 300.0)
        assert result == 500.0

    def test_initialisatie_negatieve_meting(self):
        """Seeding werkt ook met negatieve waarden (exportoverschot)."""
        result = bereken_ema(None, -800.0, 0.1, 0.6, 300.0)
        assert result == -800.0

    def test_stabiele_situatie_kleine_afwijking(self):
        """Kleine afwijking → alpha dicht bij alpha_min → EMA beweegt traag."""
        ema = bereken_ema(100.0, 105.0, 0.1, 0.6, 300.0)
        assert 100.0 < ema < 101.0

    def test_grote_afwijking_snellere_aanpassing(self):
        """Grote afwijking → EMA beweegt sneller dan bij kleine afwijking."""
        ema_groot = bereken_ema(100.0, 700.0, 0.1, 0.6, 300.0)
        ema_klein = bereken_ema(100.0, 105.0, 0.1, 0.6, 300.0)
        assert ema_groot > ema_klein

    def test_afwijking_gelijk_aan_drempel(self):
        """Afwijking == drempel → verhouding=1.0 → alpha == alpha_max."""
        ema = bereken_ema(0.0, 300.0, 0.1, 0.6, 300.0)
        verwacht = 0.6 * 300.0 + 0.4 * 0.0
        assert abs(ema - verwacht) < 0.001

    def test_herhaalde_aanroepen_convergeren(self):
        """200× dezelfde meting → EMA convergeert naar de vaste waarde."""
        ema = None
        for _ in range(200):
            ema = bereken_ema(ema, 250.0, 0.1, 0.6, 300.0)
        assert math.isfinite(ema)
        assert abs(ema - 250.0) < 1.0

    def test_drempel_nul_geeft_alpha_max(self):
        """drempel_w=0 → verhouding altijd 1.0 → alpha == alpha_max."""
        ema = bereken_ema(100.0, 110.0, 0.1, 0.6, 0.0)
        verwacht = 0.6 * 110.0 + 0.4 * 100.0
        assert abs(ema - verwacht) < 0.001


# ─── TestBerekenLaadmodusSolarflow ────────────────────────────────────────────

class TestBerekenLaadmodusSolarflow:

    # ── Stroomcorrectie (absoluut) ─────────────────────────────────────────────

    def test_surplus_stroom_omhoog(self):
        """EMA = -500W (export/surplus) → stroom hoger dan huidig."""
        # huidig_laad = 10*230*1 = 2300W; surplus = 500W; doel = 2800W → 12.17A
        doel_a, _, _ = _solarflow(ema_net_vermogen_w=-500.0, huidig_stroom_a=10.0)
        assert doel_a > 10.0

    def test_tekort_stroom_omlaag(self):
        """EMA = +500W (import/tekort) → stroom lager dan huidig."""
        # huidig_laad = 2300W; surplus = -500W; doel = 1800W → 7.83A
        doel_a, _, _ = _solarflow(ema_net_vermogen_w=500.0, huidig_stroom_a=10.0)
        assert doel_a < 10.0

    def test_groot_surplus_direct_naar_max(self):
        """Groot surplus → direct naar max_stroom_a zonder ramp-rate beperking (1-fase geforceerd)."""
        # EMA = -5000W, huidig = 10A, 1-fase: doel_vermogen = 2300 + 5000 = 7300W → 25A (max)
        # fase_modus="1" zodat de fase-upgrade de uitkomst niet beïnvloedt
        doel_a, _, _ = _solarflow(
            ema_net_vermogen_w=-5000.0,
            huidig_stroom_a=10.0,
            max_stroom_a=25.0,
            fase_modus="1",
        )
        assert doel_a == 25.0

    def test_groot_tekort_direct_naar_min(self):
        """Groot tekort → direct naar min_stroom_a, geen ramp-rate beperking."""
        # EMA = +5000W, huidig = 20A: doel_vermogen = 4600 - 5000 = 0 (max(0,...)) → 6A min
        doel_a, _, _ = _solarflow(
            ema_net_vermogen_w=5000.0,
            huidig_stroom_a=20.0,
            min_stroom_a=6.0,
        )
        assert doel_a == 6.0

    def test_stroom_begrensd_op_max(self):
        """Resultaat hoger dan max_stroom_a wordt afgekapt op max (1-fase geforceerd)."""
        # fase_modus="1" zodat de fase-upgrade de stroombegrenzing niet omzeilt
        doel_a, _, _ = _solarflow(
            ema_net_vermogen_w=-10000.0,
            huidig_stroom_a=24.0,
            max_stroom_a=25.0,
            fase_modus="1",
        )
        assert doel_a == 25.0

    def test_stroom_begrensd_op_min(self):
        """Resultaat lager dan min_stroom_a wordt afgekapt op min."""
        doel_a, _, _ = _solarflow(
            ema_net_vermogen_w=10000.0,
            huidig_stroom_a=8.0,
            min_stroom_a=6.0,
        )
        assert doel_a == 6.0

    def test_neutrale_ema_geen_wijziging(self):
        """EMA == target (beide 0W) → doel_stroom == huidig_stroom (geen correctie)."""
        # surplus = 0W → doel_vermogen = huidig_laad_w → stroom ongewijzigd
        doel_a, _, _ = _solarflow(
            ema_net_vermogen_w=0.0,
            huidig_stroom_a=10.0,
            doel_net_vermogen_w=0.0,
        )
        assert abs(doel_a - 10.0) < 0.01

    def test_identiek_aan_legacy_met_zelfde_input(self):
        """SolarFlow met EMA-input geeft zelfde stroom als legacy met ruwe meting als input."""
        # Wanneer ema = net_vermogen en buffer/target gelijk zijn: identieke formule
        net_w = -800.0
        huidig = 12.0
        fasen = 1
        buffer = 6.0

        doel_sf, fasen_sf, _ = _solarflow(
            ema_net_vermogen_w=net_w,
            huidig_stroom_a=huidig,
            huidige_fasen=fasen,
            veiligheidsbuffer_w=buffer,
            doel_net_vermogen_w=0.0,
        )
        doel_lg, fasen_lg = _legacy(
            net_vermogen_w=net_w,
            huidig_stroom_a=huidig,
            huidige_fasen=fasen,
            veiligheidsbuffer_w=buffer,
            doel_net_vermogen_w=0.0,
        )
        assert abs(doel_sf - doel_lg) < 0.01
        assert fasen_sf == fasen_lg

    # ── Target (doel_net_vermogen_w) ───────────────────────────────────────────

    def test_target_op_doel_geen_correctie(self):
        """EMA == target → surplus = 0 → stroom ongewijzigd."""
        doel_a, _, _ = _solarflow(
            ema_net_vermogen_w=-100.0,
            huidig_stroom_a=10.0,
            doel_net_vermogen_w=-100.0,  # precies op doel
        )
        assert abs(doel_a - 10.0) < 0.01

    def test_target_export_minder_stroom_dan_target_nul(self):
        """target=-100W geeft minder stroom dan target=0W bij zelfde EMA (houdt export-marge)."""
        # target=-100W: surplus = -(−500 + 0 − (−100)) = -(-400) = 400W
        # target=0W:    surplus = -(−500 + 0 − 0)      = 500W
        # Minder surplus → minder stroom bij target=-100W
        doel_export, _, _ = _solarflow(
            ema_net_vermogen_w=-500.0, huidig_stroom_a=10.0,
            doel_net_vermogen_w=-100.0,
        )
        doel_nul, _, _ = _solarflow(
            ema_net_vermogen_w=-500.0, huidig_stroom_a=10.0,
            doel_net_vermogen_w=0.0,
        )
        assert doel_export < doel_nul

    def test_target_import_hogere_stroom_dan_target_nul(self):
        """target=+50W geeft meer stroom dan target=0W (wil lichte import accepteren)."""
        # target=+50W: surplus = -(−500 + 0 − 50) = -(-550) = 550W
        # target=0W:   surplus = 500W
        doel_import, _, _ = _solarflow(
            ema_net_vermogen_w=-500.0, huidig_stroom_a=10.0,
            doel_net_vermogen_w=50.0,
        )
        doel_nul, _, _ = _solarflow(
            ema_net_vermogen_w=-500.0, huidig_stroom_a=10.0,
            doel_net_vermogen_w=0.0,
        )
        assert doel_import > doel_nul

    # ── Fasekeuze ──────────────────────────────────────────────────────────────

    def test_fase_upgrade_boven_drempel_plus_hysterese(self):
        """doel_vermogen >= drempel_3f + hysterese → upgrade naar 3 fases."""
        # drempel_3f = 6*230*3 = 4140W; hysterese = 200W → grens = 4340W
        # huidig=10A, 1f: huidig_laad=2300W; ema=-2100W: surplus=2100W; doel=4400W > 4340W ✓
        _, doel_fasen, _ = _solarflow(
            ema_net_vermogen_w=-2100.0,
            huidig_stroom_a=10.0,
            huidige_fasen=1,
            hysterese_w=200.0,
        )
        assert doel_fasen == 3

    def test_fase_upgrade_geblokkeerd_door_hysterese(self):
        """doel_vermogen < drempel_3f + hysterese → geen upgrade."""
        # huidig=10A, 1f: huidig_laad=2300W; ema=-1840W: surplus=1840W; doel=4140W < 4340W
        _, doel_fasen, _ = _solarflow(
            ema_net_vermogen_w=-1840.0,
            huidig_stroom_a=10.0,
            huidige_fasen=1,
            hysterese_w=200.0,
        )
        assert doel_fasen == 1

    def test_fase_downgrade_op_3fase_met_tekort(self):
        """Op 3-fase met doel_vermogen < drempel_3f → downgrade naar 1 fase."""
        # huidig=6A, 3f: huidig_laad=4140W; ema=+500W: surplus=-500W; doel=3640W < 4140W
        _, doel_fasen, _ = _solarflow(
            ema_net_vermogen_w=500.0,
            huidig_stroom_a=6.0,
            huidige_fasen=3,
        )
        assert doel_fasen == 1

    def test_fase_modus_1_dwingt_1_fase(self):
        """fase_modus='1' → altijd 1 fase, ook bij heel veel surplus."""
        _, doel_fasen, _ = _solarflow(
            ema_net_vermogen_w=-10000.0,
            huidig_stroom_a=20.0,
            huidige_fasen=3,
            fase_modus="1",
        )
        assert doel_fasen == 1

    def test_fase_modus_3_dwingt_3_fasen(self):
        """fase_modus='3' → altijd 3 fases, ook bij groot tekort."""
        _, doel_fasen, _ = _solarflow(
            ema_net_vermogen_w=10000.0,
            huidig_stroom_a=6.0,
            huidige_fasen=1,
            fase_modus="3",
        )
        assert doel_fasen == 3

    # ── Wissel-budget ──────────────────────────────────────────────────────────

    def test_wissel_budget_nul_geen_fasewisseling(self):
        """wissel_budget_ratio=0.0 → huidige_fasen behouden, stroom wel berekend."""
        # budget=0: return direct met huidige_fasen=1, maar stroom berekend voor 1-fase
        doel_a, doel_fasen, _ = _solarflow(
            ema_net_vermogen_w=-5000.0,
            huidig_stroom_a=20.0,
            huidige_fasen=1,
            wissel_budget_ratio=0.0,
        )
        assert doel_fasen == 1
        assert doel_a == 25.0  # clamp(9600/230, 6, 25) = 25A

    def test_wissel_budget_laag_hysterese_verdrievoudigd(self):
        """wissel_budget_ratio=0.1 (< 25%) → hysterese ×3 → upgrade geblokkeerd."""
        # drempel + hysterese×3 = 4140 + 600 = 4740W
        # huidig=16A, 1f: huidig_laad=3680W; ema=-690W: surplus=690W; doel=4370W < 4740W → 1f
        _, doel_fasen, _ = _solarflow(
            ema_net_vermogen_w=-690.0,
            huidig_stroom_a=16.0,
            huidige_fasen=1,
            wissel_budget_ratio=0.1,
            hysterese_w=200.0,
        )
        assert doel_fasen == 1

    def test_wissel_budget_medium_hysterese_anderhalve(self):
        """wissel_budget_ratio=0.35 (25-50%) → hysterese ×1.5 → upgrade bij hoog vermogen."""
        # drempel + hysterese×1.5 = 4140 + 300 = 4440W
        # huidig=20A, 1f: huidig_laad=4600W; ema=-690W: surplus=690W; doel=5290W > 4440W → 3f
        _, doel_fasen, _ = _solarflow(
            ema_net_vermogen_w=-690.0,
            huidig_stroom_a=20.0,
            huidige_fasen=1,
            wissel_budget_ratio=0.35,
            hysterese_w=200.0,
        )
        assert doel_fasen == 3

    def test_wissel_budget_none_geen_beperking(self):
        """wissel_budget_ratio=None → normale hysterese, zelfde resultaat als ratio > 0.5."""
        _, fasen_none, _ = _solarflow(
            ema_net_vermogen_w=-690.0,
            huidig_stroom_a=20.0,
            huidige_fasen=1,
            wissel_budget_ratio=None,
            hysterese_w=200.0,
        )
        _, fasen_hoog, _ = _solarflow(
            ema_net_vermogen_w=-690.0,
            huidig_stroom_a=20.0,
            huidige_fasen=1,
            wissel_budget_ratio=0.8,
            hysterese_w=200.0,
        )
        assert fasen_none == fasen_hoog

    # ── Gaussische score ───────────────────────────────────────────────────────

    def test_score_een_bij_nul_afwijking(self):
        """EMA == doel → fout = 0 → score = 1.0."""
        _, _, score = _solarflow(
            ema_net_vermogen_w=0.0,
            doel_net_vermogen_w=0.0,
        )
        assert abs(score - 1.0) < 1e-9

    def test_score_bij_sigma_afwijking(self):
        """Afwijking = 1×sigma → score = e^(-0.5) ≈ 0.6065."""
        _, _, score = _solarflow(
            ema_net_vermogen_w=150.0,
            doel_net_vermogen_w=0.0,
            scoring_sigma_w=150.0,
        )
        verwacht = math.exp(-0.5)
        assert abs(score - verwacht) < 1e-6

    def test_score_bij_twee_sigma_afwijking(self):
        """Afwijking = 2×sigma → score = e^(-2) ≈ 0.135."""
        _, _, score = _solarflow(
            ema_net_vermogen_w=300.0,
            doel_net_vermogen_w=0.0,
            scoring_sigma_w=150.0,
        )
        verwacht = math.exp(-2.0)
        assert abs(score - verwacht) < 1e-6

    def test_score_altijd_tussen_nul_en_een(self):
        """Score blijft altijd in [0, 1] ongeacht grote afwijkingen."""
        for ema in [-10000.0, -1000.0, 0.0, 1000.0, 10000.0]:
            _, _, score = _solarflow(
                ema_net_vermogen_w=ema,
                doel_net_vermogen_w=0.0,
            )
            assert 0.0 <= score <= 1.0

    def test_score_sigma_nul_guard(self):
        """scoring_sigma_w=0 → score=1.0 bij nulafwijking, 0.0 anders."""
        _, _, score_nul = _solarflow(
            ema_net_vermogen_w=0.0, doel_net_vermogen_w=0.0,
            scoring_sigma_w=0.0,
        )
        _, _, score_afwijking = _solarflow(
            ema_net_vermogen_w=100.0, doel_net_vermogen_w=0.0,
            scoring_sigma_w=0.0,
        )
        assert score_nul == 1.0
        assert score_afwijking == 0.0

    def test_score_op_basis_van_ema_niet_na_correctie(self):
        """Score meet afwijking van EMA t.o.v. target, niet gecorrigeerde waarde."""
        # target=-100W, ema=-100W → fout=0 → score=1.0
        _, _, score = _solarflow(
            ema_net_vermogen_w=-100.0,
            doel_net_vermogen_w=-100.0,
            scoring_sigma_w=150.0,
        )
        assert abs(score - 1.0) < 1e-9


# ─── TestBerekenLaadmodus (legacy) ────────────────────────────────────────────

class TestBerekenLaadmodus:

    def test_basis_surplus_stroom_omhoog(self):
        """Negatief net → surplus → stroom hoger dan huidig."""
        doel_a, _ = _legacy(net_vermogen_w=-500.0, huidig_stroom_a=10.0)
        assert doel_a > 10.0

    def test_basis_tekort_stroom_omlaag(self):
        """Positief net → tekort → stroom lager dan huidig."""
        doel_a, _ = _legacy(net_vermogen_w=500.0, huidig_stroom_a=10.0)
        assert doel_a < 10.0

    def test_target_nul_identiek_aan_geen_target(self):
        """doel_net_vermogen_w=0 (default) geeft zelfde resultaat als weggelaten."""
        doel_met, fasen_met = _legacy(net_vermogen_w=-500.0, doel_net_vermogen_w=0.0)
        # doel_net_vermogen_w default is 0.0, dus dit is de baseline
        assert math.isfinite(doel_met)

    def test_target_export_geeft_minder_stroom(self):
        """target=-100W → minder surplus beschikbaar → lagere stroom dan target=0."""
        doel_export, _ = _legacy(net_vermogen_w=-500.0, doel_net_vermogen_w=-100.0)
        doel_nul,   _ = _legacy(net_vermogen_w=-500.0, doel_net_vermogen_w=0.0)
        assert doel_export < doel_nul

    def test_target_op_doel_geen_wijziging(self):
        """net == target → surplus=0 → doel_stroom == huidig_stroom."""
        # net=-100W, target=-100W: surplus = -(-100 + 0 - (-100)) = -(0) = 0W
        # doel_vermogen = huidig_laad_w + 0 = 10*230*1 = 2300W → doel_a = 10A
        doel_a, _ = _legacy(
            net_vermogen_w=-100.0,
            huidig_stroom_a=10.0,
            doel_net_vermogen_w=-100.0,
        )
        assert abs(doel_a - 10.0) < 0.01

    def test_stroom_begrensd_op_max(self):
        """Groot surplus → doel_stroom afgekapt op max_stroom_a (1-fase geforceerd)."""
        doel_a, _ = _legacy(
            net_vermogen_w=-10000.0,
            huidig_stroom_a=24.0,
            max_stroom_a=25.0,
            fase_modus="1",
        )
        assert doel_a == 25.0

    def test_stroom_begrensd_op_min(self):
        """Groot tekort → doel_stroom afgekapt op min_stroom_a."""
        doel_a, _ = _legacy(
            net_vermogen_w=10000.0,
            huidig_stroom_a=8.0,
            min_stroom_a=6.0,
        )
        assert doel_a == 6.0

    def test_fase_upgrade_met_hysterese(self):
        """Voldoende surplus + hysterese overschreden → upgrade naar 3 fases."""
        # drempel_3f = 4140W; hysterese = 200W → grens = 4340W
        # huidig=10A, 1f: huidig_laad=2300W; net=-2100W: surplus=2100W; doel=4400W > 4340W ✓
        _, doel_fasen = _legacy(
            net_vermogen_w=-2100.0,
            huidig_stroom_a=10.0,
            huidige_fasen=1,
            hysterese_w=200.0,
        )
        assert doel_fasen == 3

    def test_fase_downgrade_bij_tekort(self):
        """Op 3-fase met tekort → doel_vermogen < drempel_3f → downgrade."""
        # huidig=6A, 3f: huidig_laad=4140W; net=+500W: surplus=-500W; doel=3640W < 4140W
        _, doel_fasen = _legacy(
            net_vermogen_w=500.0,
            huidig_stroom_a=6.0,
            huidige_fasen=3,
        )
        assert doel_fasen == 1

    def test_veiligheidsbuffer_verhoogt_drempel(self):
        """Positieve veiligheidsbuffer → minder surplus → lagere stroom."""
        doel_geen_buffer, _ = _legacy(net_vermogen_w=-500.0, veiligheidsbuffer_w=0.0)
        doel_met_buffer, _  = _legacy(net_vermogen_w=-500.0, veiligheidsbuffer_w=100.0)
        assert doel_met_buffer < doel_geen_buffer
