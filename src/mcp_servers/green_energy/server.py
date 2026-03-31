import asyncio
import random
import structlog
from datetime import datetime, timedelta
from mcp.server.fastmcp import FastMCP
from src.mcp_servers.green_energy.energy_client import get_energy_client, MockEnergyClient
from src.database.repository import GreenWorkloadRepository
from config.settings import settings

log = structlog.get_logger()
mcp = FastMCP("green-energy-mcp")
repo = GreenWorkloadRepository()
client = get_energy_client(
    settings.ELECTRICITY_MAPS_API_KEY,
    settings.WATTTIME_USERNAME,
    settings.WATTTIME_PASSWORD,
)


async def _fetch_energy(zone_code: str) -> dict:
    """Fetch energy data for a zone, handling both sync and async clients."""
    if isinstance(client, MockEnergyClient):
        return await asyncio.to_thread(client.get_zone_energy, zone_code)
    else:
        return await client.get_zone_energy(zone_code)


@mcp.tool()
async def get_zone_energy_status(zone_id: str) -> dict:
    """Get current energy status for a zone by DB UUID or electricitymap zone code."""
    try:
        zones = repo.get_all_zones_with_energy()
        zone = next(
            (z for z in zones if z["zone_id"] == zone_id or z.get("electricitymap_zone") == zone_id),
            None,
        )
        if not zone:
            return {"error": f"Zone not found: {zone_id}"}
        zone_code = zone.get("electricitymap_zone") or zone.get("watttime_ba") or zone["zone_name"]
        energy = await _fetch_energy(zone_code)
        repo.upsert_energy_reading(zone["zone_id"], energy)
        return {**zone, **energy}
    except Exception as e:
        log.error("get_zone_energy_status failed", error=str(e))
        return {"error": str(e)}


@mcp.tool()
async def get_all_zones_energy_status() -> dict:
    """Get current energy status for all configured zones."""
    try:
        zones = repo.get_all_zones_with_energy()
        results = []
        for zone in zones:
            zone_code = zone.get("electricitymap_zone") or zone.get("watttime_ba") or zone["zone_name"]
            energy = await _fetch_energy(zone_code)
            repo.upsert_energy_reading(zone["zone_id"], energy)
            results.append({**zone, **energy})
        return {"zones": results, "count": len(results), "timestamp": datetime.utcnow().isoformat()}
    except Exception as e:
        log.error("get_all_zones_energy_status failed", error=str(e))
        return {"error": str(e)}


@mcp.tool()
async def get_greenest_zones(min_renewable_pct: float = 50.0) -> dict:
    """Return zones ranked by greenness (highest renewable %)."""
    try:
        zones = repo.get_all_zones_with_energy()
        results = []
        for zone in zones:
            zone_code = zone.get("electricitymap_zone") or zone.get("watttime_ba") or zone["zone_name"]
            energy = await _fetch_energy(zone_code)
            if energy.get("renewable_percentage", 0) >= min_renewable_pct:
                results.append({**zone, **energy})
        results.sort(key=lambda x: x.get("renewable_percentage", 0), reverse=True)
        return {"green_zones": results, "count": len(results), "min_renewable_pct": min_renewable_pct}
    except Exception as e:
        log.error("get_greenest_zones failed", error=str(e))
        return {"error": str(e)}


@mcp.tool()
async def get_zone_energy_forecast(zone_id: str, hours_ahead: int = 2) -> dict:
    """Get short-term forecast for a zone by generating mock future readings."""
    try:
        zones = repo.get_all_zones_with_energy()
        zone = next(
            (z for z in zones if z["zone_id"] == zone_id or z.get("electricitymap_zone") == zone_id),
            None,
        )
        if not zone:
            return {"error": f"Zone not found: {zone_id}"}
        zone_code = zone.get("electricitymap_zone") or zone.get("watttime_ba") or zone["zone_name"]
        current = await _fetch_energy(zone_code)

        forecast = []
        for h in range(1, hours_ahead + 1):
            ts = (datetime.utcnow() + timedelta(hours=h)).isoformat()
            variation = random.uniform(-10, 10)
            renewable = max(0, min(100, current["renewable_percentage"] + variation))
            forecast.append({
                "timestamp": ts,
                "renewable_percentage": round(renewable, 2),
                "carbon_intensity": round(max(10, current["carbon_intensity"] + variation * 2), 3),
                "is_green": renewable >= 50,
                "forecast_type": "mock",
            })
        return {"zone_id": zone_id, "current": current, "forecast": forecast}
    except Exception as e:
        log.error("get_zone_energy_forecast failed", error=str(e))
        return {"error": str(e)}


@mcp.tool()
async def backfill_energy_history(zone_ids: list[str], lookback_hours: int = 24) -> dict:
    """Backfill historical energy readings at hourly intervals."""
    try:
        results = {}
        for zone_id in zone_ids:
            zones = repo.get_all_zones_with_energy()
            zone = next((z for z in zones if z["zone_id"] == zone_id), None)
            if not zone:
                results[zone_id] = {"error": "Zone not found"}
                continue
            zone_code = zone.get("electricitymap_zone") or zone["zone_name"]
            inserted = 0
            base = await _fetch_energy(zone_code)
            for h in range(lookback_hours, 0, -1):
                ts = datetime.utcnow() - timedelta(hours=h)
                variation = random.uniform(-15, 15)
                renewable = max(0, min(100, base["renewable_percentage"] + variation))
                data = {
                    "zone_code": zone_code,
                    "carbon_intensity": round(max(10, base["carbon_intensity"] + variation * 2), 3),
                    "renewable_percentage": round(renewable, 2),
                    "energy_sources": base["energy_sources"],
                    "is_green": renewable >= 50,
                    "data_quality": "backfill",
                    "timestamp": ts.isoformat(),
                }
                repo.upsert_energy_reading(zone_id, data)
                inserted += 1
            results[zone_id] = {"inserted": inserted}
        return {"results": results, "lookback_hours": lookback_hours}
    except Exception as e:
        log.error("backfill_energy_history failed", error=str(e))
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run()
