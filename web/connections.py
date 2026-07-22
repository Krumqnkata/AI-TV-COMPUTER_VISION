from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from fastapi import WebSocket


@dataclass(eq=False)
class ScreenConnection:
    websocket: WebSocket
    screen_id: str
    zone_id: str


class ConnectionManager:
    def __init__(self):
        self._connections: dict[WebSocket, ScreenConnection] = {}
        self._lock = asyncio.Lock()

    async def register(self, websocket: WebSocket, screen_id: str, zone_id: str) -> ScreenConnection:
        connection = ScreenConnection(websocket=websocket, screen_id=screen_id, zone_id=zone_id)
        async with self._lock:
            self._connections[websocket] = connection
        return connection

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.pop(websocket, None)

    async def _send(self, targets: list[ScreenConnection], payload: dict) -> int:
        message = json.dumps(payload, ensure_ascii=False)
        delivered = 0
        stale: list[WebSocket] = []
        for connection in targets:
            try:
                await connection.websocket.send_text(message)
                delivered += 1
            except Exception:
                stale.append(connection.websocket)
        if stale:
            async with self._lock:
                for websocket in stale:
                    self._connections.pop(websocket, None)
        return delivered

    async def send_to_screen_or_zone(
        self,
        payload: dict,
        screen_id: str | None,
        zone_id: str,
    ) -> int:
        async with self._lock:
            connections = list(self._connections.values())
        targets = [c for c in connections if screen_id and c.screen_id == screen_id]
        if not targets:
            targets = [c for c in connections if c.zone_id == zone_id]
        return await self._send(targets, payload)

    async def send_to_zone(self, payload: dict, zone_id: str) -> int:
        async with self._lock:
            targets = [c for c in self._connections.values() if c.zone_id == zone_id]
        return await self._send(targets, payload)

    async def broadcast_system(self, payload: dict) -> int:
        async with self._lock:
            targets = list(self._connections.values())
        return await self._send(targets, payload)

    def count(self) -> int:
        return len(self._connections)


connection_manager = ConnectionManager()
