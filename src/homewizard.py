"""
HomeWizard P1 Meter API client (v2).

Leest het actuele netvermogen van de HomeWizard P1 Meter via de lokale HTTPS API.
De v2 API vereist een Bearer token, zie README.md voor het ophalen hiervan.
"""

import logging
import requests
import urllib3

# Onderdruk SSL-waarschuwingen voor het zelf-gesigneerde certificaat van de P1 Meter
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class HomeWizardError(Exception):
    """Fout bij communicatie met de HomeWizard P1 Meter."""
    pass


class HomeWizardClient:
    """Client voor de HomeWizard P1 Meter lokale API (v2)."""

    def __init__(self, ip: str, token: str):
        self._base_url = f"https://{ip}"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "X-Api-Version": "2",
        }
        self._session = requests.Session()
        self._session.verify = False  # Zelf-gesigneerd certificaat
        self._session.headers.update(self._headers)

    def get_measurement(self) -> dict:
        """
        Haalt de meest recente meting op van de P1 Meter.

        Returns:
            dict met alle meetwaarden, bijv. {"power_w": -712, "power_l1_w": -712, ...}

        Raises:
            HomeWizardError: als de API niet bereikbaar is of een fout retourneert.
        """
        url = f"{self._base_url}/api/measurement"
        logger.debug("HomeWizard GET %s", url)

        try:
            response = self._session.get(url, timeout=10)
        except requests.exceptions.Timeout:
            raise HomeWizardError(
                f"HomeWizard P1 Meter reageert niet (timeout). "
                f"Controleer of het IP-adres {self._base_url!r} klopt in config.yaml."
            )
        except requests.exceptions.ConnectionError as e:
            raise HomeWizardError(
                f"Kan HomeWizard P1 Meter niet bereiken op {self._base_url}: {e}"
            )
        except requests.exceptions.RequestException as e:
            raise HomeWizardError(f"HomeWizard verbindingsfout: {e}")

        if response.status_code == 401:
            raise HomeWizardError(
                "HomeWizard token ongeldig (HTTP 401). "
                "Haal een nieuw token op en update HOMEWIZARD_TOKEN in config/.env."
            )
        if not response.ok:
            raise HomeWizardError(
                f"HomeWizard API fout: HTTP {response.status_code} — {response.text[:200]}"
            )

        try:
            data = response.json()
        except ValueError:
            raise HomeWizardError(
                f"HomeWizard stuurde onleesbare data terug: {response.text[:200]}"
            )

        logger.debug("HomeWizard meting ontvangen: power_w=%s", data.get("power_w"))
        return data

    def get_power_watts(self) -> float:
        """
        Retourneert het actuele totale netvermogen in Watt.

        Positief = importeren van het net (meer verbruik dan productie).
        Negatief = exporteren naar het net (meer productie dan verbruik = zonneover­schot).

        Returns:
            float: netvermogen in Watt

        Raises:
            HomeWizardError: als power_w ontbreekt of de API niet bereikbaar is.
        """
        data = self.get_measurement()

        if "power_w" not in data:
            raise HomeWizardError(
                "HomeWizard stuurt geen 'power_w' waarde. "
                "Controleer of de slimme meter verbonden is met de P1 Meter."
            )

        return float(data["power_w"])
