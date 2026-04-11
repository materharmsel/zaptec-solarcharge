"""
Zaptec API client.

Beheert authenticatie (OAuth2 ROPC), het uitlezen van de laderstatus
en het aanpassen van het laadvermogen via de Zaptec REST API.
"""

import logging
import time
import requests

logger = logging.getLogger(__name__)

# Zaptec API adressen
TOKEN_URL = "https://api.zaptec.com/oauth/token"
BASE_URL  = "https://api.zaptec.com"

# Observation IDs uit de Zaptec state-reference
OBS_CHARGER_OPERATION_MODE = 710  # 1=niets, 2=auto aangesloten+wacht, 3=laadt, 5=klaar
OBS_SET_PHASES              = 519  # 4=3-fase, alles anders=1-fase
OBS_CHARGE_CURRENT_SET      = 708  # Stroomlimiet die Zaptec als actief heeft bevestigd (A)
OBS_CURRENT_PHASE1          = 507  # Gemeten stroom op fase 1 (A)
OBS_CURRENT_PHASE2          = 508  # Gemeten stroom op fase 2 (A)
OBS_CURRENT_PHASE3          = 509  # Gemeten stroom op fase 3 (A)


class ZaptecError(Exception):
    """Fout bij communicatie met de Zaptec API."""
    pass


class ZaptecClient:
    """Client voor de Zaptec Cloud API."""

    def __init__(self, username: str, password: str):
        self._username = username
        self._password = password
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._session = requests.Session()

    # ─── Authenticatie ────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        """
        Haalt een nieuw OAuth2 Bearer token op van Zaptec.

        Raises:
            ZaptecError: als het token-verzoek mislukt.
        """
        logger.debug("Zaptec: nieuw token ophalen")
        try:
            response = requests.post(
                TOKEN_URL,
                data={
                    "grant_type": "password",
                    "username": self._username,
                    "password": self._password,
                    "scope": "openid",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
        except requests.exceptions.RequestException as e:
            raise ZaptecError(f"Zaptec token-aanvraag mislukt (verbindingsfout): {e}")

        if not response.ok:
            raise ZaptecError(
                f"Zaptec login mislukt: HTTP {response.status_code}. "
                f"Controleer ZAPTEC_USERNAME en ZAPTEC_PASSWORD in config/.env. "
                f"Antwoord: {response.text[:300]}"
            )

        data = response.json()
        token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)

        if not token:
            raise ZaptecError(f"Zaptec stuurde geen access_token: {data}")

        logger.debug("Zaptec: token geldig voor %d seconden", expires_in)
        return token, float(expires_in)

    def _ensure_token(self) -> None:
        """
        Controleert of het huidige token nog geldig is.
        Vernieuwt het token als het verlopen is of binnen 60 seconden verloopt.
        """
        if self._token is None or time.time() >= self._token_expires_at - 60:
            token, expires_in = self._get_token()
            self._token = token
            self._token_expires_at = time.time() + expires_in

    def _auth_headers(self) -> dict:
        """Retourneert de Authorization-header met een geldig token."""
        self._ensure_token()
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str) -> dict:
        """Hulpfunctie voor GET-verzoeken naar de Zaptec API."""
        url = f"{BASE_URL}{path}"
        logger.debug("Zaptec GET %s", path)
        try:
            response = self._session.get(url, headers=self._auth_headers(), timeout=15)
        except requests.exceptions.RequestException as e:
            raise ZaptecError(f"Zaptec verbindingsfout bij GET {path}: {e}")

        if response.status_code == 429:
            raise ZaptecError("Zaptec rate-limit bereikt (HTTP 429). Probeer later opnieuw.")
        if response.status_code == 401:
            # Token is onverwacht verlopen — forceer vernieuwing bij volgende aanroep
            self._token = None
            raise ZaptecError("Zaptec: niet geautoriseerd (HTTP 401). Token wordt vernieuwd.")
        if response.status_code == 404:
            raise ZaptecError(
                f"Zaptec: resource niet gevonden (HTTP 404) op {path}. "
                f"Controleer installation_id en charger_id in config.yaml."
            )
        if not response.ok:
            raise ZaptecError(
                f"Zaptec API fout bij GET {path}: HTTP {response.status_code} — {response.text[:200]}"
            )

        return response.json()

    def _post(self, path: str, body: dict) -> None:
        """Hulpfunctie voor POST-verzoeken naar de Zaptec API."""
        url = f"{BASE_URL}{path}"
        logger.debug("Zaptec POST %s — body: %s", path, body)
        try:
            response = self._session.post(
                url, json=body, headers=self._auth_headers(), timeout=15
            )
        except requests.exceptions.RequestException as e:
            raise ZaptecError(f"Zaptec verbindingsfout bij POST {path}: {e}")

        if response.status_code == 429:
            raise ZaptecError("Zaptec rate-limit bereikt (HTTP 429). Probeer later opnieuw.")
        if response.status_code == 401:
            self._token = None
            raise ZaptecError("Zaptec: niet geautoriseerd (HTTP 401). Token wordt vernieuwd.")
        if response.status_code == 404:
            raise ZaptecError(
                f"Zaptec: resource niet gevonden (HTTP 404) op {path}. "
                f"Controleer installation_id in config.yaml."
            )
        if not response.ok:
            raise ZaptecError(
                f"Zaptec API fout bij POST {path}: HTTP {response.status_code} — {response.text[:200]}"
            )

    # ─── Laderstatus ──────────────────────────────────────────────────────────

    def get_charger_state(self, charger_id: str) -> dict:
        """
        Haalt de volledige staat van de lader op als een dict.

        Returns:
            dict van {stateId (int): valueAsString (str)}
            Voorbeeld: {710: "3", 519: "4", ...}

        Raises:
            ZaptecError: als de API niet bereikbaar is.
        """
        data = self._get(f"/api/chargers/{charger_id}/state")
        observations = {}
        for obs in data:
            try:
                state_id = int(obs.get("stateId", obs.get("StateId", 0)))
                value = str(obs.get("valueAsString", obs.get("ValueAsString", "")))
                observations[state_id] = value
            except (ValueError, TypeError):
                continue
        logger.debug("Zaptec staat opgehaald: %d observations", len(observations))
        return observations

    def get_charger_operation_mode(self, charger_id: str) -> int:
        """
        Retourneert de huidige operatiemodus van de lader (observation 710).

        Waarden:
            1 = Niets aangesloten (idle)
            2 = Auto aangesloten, wacht op laadstart
            3 = Aan het laden
            5 = Laden voltooid (auto nog aangesloten)

        Returns:
            int: operatiemodus, of 1 als de waarde niet beschikbaar is.
        """
        observations = self.get_charger_state(charger_id)
        raw = observations.get(OBS_CHARGER_OPERATION_MODE)
        if raw is None:
            logger.warning(
                "Zaptec observation %d (ChargerOperationMode) niet gevonden — "
                "neem aan dat er geen auto is aangesloten.",
                OBS_CHARGER_OPERATION_MODE,
            )
            return 1
        try:
            mode = int(raw)
            logger.debug("Zaptec ChargerOperationMode: %d", mode)
            return mode
        except ValueError:
            logger.warning("Zaptec: ongeldige waarde voor ChargerOperationMode: %r", raw)
            return 1

    def get_current_phases(self, charger_id: str) -> int:
        """
        Retourneert het huidige aantal fases van de lader (observation 519).

        Observation 519 (SetPhases) waarden:
            4 = TN 3-fase (alle drie fasen actief)
            1,2,3,5,6,8 = 1-fase op een specifieke fase

        Returns:
            int: 3 als de lader op 3-fase laadt, anders 1.
        """
        observations = self.get_charger_state(charger_id)
        raw = observations.get(OBS_SET_PHASES)
        if raw is None:
            logger.warning(
                "Zaptec observation %d (SetPhases) niet gevonden — neem aan 1-fase.",
                OBS_SET_PHASES,
            )
            return 1
        try:
            value = int(raw)
            fasen = 3 if value == 4 else 1
            logger.debug("Zaptec SetPhases: %d → %d fase(n)", value, fasen)
            return fasen
        except ValueError:
            logger.warning("Zaptec: ongeldige waarde voor SetPhases: %r — neem aan 1-fase.", raw)
            return 1

    def is_car_connected(self, charger_id: str) -> bool:
        """
        Retourneert True als er een auto aangesloten is (mode 2, 3 of 5).

        Mode 1 (idle) = geen auto. In alle andere gevallen is de auto fysiek
        aangesloten, ook als het laden afgerond is (mode 5).
        """
        mode = self.get_charger_operation_mode(charger_id)
        connected = mode in (2, 3, 5)
        logger.debug("Zaptec auto aangesloten: %s (mode %d)", connected, mode)
        return connected

    def get_installation_mode(self, installation_id: str) -> int:
        """
        Retourneert de actieve laadmodus van de installatie.

        Waarden:
            0 = Standaard laden (Manual) — dynamisch vermogensbeheer via API mogelijk
            1 = Gepland laden (Schedule) — tijdschema actief in Zaptec portaal
            2 = Automatisch opladen (Auto Power Management) — Zaptec regelt zelf

        Returns:
            int: laadmodus (0/1/2), of 0 als de waarde niet beschikbaar is.

        Raises:
            ZaptecError: als de API niet bereikbaar is.
        """
        data = self._get(f"/api/installation/{installation_id}")
        mode = data.get("availableCurrentMode", 0)
        try:
            result = int(mode)
            logger.debug("Zaptec installatie laadmodus: %d", result)
            return result
        except (ValueError, TypeError):
            logger.warning(
                "Zaptec: ongeldige waarde voor availableCurrentMode: %r — neem 0 aan.", mode
            )
            return 0

    def get_installation_schakelingen(self, installation_id: str) -> int | None:
        """
        Retourneert het maximum aantal fasewijzigingen per sessie (propertySessionMaxStopCount).

        Dit is de instelling "Schakelingen toegestaan voor vergrendeling op 1-fase"
        in het Zaptec portaal. Standaard is dit 5, maximaal 20.
        De waarde is alleen leesbaar via de API — aanpassen kan alleen via het portaal.

        Returns:
            int: max. schakelingen, of None als de waarde niet beschikbaar is.
        """
        data = self._get(f"/api/installation/{installation_id}")
        waarde = data.get("propertySessionMaxStopCount")
        if waarde is None:
            return None
        try:
            result = int(waarde)
            logger.debug("Zaptec installatie max schakelingen: %d", result)
            return result
        except (ValueError, TypeError):
            logger.warning(
                "Zaptec: ongeldige waarde voor propertySessionMaxStopCount: %r", waarde
            )
            return None

    def get_charger_details(self, charger_id: str) -> dict:
        """
        Haalt de volledige lader-details op.

        Returns:
            dict met lader-eigenschappen (o.a. maxChargePhases).

        Raises:
            ZaptecError: als de API niet bereikbaar is.
        """
        return self._get(f"/api/chargers/{charger_id}")

    def get_charger_max_phases(self, charger_id: str) -> int:
        """
        Retourneert het maximale aantal fases van de lader.

        Probeert achtereenvolgens:
          1. maxChargePhases / MaxChargePhases in GET /api/chargers/{id}
          2. Dezelfde veldnamen in GET /api/circuits/{CircuitId}

        Als het veld nergens gevonden wordt, wordt 3 teruggegeven (veilige
        default) en worden de beschikbare veldnamen gelogd op WARNING.

        Returns:
            int: 1 of 3. Fallback naar 3 als de waarde niet beschikbaar is.
        """
        FASE_VELDNAMEN = ("maxChargePhases", "MaxChargePhases",
                          "maxChargingPhases", "MaxChargingPhases")

        # Stap 1: probeer charger-details
        data = self.get_charger_details(charger_id)
        for naam in FASE_VELDNAMEN:
            waarde = data.get(naam)
            if waarde is not None:
                try:
                    result = int(waarde)
                    logger.debug("Zaptec lader %s: %d (uit charger-details)", naam, result)
                    return result
                except (ValueError, TypeError):
                    logger.warning("Zaptec: ongeldige waarde voor %s: %r — neem 3 aan.", naam, waarde)
                    return 3

        # Stap 2: probeer circuit-details via CircuitId uit de charger-response
        circuit_id = data.get("CircuitId")
        if circuit_id:
            try:
                circuit = self._get(f"/api/circuits/{circuit_id}")
                for naam in FASE_VELDNAMEN:
                    waarde = circuit.get(naam)
                    if waarde is not None:
                        try:
                            result = int(waarde)
                            logger.debug("Zaptec lader %s: %d (uit circuit-details)", naam, result)
                            return result
                        except (ValueError, TypeError):
                            logger.warning(
                                "Zaptec: ongeldige waarde voor %s: %r — neem 3 aan.", naam, waarde
                            )
                            return 3
                # Veld ook niet in circuit gevonden — log circuit-velden voor diagnose
                logger.warning(
                    "Zaptec: maxChargePhases niet gevonden in charger- of circuit-details — neem 3 aan. "
                    "Circuit-velden: %s",
                    list(circuit.keys()),
                )
            except ZaptecError as e:
                logger.warning(
                    "Zaptec: circuit-details niet ophaalbaar (CircuitId=%s): %s — neem 3 aan.",
                    circuit_id, e,
                )
        else:
            logger.warning(
                "Zaptec: maxChargePhases niet gevonden en geen CircuitId beschikbaar — neem 3 aan. "
                "Charger-velden: %s",
                list(data.keys()),
            )

        return 3

    # ─── Laadvermogen aanpassen ───────────────────────────────────────────────

    def set_installation_settings(
        self,
        installation_id: str,
        available_current: float,
        drie_naar_een_fase_stroom: float | None = None,
    ) -> None:
        """
        Past de installatie-instellingen aan om het laadvermogen te regelen.

        Args:
            installation_id:
                Het Zaptec installatie-ID uit config.yaml.
            available_current:
                Beschikbare laadstroom in Ampere voor alle fases.
                Stel in op -1.0 om de Zaptec-standaard te herstellen.
            drie_naar_een_fase_stroom:
                Drempelwaarde voor automatische fasewisseling:
                  0   = altijd 3-fase (geen terugval naar 1-fase)
                  32  = altijd 1-fase
                  None = deze instelling niet aanpassen

        Raises:
            ZaptecError: als het API-verzoek mislukt.
        """
        body: dict = {"availableCurrent": available_current}

        if drie_naar_een_fase_stroom is not None:
            body["threeToOnePhaseSwitchCurrent"] = drie_naar_een_fase_stroom

        logger.debug(
            "Zaptec installatie update: stroom=%.1fA, fasedrempel=%s",
            available_current,
            drie_naar_een_fase_stroom,
        )
        self._post(f"/api/installation/{installation_id}/update", body)
        logger.info(
            "Zaptec bijgewerkt: availableCurrent=%.1fA%s",
            available_current,
            f", threeToOnePhaseSwitchCurrent={drie_naar_een_fase_stroom}"
            if drie_naar_een_fase_stroom is not None
            else "",
        )
