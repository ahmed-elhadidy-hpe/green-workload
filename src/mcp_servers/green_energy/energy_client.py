import httpx
import asyncio
import hashlib
import random
import time
from datetime import datetime, timedelta
from tenacity import retry, stop_after_attempt, wait_exponential

MOCK_CACHE: dict = {}
REAL_CACHE: dict = {}
CACHE_TTL_SECONDS = 300


class MockEnergyClient:
    """Returns deterministic-ish mock data per zone with small variation."""

    def get_zone_energy(self, zone_code: str) -> dict:
        seed = int(hashlib.md5(zone_code.encode()).hexdigest()[:8], 16) % 100
        base_renewable = 30 + seed
        base_renewable = max(20, min(90, base_renewable))
        variation = random.uniform(-5, 5)
        renewable_pct = round(max(0, min(100, base_renewable + variation)), 2)
        carbon = round(max(10, 500 - renewable_pct * 4 + random.uniform(-10, 10)), 3)

        solar = round(max(0, renewable_pct * 0.4 + random.uniform(-5, 5)), 2)
        wind = round(max(0, renewable_pct * 0.4 + random.uniform(-5, 5)), 2)
        hydro = round(max(0, renewable_pct - solar - wind), 2)
        fossil = round(100 - renewable_pct, 2)

        return {
            "zone_code": zone_code,
            "carbon_intensity": carbon,
            "renewable_percentage": renewable_pct,
            "energy_sources": {
                "solar": solar,
                "wind": wind,
                "hydro": hydro,
                "coal": fossil * 0.5,
                "gas": fossil * 0.5,
            },
            "is_green": renewable_pct >= 50,
            "data_quality": "mock",
            "timestamp": datetime.utcnow().isoformat(),
        }


class ElectricityMapsClient:
    BASE_URL = "https://api.electricitymap.org/v3"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=10.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def get_zone_energy(self, zone_code: str) -> dict:
        cached = REAL_CACHE.get(zone_code)
        if cached and cached["expires_at"] > time.time():
            return cached["data"]

        response = await self.client.get(
            f"{self.BASE_URL}/carbon-intensity/latest",
            params={"zone": zone_code},
            headers={"auth-token": self.api_key},
        )
        response.raise_for_status()
        data = response.json()

        result = {
            "zone_code": zone_code,
            "carbon_intensity": data.get("carbonIntensity", 0),
            "renewable_percentage": data.get("fossilFreePercentage", 0),
            "energy_sources": data.get("powerConsumptionBreakdown", {}),
            "is_green": data.get("fossilFreePercentage", 0) >= 50,
            "data_quality": "live",
            "timestamp": datetime.utcnow().isoformat(),
        }

        REAL_CACHE[zone_code] = {"data": result, "expires_at": time.time() + CACHE_TTL_SECONDS}
        return result


class WattTimeClient:
    BASE_URL = "https://api.watttime.org/v3"

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self._token: str | None = None
        self._token_expires: float = 0
        self.client = httpx.AsyncClient(timeout=10.0)

    async def _authenticate(self) -> None:
        if self._token and self._token_expires > time.time():
            return
        resp = await self.client.get(
            f"{self.BASE_URL}/login",
            auth=(self.username, self.password),
        )
        resp.raise_for_status()
        self._token = resp.json()["token"]
        self._token_expires = time.time() + 1800

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def get_zone_energy(self, ba: str) -> dict:
        await self._authenticate()
        resp = await self.client.get(
            f"{self.BASE_URL}/signal-index",
            params={"ba": ba, "signal_type": "co2_moer"},
            headers={"Authorization": f"Bearer {self._token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        moer = data.get("data", [{}])[0].get("value", 500)
        renewable_pct = max(0, min(100, round((1000 - moer) / 10, 2)))
        return {
            "zone_code": ba,
            "carbon_intensity": moer,
            "renewable_percentage": renewable_pct,
            "energy_sources": {"unknown": 100},
            "is_green": renewable_pct >= 50,
            "data_quality": "live",
            "timestamp": datetime.utcnow().isoformat(),
        }


def get_energy_client(
    electricity_maps_key: str = "",
    watttime_username: str = "",
    watttime_password: str = "",
):
    """Return appropriate client based on available credentials. Falls back to mock."""
    if electricity_maps_key:
        return ElectricityMapsClient(electricity_maps_key)
    elif watttime_username and watttime_password:
        return WattTimeClient(watttime_username, watttime_password)
    else:
        return MockEnergyClient()
