"""Smoke-test a managed device without camera or audio hardware."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import Config  # noqa: E402


def build_device_headers(device_id: str = "", device_key: str = "", legacy_key: str = "") -> dict[str, str]:
    """Build explicit authentication headers; individual credentials win."""
    if device_id and device_key:
        return {"X-Device-ID": device_id, "X-Device-Key": device_key}
    if device_id or device_key:
        raise ValueError("DEVICE_ID и DEVICE_KEY трябва да бъдат зададени заедно")
    if legacy_key:
        return {"X-Device-Key": legacy_key}
    raise ValueError("Липсват индивидуални device credentials или изричен legacy key")


class DeviceSimulator:
    def __init__(self, server_url: str, timeout: float = 10.0):
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()

    def set_credentials(self, device_id: str = "", device_key: str = "", legacy_key: str = "") -> None:
        self.http.headers.clear()
        self.http.headers.update(build_device_headers(device_id, device_key, legacy_key))

    def enroll(self, token: str, identifier: str, name: str, device_type: str) -> dict:
        response = requests.post(
            f"{self.server_url}/api/devices/enroll",
            headers={"X-Enrollment-Token": token},
            json={
                "identifier": identifier,
                "name": name,
                "device_type": device_type,
                "capabilities": ["simulator", "qr", "screen"],
                "software_version": "simulator-1.0",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        self.set_credentials(payload["device_id"], payload["device_key"])
        return payload

    def heartbeat(self) -> dict:
        response = self.http.post(
            f"{self.server_url}/api/devices/heartbeat",
            json={
                "status": "online",
                "software_version": "simulator-1.0",
                "capabilities": ["simulator", "qr", "screen"],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def config(self) -> dict:
        response = self.http.get(f"{self.server_url}/api/devices/config", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def scan(self, badge_token: str, camera_id: str, zone_id: str) -> dict:
        response = self.http.post(
            f"{self.server_url}/api/detect_qr",
            json={
                "camera_id": camera_id,
                "zone_id": zone_id,
                "badge_token": badge_token,
                "confidence": 1.0,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def acknowledge_pending_commands(self) -> list[int]:
        response = self.http.get(
            f"{self.server_url}/api/devices/commands/pending",
            timeout=self.timeout,
        )
        response.raise_for_status()
        acknowledged = []
        for command in response.json():
            command_id = int(command["id"])
            ack = self.http.post(
                f"{self.server_url}/api/devices/commands/{command_id}/ack",
                json={"success": True, "result": {"simulated": True}},
                timeout=self.timeout,
            )
            ack.raise_for_status()
            acknowledged.append(command_id)
        return acknowledged


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Managed-device smoke simulator")
    parser.add_argument("--server-url", default=Config.SERVER_URL)
    parser.add_argument("--device-id", default=Config.DEVICE_ID)
    parser.add_argument("--device-key", default=Config.DEVICE_KEY)
    parser.add_argument("--enrollment-token", default=Config.DEVICE_ENROLLMENT_TOKEN)
    parser.add_argument("--legacy-key", default="", help="Deprecated compatibility mode; never the default")
    parser.add_argument("--name", default="Тестов симулатор")
    parser.add_argument("--device-type", default="simulator")
    parser.add_argument("--badge-token", default="")
    parser.add_argument("--camera-id", default=Config.CAMERA_ID)
    parser.add_argument("--zone-id", default=Config.ZONE_ID)
    parser.add_argument("--ack-commands", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    simulator = DeviceSimulator(args.server_url, Config.HTTP_TIMEOUT_SECONDS)
    try:
        if args.enrollment_token and not args.device_key:
            if not args.device_id:
                parser.error("--device-id е задължителен при enrollment")
            payload = simulator.enroll(
                args.enrollment_token,
                args.device_id,
                args.name,
                args.device_type,
            )
            print("Устройството е сдвоено. Запазете еднократно:")
            print(f"DEVICE_ID={payload['device_id']}")
            print(f"DEVICE_KEY={payload['device_key']}")
        else:
            simulator.set_credentials(args.device_id, args.device_key, args.legacy_key)

        print("Heartbeat:", simulator.heartbeat())
        print("Config:", simulator.config())
        if args.badge_token:
            print("QR scan:", simulator.scan(args.badge_token, args.camera_id, args.zone_id))
        if args.ack_commands:
            print("ACK commands:", simulator.acknowledge_pending_commands())
        return 0
    except (ValueError, requests.RequestException) as exc:
        print(f"Simulator error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
