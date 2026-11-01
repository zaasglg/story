"""User-triggered address lookup, cached and globally throttled via Redis."""

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis

from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.config import Settings
from backend.core.errors import ApiError
from backend.identity.dependencies import CurrentUser

router = APIRouter(prefix="/admin/maps", tags=["maps"])
Actor = Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))]


class AddressGeocoder:
    def __init__(self, settings: Settings, redis: Redis | None = None) -> None:
        self.settings = settings
        self.redis = redis
        self.client = httpx.AsyncClient(
            base_url=settings.geocoding_url.rstrip("/"),
            timeout=10,
            headers={"User-Agent": "ALASH-M1917/1.0 (kz.alash.m1917)",
                     "Accept-Language": "ru"},
        )
        self.cache: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()
        self.last_request = 0.0
        self.lock = asyncio.Lock()

    async def lookup(self, path: str, params: dict) -> list[dict]:
        key = "maps:cache:" + hashlib.sha256(
            json.dumps([self.settings.geocoding_url, path, params], sort_keys=True).encode()
        ).hexdigest()
        try:
            if self.redis is not None:
                cached = await self.redis.get(key)
                if cached is not None:
                    return json.loads(cached)
            elif key in self.cache and self.cache[key][0] > time.monotonic():
                return self.cache[key][1]
            async with self.lock:
                if self.redis is not None:
                    allowed = await self.redis.set("maps:request-slot", "1", nx=True, px=1100)
                    if not allowed:
                        raise ApiError(429, "geocoding_busy", "Retry address lookup shortly")
                else:
                    delay = 1.1 - (time.monotonic() - self.last_request)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    self.last_request = time.monotonic()
                response = await self.client.get(path, params=params)
                response.raise_for_status()
                places = []
                for feature in response.json().get("features", []):
                    props = feature.get("properties", {})
                    lon, lat = feature["geometry"]["coordinates"][:2]
                    if not (-90 <= float(lat) <= 90 and -180 <= float(lon) <= 180):
                        continue
                    street = " ".join(str(props[k]) for k in ("street", "housenumber")
                                      if props.get(k))
                    parts = [props.get("city") or props.get("town") or props.get("village"),
                             street, props.get("name"), props.get("state"), props.get("country")]
                    address = ", ".join(dict.fromkeys(str(part) for part in parts if part))
                    if address:
                        places.append({"address": address[:2000],
                                       "latitude": float(lat), "longitude": float(lon)})
                if self.redis is not None:
                    await self.redis.set(key, json.dumps(places), ex=86400)
                else:
                    self.cache[key] = (time.monotonic() + 86400, places)
                    if len(self.cache) > 256:
                        self.cache.popitem(last=False)
                return places
        except ApiError:
            raise
        except Exception as exc:
            raise ApiError(503, "geocoding_unavailable", "Address lookup is unavailable") from exc


def geocoder(request: Request) -> AddressGeocoder:
    return request.app.state.geocoder


@router.get("/config")
async def map_config(request: Request, _: Actor) -> dict:
    settings = request.app.state.settings
    return {"tile_url": settings.map_tile_url, "attribution": settings.map_attribution,
            "attribution_url": settings.map_attribution_url}


@router.get("/search")
async def search_address(
    _: Actor,
    service: Annotated[AddressGeocoder, Depends(geocoder)],
    q: Annotated[str, Query(min_length=2, max_length=300)],
) -> list[dict]:
    if len(q.strip()) < 2:
        raise ApiError(422, "invalid_address_query", "Enter an address")
    return await service.lookup("/api/", {"q": q.strip(), "limit": 5})


@router.get("/reverse")
async def reverse_address(
    _: Actor,
    service: Annotated[AddressGeocoder, Depends(geocoder)],
    latitude: Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)],
    longitude: Annotated[float, Query(ge=-180, le=180, allow_inf_nan=False)],
) -> list[dict]:
    return await service.lookup("/reverse", {"lat": latitude, "lon": longitude, "limit": 1})
