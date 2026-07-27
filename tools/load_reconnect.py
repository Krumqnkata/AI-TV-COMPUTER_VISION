"""Deterministic in-process WebSocket targeting and reconnect load baseline."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from web.connections import ConnectionManager  # noqa: E402


@dataclass(eq=False)
class SimulatedWebSocket:
    messages: list[str] = field(default_factory=list)
    fail_sends: bool = False

    async def send_text(self, message: str) -> None:
        if self.fail_sends:
            raise ConnectionError("simulated_stale_connection")
        self.messages.append(message)


async def run_load_scenario(
    device_count: int,
    reconnect_rounds: int,
) -> dict[str, float | int]:
    """Exercise unique-device registration, exact targeting and reconnect churn."""
    if not 1 <= int(device_count) <= 10_000:
        raise ValueError("device_count must be between 1 and 10000")
    if not 0 <= int(reconnect_rounds) <= 100:
        raise ValueError("reconnect_rounds must be between 0 and 100")

    manager = ConnectionManager()
    sockets: list[SimulatedWebSocket] = []
    started = time.perf_counter()

    async def register(index: int) -> SimulatedWebSocket:
        socket = SimulatedWebSocket()
        await manager.register(
            socket,  # type: ignore[arg-type]
            screen_id=f"screen-{index}",
            zone_id=f"zone-{index % 10}",
            device_id=f"device-{index}",
            profile="screen" if index % 2 else "kiosk",
        )
        return socket

    sockets = list(await asyncio.gather(*(
        register(index) for index in range(device_count)
    )))
    if manager.count() != device_count:
        raise AssertionError("initial connection count mismatch")

    target_index = device_count // 2
    delivered = await manager.send_to_device(
        {"type": "load_probe", "data": {"sequence": 1}},
        f"device-{target_index}",
    )
    if delivered != 1 or len(sockets[target_index].messages) != 1:
        raise AssertionError("exact device targeting failed")

    for _round in range(reconnect_rounds):
        old_sockets = sockets
        await asyncio.gather(*(manager.disconnect(socket) for socket in old_sockets))
        sockets = list(await asyncio.gather(*(
            register(index) for index in range(device_count)
        )))
        if manager.count() != device_count:
            raise AssertionError("reconnect count mismatch")

    stale = sockets[0]
    stale.fail_sends = True
    broadcast_delivered = await manager.broadcast_system({
        "type": "load_broadcast",
        "data": {},
    })
    if broadcast_delivered != device_count - 1:
        raise AssertionError("stale connection cleanup delivery mismatch")
    if manager.count() != device_count - 1:
        raise AssertionError("stale connection was not removed")

    duration = time.perf_counter() - started
    operations = device_count * (reconnect_rounds + 1)
    return {
        "devices": device_count,
        "reconnect_rounds": reconnect_rounds,
        "registrations": operations,
        "final_connections": manager.count(),
        "duration_seconds": round(duration, 6),
        "registrations_per_second": (
            round(operations / duration, 2) if duration else 0
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the School AI in-process WebSocket reconnect baseline.",
    )
    parser.add_argument("--devices", type=int, default=100)
    parser.add_argument("--reconnect-rounds", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = asyncio.run(
        run_load_scenario(args.devices, args.reconnect_rounds),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
