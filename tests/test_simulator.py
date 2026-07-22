"""Unit tests for the managed-device simulator authentication boundary."""

import unittest

from tools.simulate_nodes import DeviceSimulator, build_device_headers


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _FakeSession:
    def __init__(self):
        self.posts = []

    def get(self, url, timeout):
        return _Response([{"id": 7}, {"id": 9}])

    def post(self, url, json, timeout):
        self.posts.append((url, json, timeout))
        return _Response({"success": True})


class TestDeviceSimulator(unittest.TestCase):
    def test_individual_credentials_include_id_and_key(self):
        self.assertEqual(
            build_device_headers("screen-01", "individual-secret"),
            {"X-Device-ID": "screen-01", "X-Device-Key": "individual-secret"},
        )

    def test_partial_individual_credentials_are_rejected(self):
        with self.assertRaises(ValueError):
            build_device_headers(device_id="screen-01")
        with self.assertRaises(ValueError):
            build_device_headers(device_key="individual-secret")

    def test_legacy_key_requires_explicit_argument(self):
        self.assertEqual(
            build_device_headers(legacy_key="temporary-shared-key"),
            {"X-Device-Key": "temporary-shared-key"},
        )
        with self.assertRaises(ValueError):
            build_device_headers()

    def test_pending_commands_are_acknowledged_individually(self):
        simulator = DeviceSimulator("http://school.test", timeout=4)
        fake_session = _FakeSession()
        simulator.http = fake_session

        self.assertEqual(simulator.acknowledge_pending_commands(), [7, 9])
        self.assertEqual(
            [item[0] for item in fake_session.posts],
            [
                "http://school.test/api/devices/commands/7/ack",
                "http://school.test/api/devices/commands/9/ack",
            ],
        )
        self.assertTrue(all(item[1] == {"success": True, "result": {"simulated": True}} for item in fake_session.posts))


if __name__ == "__main__":
    unittest.main()
