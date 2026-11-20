#!/usr/bin/env python3
"""test_bridge.py

Deterministic unit test for AlfredBridge Phase 6:
- CRC-16/CCITT-FALSE verification
- Optical 256x2 pixel strip encode/decode round-trip
- Sheldon Bridge Daemon HTTP API (telemetry, party_state.json, tts-event, poll)
- AddOn install & release zip validation
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

# Add bridge dir to sys.path
BRIDGE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BRIDGE_DIR))

from alfred_bridge_relay import (  # noqa: E402
    CELL_COUNT,
    V2_COLS,
    V2_ROWS,
    V3_TOTAL_CELLS,
    crc16_ccitt,
    decode_strip_from_image,
    encode_strip_bytes,
    encode_strip_v2_bits,
    encode_strip_v3_stealth,
)
from sheldon_bridge_daemon import create_server  # noqa: E402

try:
    from PIL import Image
except ImportError:
    Image = None


class TestAlfredBridge(unittest.TestCase):
    def test_crc16_ccitt(self):
        """Standard CCITT-FALSE vector check (init=0xFFFF, poly=0x1021)."""
        test_vector = b"123456789"
        expected_crc = 0x29B1
        self.assertEqual(crc16_ccitt(test_vector), expected_crc)

    def test_strip_encode_decode_roundtrip(self):
        """Round-trip synthetic test for 128-cell 256x2 optical pixel strip."""
        if Image is None:
            self.skipTest("PIL (Pillow) not installed")

        payload = {
            "p": "Arthas",
            "r": "Lordaeron",
            "c": "PALADIN",
            "l": 60,
            "m": 1420,
            "z": "Tirisfal Glades",
            "sz": "Brill",
            "x": 61.25,
            "y": 52.40,
            "cb": 0,
            "t": "Zombie",
            "tn": 1500,
            "q": ["A New Plague", "Gargoyles"],
            "pty": [{"n": "Jaina", "c": "MAGE", "l": 60, "z": "Dalaran"}],
            "s": 3,
            "ev": "boss:Kel'Thuzad",
        }

        # 1. Encode into 384 bytes
        buf = encode_strip_bytes(payload)
        self.assertEqual(len(buf), 384)
        self.assertEqual(buf[0], 0xA1)
        self.assertEqual(buf[1], 0xF2)
        self.assertEqual(buf[2], 0x01)

        # 2. Render into 256x2 synthetic PIL Image (cell_size=2)
        cell_size = 2
        img = Image.new("RGB", (CELL_COUNT * cell_size, cell_size), color=(0, 0, 0))
        pixels = img.load()

        for k in range(CELL_COUNT):
            r = buf[k * 3]
            g = buf[k * 3 + 1]
            b = buf[k * 3 + 2]
            for cx in range(cell_size):
                for cy in range(cell_size):
                    pixels[k * cell_size + cx, cy] = (r, g, b)

        # 3. Decode back from image
        decoded = decode_strip_from_image(img, cell_size=cell_size)
        self.assertIsNotNone(decoded, "Failed to decode valid strip")
        self.assertEqual(decoded, payload)

    def test_strip_v2_contrast_brightness_roundtrip(self):
        """v2 binary 0/255 optical strip survives WoW Contrast 60 + Brightness 47 distortion."""
        if Image is None:
            self.skipTest("PIL (Pillow) not installed")

        payload = {
            "p": "Bennen",
            "c": "PALADIN",
            "l": 27,
            "m": 1421,
            "z": "Silverpine Forest",
            "x": 44.8,
            "y": 67.8,
            "cb": 0,
            "t": "Baron Silverlaine",
            "q": ["Arugal Must Die"],
            "pty": [{"n": "Bobby", "c": "WARR", "l": 27}, {"n": "Lucky", "c": "MAGE", "l": 26}],
            "s": 1,
            "ev": "",
        }
        cells = encode_strip_v2_bits(payload)
        self.assertEqual(len(cells), V2_COLS * V2_ROWS)

        cell_size = 2
        img = Image.new("RGB", (V2_COLS * cell_size, V2_ROWS * cell_size), color=(0, 0, 0))
        pixels = img.load()
        # Simulate heavy Contrast/Brightness/Gamma shift (0 -> 28, 255 -> 218)
        for row in range(V2_ROWS):
            for col in range(V2_COLS):
                r, g, b = cells[row * V2_COLS + col]
                distorted = (218 if r else 28, 218 if g else 28, 218 if b else 28)
                for cx in range(cell_size):
                    for cy in range(cell_size):
                        pixels[col * cell_size + cx, row * cell_size + cy] = distorted

        decoded = decode_strip_from_image(img, cell_size=cell_size)
        self.assertEqual(decoded, payload)

    def test_strip_v3_stealth_hairline_roundtrip(self):
        """v3 642x1 stealth dark charcoal hairline survives Contrast 60 + Brightness 47 shift."""
        if Image is None:
            self.skipTest("PIL (Pillow) not installed")

        payload = {
            "p": "Chudcell",
            "c": "PRIEST",
            "l": 28,
            "m": 1421,
            "z": "Shadowfang Keep",
            "x": 64.0,
            "y": 82.1,
            "cb": 1,
            "t": "Archmage Arugal",
            "q": ["Arugal Must Die"],
            "pty": [
                {"n": "Bobby", "c": "WARRIOR", "l": 27},
                {"n": "Lucky", "c": "MAGE", "l": 26},
                {"n": "Josh", "c": "ROGUE", "l": 28},
                {"n": "Iced", "c": "DRUID", "l": 27},
            ],
            "s": 4,
            "ev": "",
        }
        cells = encode_strip_v3_stealth(payload)
        self.assertEqual(len(cells), V3_TOTAL_CELLS)

        cell_size = 2
        img = Image.new("RGB", (V3_TOTAL_CELLS * cell_size, cell_size), color=(0, 0, 0))
        pixels = img.load()
        # Simulate WoW Contrast 60 + Brightness 47 mapping dark charcoal (20 -> 13, 51 -> 48)
        for col, (r, g, b) in enumerate(cells):
            shifted = (
                48 if r > 35 else 13,
                48 if g > 35 else 13,
                48 if b > 35 else 13,
            )
            for cx in range(cell_size):
                for cy in range(cell_size):
                    pixels[col * cell_size + cx, cy] = shifted

        decoded = decode_strip_from_image(img, cell_size=cell_size)
        self.assertEqual(decoded, payload)

    def test_daemon_telemetry_and_poll(self):
        """Spins up Sheldon Bridge Daemon on ephemeral port, tests /telemetry and /tts-event -> /poll."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            party_state_file = Path(tmp_dir) / "party_state.json"
            server = create_server("127.0.0.1", 0, party_state_file)
            host, port = server.server_address

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            base_url = f"http://{host}:{port}"

            try:
                # 1. Check health
                req = urllib.request.Request(f"{base_url}/health")
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(data.get("ok"))

                # 2. POST /telemetry
                telemetry = {
                    "p": "Ben",
                    "r": "Forever",
                    "c": "WARRIOR",
                    "l": 25,
                    "m": 1411,
                    "z": "Durotar",
                    "sz": "Razor Hill",
                    "x": 52.4,
                    "y": 43.8,
                    "cb": 1,
                    "t": "Kul Tiras Marine",
                    "tn": 3192,
                    "q": ["Break the Line"],
                    "pty": [],
                    "s": 2,
                    "ev": "",
                }
                req = urllib.request.Request(
                    f"{base_url}/telemetry",
                    data=json.dumps(telemetry).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    self.assertEqual(resp.status, 200)

                # Verify party_state.json
                self.assertTrue(party_state_file.exists())
                with open(party_state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)

                self.assertIn("live", state)
                self.assertIn("ben-forever", state["live"])
                live_player = state["live"]["ben-forever"]
                self.assertEqual(live_player["level"], 25)
                self.assertEqual(live_player["class"], "WARRIOR")
                self.assertEqual(live_player["subzone"], "Razor Hill")

                self.assertIn("players", state)
                self.assertIn("ben-forever", state["players"])
                self.assertIn("level 25 Warrior (Ben) in Razor Hill (52.4, 43.8)", state["players"]["ben-forever"]["summary"])

                # 3. POST /tts-event
                tts_payload = {
                    "speaking": True,
                    "waypoints": [
                        {
                            "label": "Razor Hill Watchtower",
                            "zone": "Durotar",
                            "mapID": 1411,
                            "x": 52.4,
                            "y": 43.8,
                            "way_cmd": "/way Durotar 52.4 43.8 Razor Hill Watchtower",
                        }
                    ],
                }
                req = urllib.request.Request(
                    f"{base_url}/tts-event",
                    data=json.dumps(tts_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(resp_data.get("ok"))
                    self.assertEqual(resp_data.get("pin_seq"), 1)

                # 4. GET /poll?since_pin=0
                req = urllib.request.Request(f"{base_url}/poll?since_pin=0")
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    poll_data = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(poll_data.get("ok"))
                    self.assertTrue(poll_data.get("speaking"))
                    self.assertEqual(poll_data.get("pin_seq"), 1)
                    pin = poll_data.get("pin")
                    self.assertIsNotNone(pin)
                    self.assertEqual(pin.get("label"), "Razor Hill Watchtower")

                # 5. GET /poll?since_pin=1 (already seen pin)
                req = urllib.request.Request(f"{base_url}/poll?since_pin=1")
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    poll_data = json.loads(resp.read().decode("utf-8"))
                    self.assertIsNone(poll_data.get("pin"))
                    self.assertTrue(poll_data.get("speaking"))

            finally:
                server.shutdown()
                server.server_close()

    def test_dist_zip_and_installed_addon(self):
        """Verifies release zip and installed addon files exist."""
        project_root = BRIDGE_DIR.parent
        zip_path = project_root / "dist" / "AlfredBridge-v1.0.0.zip"
        self.assertTrue(zip_path.exists(), f"Expected distribution zip missing at {zip_path}")

        default_addon_dir = Path(r"C:\Program Files (x86)\World of Warcraft\_forever_\Interface\AddOns\AlfredBridge")
        if default_addon_dir.parent.exists():
            self.assertTrue(default_addon_dir.exists(), f"Addon not installed at {default_addon_dir}")
            self.assertTrue((default_addon_dir / "AlfredBridge.toc").exists())
            self.assertTrue((default_addon_dir / "Bindings.xml").exists())
            self.assertTrue((default_addon_dir / "AlfredBridge.lua").exists())


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAlfredBridge)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
