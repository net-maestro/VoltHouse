import hashlib
import logging
import math
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import httpx

from app.config import settings
from app.services.redis_cache import redis_cache

logger = logging.getLogger(__name__)


@dataclass
class StationTelemetry:
    station_id: str
    station_name: str
    grid_connected: bool
    v_l1: float
    v_l2: float
    v_l3: float
    soc: float  # % (0 - 100)
    battery_power_kw: float  # positive = charging, negative = discharging
    load_power_kw: float  # house consumption in kW
    grid_power_kw: float
    inverter_fault: Optional[str] = None
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StationTelemetry":
        return cls(**data)

    @property
    def is_voltage_normal(self) -> bool:
        if not self.grid_connected:
            return True
        for v in (self.v_l1, self.v_l2, self.v_l3):
            if v < settings.VOLTAGE_MIN or v > settings.VOLTAGE_MAX:
                return False
        return True

    def estimate_hours_to_critical(self, capacity_kwh: float = 50.0) -> Optional[float]:
        """Estimate remaining hours until battery reaches ELEVATOR_CRITICAL_SOC (20%)."""
        critical_soc = settings.ELEVATOR_CRITICAL_SOC
        if self.soc <= critical_soc:
            return 0.0
        # Discharge rate: either negative battery_power_kw or current load_power_kw when off-grid
        discharge_kw = abs(self.battery_power_kw) if self.battery_power_kw < -0.1 else (
            self.load_power_kw if not self.grid_connected else 0.0
        )
        if discharge_kw <= 0.05:
            return None
        usable_fraction = (self.soc - critical_soc) / 100.0
        usable_kwh = capacity_kwh * usable_fraction
        return round(usable_kwh / discharge_kw, 2)


class DeyeCloudClient:
    """
    Async client for Deye Cloud OpenAPI (https://eu1-developer.deyecloud.com).
    Supports master account token authentication, station discovery, and 3-phase telemetry.
    Includes a realistic simulation mode when DEYE_MOCK_MODE=True.
    """

    TOKEN_REDIS_KEY = "deye:master:access_token"

    def __init__(self) -> None:
        self.base_url = settings.DEYE_BASE_URL.rstrip("/")
        self.app_id = settings.DEYE_APP_ID
        self.app_secret = settings.DEYE_APP_SECRET
        self.email = settings.DEYE_EMAIL
        self.password = settings.DEYE_PASSWORD
        self.mock_mode = settings.DEYE_MOCK_MODE

    async def _get_access_token(self) -> str:
        cached = await redis_cache.client.get(self.TOKEN_REDIS_KEY)
        if cached:
            return cached

        password_sha256 = hashlib.sha256(self.password.encode("utf-8")).hexdigest()
        url = f"{self.base_url}/v1.0/account/token"
        params = {"appId": self.app_id}
        payload = {
            "appSecret": self.app_secret,
            "email": self.email,
            "password": password_sha256,
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, params=params, json=payload)
            resp.raise_for_status()
            data = resp.json()

        access_token = data.get("accessToken")
        if not access_token:
            raise RuntimeError(f"Failed to obtain Deye Cloud accessToken: {data}")

        # Cache token for 50 minutes
        await redis_cache.client.set(self.TOKEN_REDIS_KEY, access_token, ex=3000)
        return access_token

    async def get_stations(self) -> List[Dict[str, Any]]:
        """Return list of stations managed by the master account."""
        if self.mock_mode or not self.app_id:
            return [
                {"id": "deye_st_61", "name": "Будинок №61", "dtek_group": "Черга 5.1"},
                {"id": "deye_st_63", "name": "Будинок №63", "dtek_group": "Черга 3.2"},
                {"id": "deye_st_65a", "name": "Будинок №65А", "dtek_group": "Черга 1.1"},
            ]

        token = await self._get_access_token()
        url = f"{self.base_url}/v1.0/station/list"
        headers = {"Authorization": f"bearer {token}"}
        payload = {"page": 1, "size": 50}

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            body = resp.json()

        station_list = body.get("stationList", [])
        return [
            {
                "id": str(st.get("id")),
                "name": st.get("name", f"Станція {st.get('id')}"),
                "dtek_group": "Черга 5.1",
            }
            for st in station_list
        ]

    async def get_station_telemetry(
        self, station_id: str, station_name: str = "Будинок"
    ) -> StationTelemetry:
        """Fetch latest telemetry for a specific station (with mock fallback)."""
        if self.mock_mode or not self.app_id:
            return self._generate_mock_telemetry(station_id, station_name)

        token = await self._get_access_token()
        headers = {"Authorization": f"bearer {token}"}

        async with httpx.AsyncClient(timeout=20.0) as client:
            # 1. Fetch station latest summary
            latest_url = f"{self.base_url}/v1.0/station/latest"
            resp = await client.post(
                latest_url, headers=headers, json={"stationId": int(station_id)}
            )
            resp.raise_for_status()
            st_data = resp.json()

            # Parse core metrics (Deye reports power in Watts)
            soc = float(st_data.get("batterySOC") or 0.0)
            battery_power_w = float(st_data.get("batteryPower") or 0.0)
            load_power_w = float(st_data.get("consumptionPower") or 0.0)
            grid_power_w = float(st_data.get("gridPower") or 0.0)
            wire_power_w = float(st_data.get("wirePower") or grid_power_w)

            # Default 3-phase voltages if device endpoint is not available
            v_l1, v_l2, v_l3 = 228.0, 226.0, 229.0
            grid_connected = abs(wire_power_w) > 10.0 or st_data.get("gridInterconnectionType") != 0

            # 2. Try fetching inverter device parameters for exact 3-phase voltages
            try:
                dev_list_resp = await client.post(
                    f"{self.base_url}/v1.0/station/device",
                    headers=headers,
                    json={"stationIds": [int(station_id)], "page": 1, "size": 10},
                )
                if dev_list_resp.status_code == 200:
                    devs = dev_list_resp.json().get("deviceListItems", [])
                    inverter_sns = [
                        d["deviceSn"] for d in devs if d.get("deviceType") == "INVERTER"
                    ]
                    if inverter_sns:
                        dev_data_resp = await client.post(
                            f"{self.base_url}/v1.0/device/latest",
                            headers=headers,
                            json={"deviceList": inverter_sns[:1]},
                        )
                        if dev_data_resp.status_code == 200:
                            items = dev_data_resp.json().get("deviceDataList", [])
                            if items:
                                kv = {
                                    p["key"]: float(p["value"])
                                    for p in items[0].get("dataList", [])
                                    if p.get("value") is not None
                                    and str(p.get("value"))
                                    .replace(".", "", 1)
                                    .replace("-", "", 1)
                                    .isdigit()
                                }
                                v_l1 = kv.get("GridVoltageL1", kv.get("ACVoltageR", v_l1))
                                v_l2 = kv.get("GridVoltageL2", kv.get("ACVoltageS", v_l2))
                                v_l3 = kv.get("GridVoltageL3", kv.get("ACVoltageT", v_l3))
                                grid_connected = (v_l1 > 80.0 or v_l2 > 80.0 or v_l3 > 80.0)
            except Exception as exc:
                logger.warning("Could not fetch detailed inverter phase voltages: %s", exc)

            return StationTelemetry(
                station_id=str(station_id),
                station_name=station_name,
                grid_connected=grid_connected,
                v_l1=round(v_l1, 1),
                v_l2=round(v_l2, 1),
                v_l3=round(v_l3, 1),
                soc=round(soc, 1),
                battery_power_kw=round(battery_power_w / 1000.0, 2),
                load_power_kw=round(load_power_w / 1000.0, 2),
                grid_power_kw=round(grid_power_w / 1000.0, 2),
                updated_at=datetime.now().strftime("%d.%m.%Y %H:%M"),
            )

    def _generate_mock_telemetry(
        self, station_id: str, station_name: str
    ) -> StationTelemetry:
        """Deterministic time-based realistic telemetry for testing/demo."""
        now = datetime.now()
        minute_of_day = now.hour * 60 + now.minute
        wave = math.sin(minute_of_day / 180.0)

        # Simulate realistic 3-phase grid operation
        v_l1 = round(226.0 + 4.0 * wave, 1)
        v_l2 = round(223.0 + 3.5 * wave, 1)
        v_l3 = round(225.0 + 4.5 * wave, 1)
        soc = round(78.0 + 18.0 * wave, 1)
        load_kw = round(6.4 + 2.1 * abs(wave), 2)
        bat_kw = round(2.5 * wave, 2)

        return StationTelemetry(
            station_id=str(station_id),
            station_name=station_name,
            grid_connected=True,
            v_l1=v_l1,
            v_l2=v_l2,
            v_l3=v_l3,
            soc=soc,
            battery_power_kw=bat_kw,
            load_power_kw=load_kw,
            grid_power_kw=round(load_kw + max(0.0, bat_kw), 2),
            updated_at=now.strftime("%d.%m.%Y %H:%M"),
        )


deye_client = DeyeCloudClient()
