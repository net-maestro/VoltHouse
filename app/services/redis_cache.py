import json
from typing import Any, Dict, Optional
import redis.asyncio as redis

from app.config import settings


class RedisCache:
    def __init__(self) -> None:
        self._client: Optional[redis.Redis] = None

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._client

    async def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        raw = await self.client.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set_json(self, key: str, value: Dict[str, Any], ttl: int = 360) -> None:
        await self.client.set(key, json.dumps(value, default=str), ex=ttl)

    async def get_house_status(self, station_id: str) -> Optional[Dict[str, Any]]:
        return await self.get_json(f"house:status:{station_id}")

    async def set_house_status(
        self, station_id: str, data: Dict[str, Any], ttl: int = 600
    ) -> None:
        await self.set_json(f"house:status:{station_id}", data, ttl=ttl)

    async def get_monitor_state(self, house_id: int) -> Optional[Dict[str, Any]]:
        return await self.get_json(f"house:monitor_state:{house_id}")

    async def set_monitor_state(self, house_id: int, state: Dict[str, Any]) -> None:
        # Persist monitor state for 7 days
        await self.set_json(f"house:monitor_state:{house_id}", state, ttl=604800)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


redis_cache = RedisCache()
