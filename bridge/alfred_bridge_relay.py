#!/usr/bin/env python3
"""alfred_bridge_relay.py

Windows companion relay for Alfred Discord Voice and AlfredBridge WoW Forever AddOn.
Decodes the optical pixel strip from the WoW client, forwards live telemetry to
the Sheldon Bridge Daemon, and relays waypoint pins / audio ducking signals back into WoW.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageGrab
except ImportError:
    Image = None
    ImageGrab = None

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
else:
    ctypes = None

# Public GitHub hygiene defaults - NO hardcoded private IPs or credentials
DEFAULT_SERVER_URL = os.environ.get("ALFRED_BRIDGE_URL", "http://127.0.0.1:8098")
DEFAULT_TOKEN = os.environ.get("ALFRED_BRIDGE_TOKEN", "")
DEFAULT_WOW_FOREVER_DIR = os.environ.get(
    "WOW_FOREVER_DIR",
    r"C:\Program Files (x86)\World of Warcraft\_forever_",
)
DEFAULT_SPEAKER_NAME = os.environ.get("ALFRED_SPEAKER_NAME", "")
DEFAULT_SPEAKER_ID = os.environ.get("ALFRED_SPEAKER_ID", "")

# Magic framing constants (v1 raw RGB + v2 contrast/gamma-immune binary 0/255 RGB)
MAGIC_0 = 0xA1
MAGIC_1 = 0xF2
PROTOCOL_VERSION = 0x01
MAX_PAYLOAD_BYTES = 377
CELL_COUNT = 128
BUFFER_BYTES = CELL_COUNT * 3  # 384 bytes

V2_MAGIC_0 = 0xA5
V2_MAGIC_1 = 0x5A
V2_VERSION = 0x02
V2_COLS = 128
V2_ROWS = 5
V2_CELLS = V2_COLS * V2_ROWS
V2_BUFFER_BYTES = (V2_CELLS * 3) // 8  # 240 bytes
V2_MAX_PAYLOAD = V2_BUFFER_BYTES - 6  # 234 bytes

V3_VERSION = 0x03
V3_DATA_CELLS = 896
V3_TOTAL_CELLS = 898
V3_BUFFER_BYTES = (V3_DATA_CELLS * 3) // 8  # 336 bytes
V3_MAX_PAYLOAD = V3_BUFFER_BYTES - 7        # 329 bytes
V3_TONE_LOW = 20   # round(0.08 * 255) subtle dark charcoal
V3_TONE_HIGH = 51  # round(0.20 * 255) subtle dark charcoal


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly=0x1021, init=0xFFFF, no final xor)."""
    crc = 0xFFFF
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def encode_strip_bytes(payload_dict: dict) -> bytes:
    """Encodes a payload dictionary into the exact 384-byte optical strip binary format."""
    payload_str = json.dumps(payload_dict, separators=(",", ":"), ensure_ascii=False)
    payload_bytes = payload_str.encode("utf-8")
    L = len(payload_bytes)
    if L > MAX_PAYLOAD_BYTES:
        raise ValueError(f"Payload size {L} exceeds maximum allowable {MAX_PAYLOAD_BYTES} bytes")

    crc = crc16_ccitt(payload_bytes)

    buf = bytearray(BUFFER_BYTES)
    buf[0] = MAGIC_0
    buf[1] = MAGIC_1
    buf[2] = PROTOCOL_VERSION
    buf[3] = (L >> 8) & 0xFF
    buf[4] = L & 0xFF
    buf[5] = (crc >> 8) & 0xFF
    buf[6] = crc & 0xFF
    buf[7 : 7 + L] = payload_bytes
    # Remaining bytes remain 0
    return bytes(buf)


def encode_strip_v2_bits(payload_dict: dict) -> list[tuple[int, int, int]]:
    """Encodes payload into 640 high-contrast binary (0 or 255) RGB cells for 128x5 grid."""
    payload_bytes = json.dumps(payload_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    L = len(payload_bytes)
    if L > V2_MAX_PAYLOAD:
        raise ValueError(f"Payload size {L} exceeds v2 max {V2_MAX_PAYLOAD}")
    crc = crc16_ccitt(payload_bytes)
    buf = bytearray(V2_BUFFER_BYTES)
    buf[0], buf[1], buf[2], buf[3] = V2_MAGIC_0, V2_MAGIC_1, V2_VERSION, L & 0xFF
    buf[4], buf[5] = (crc >> 8) & 0xFF, crc & 0xFF
    buf[6 : 6 + L] = payload_bytes
    cells = []
    for k in range(V2_CELLS):
        rgb = []
        for ch in range(3):
            bit_idx = k * 3 + ch
            bit_val = (buf[bit_idx // 8] >> (7 - (bit_idx % 8))) & 1
            rgb.append(255 if bit_val else 0)
        cells.append(tuple(rgb))
    return cells


def _decode_v2_binary(pixels, width: int, height: int, cell_size: int) -> Optional[dict]:
    if width < V2_COLS * cell_size or height < V2_ROWS * cell_size:
        return None
    buf = bytearray(V2_BUFFER_BYTES)
    half = cell_size // 2
    for row in range(V2_ROWS):
        sy = min(row * cell_size + half, height - 1)
        for col in range(V2_COLS):
            sx = min(col * cell_size + half, width - 1)
            r, g, b = pixels[sx, sy][:3]
            k = row * V2_COLS + col
            for ch, val in ((0, r), (1, g), (2, b)):
                if val >= 128:
                    bit_idx = k * 3 + ch
                    buf[bit_idx // 8] |= 1 << (7 - (bit_idx % 8))
    if buf[0] != V2_MAGIC_0 or buf[1] != V2_MAGIC_1 or buf[2] != V2_VERSION:
        return None
    L = buf[3]
    if L > V2_MAX_PAYLOAD or (6 + L) > V2_BUFFER_BYTES:
        return None
    expected_crc = (buf[4] << 8) | buf[5]
    payload_bytes = bytes(buf[6 : 6 + L])
    if expected_crc != crc16_ccitt(payload_bytes):
        return None
    try:
        return json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return None


def encode_strip_v3_stealth(payload_dict: dict) -> list[tuple[int, int, int]]:
    """Encodes payload into 698 single-row dark charcoal cells (2-cell preamble + 696 data cells)."""
    payload_bytes = json.dumps(payload_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    L = len(payload_bytes)
    if L > V3_MAX_PAYLOAD:
        raise ValueError(f"Payload size {L} exceeds v3 max {V3_MAX_PAYLOAD}")
    crc = crc16_ccitt(payload_bytes)
    buf = bytearray(V3_BUFFER_BYTES)
    buf[0], buf[1], buf[2] = V2_MAGIC_0, V2_MAGIC_1, V3_VERSION
    buf[3], buf[4] = (L >> 8) & 0xFF, L & 0xFF
    buf[5], buf[6] = (crc >> 8) & 0xFF, crc & 0xFF
    buf[7 : 7 + L] = payload_bytes
    cells: list[tuple[int, int, int]] = [
        (V3_TONE_LOW, V3_TONE_HIGH, V3_TONE_LOW),
        (V3_TONE_HIGH, V3_TONE_LOW, V3_TONE_HIGH),
    ]
    for k in range(V3_DATA_CELLS):
        rgb = []
        for ch in range(3):
            bit_idx = k * 3 + ch
            bit_val = (buf[bit_idx // 8] >> (7 - (bit_idx % 8))) & 1
            rgb.append(V3_TONE_HIGH if bit_val else V3_TONE_LOW)
        cells.append((rgb[0], rgb[1], rgb[2]))
    return cells


def _decode_v3_stealth(pixels, width: int, height: int, cell_size: int) -> Optional[dict]:
    """Decodes 698x1 Stealth Hairline strip using dynamic threshold from 2-cell preamble."""
    if width < V3_TOTAL_CELLS * cell_size or height < 1:
        return None
    half = cell_size // 2
    sy = min(half, height - 1)
    r0, g0, b0 = pixels[half, sy][:3]
    r1, g1, b1 = pixels[cell_size + half, sy][:3]
    if max(r0, b0, g1) >= min(g0, r1, b1):
        return None
    low_ref = (r0 + b0 + g1) / 3.0
    high_ref = (g0 + r1 + b1) / 3.0
    if (high_ref - low_ref) < 6.0 or high_ref > 160.0:
        return None
    threshold = (low_ref + high_ref) / 2.0

    buf = bytearray(V3_BUFFER_BYTES)
    for k in range(V3_DATA_CELLS):
        sx = (k + 2) * cell_size + half
        r, g, b = pixels[sx, sy][:3]
        for ch, val in ((0, r), (1, g), (2, b)):
            if val >= threshold:
                bit_idx = k * 3 + ch
                buf[bit_idx // 8] |= 1 << (7 - (bit_idx % 8))

    if buf[0] != V2_MAGIC_0 or buf[1] != V2_MAGIC_1 or buf[2] != V3_VERSION:
        return None
    L = (buf[3] << 8) | buf[4]
    if L > V3_MAX_PAYLOAD or (7 + L) > V3_BUFFER_BYTES:
        return None
    expected_crc = (buf[5] << 8) | buf[6]
    payload_bytes = bytes(buf[7 : 7 + L])
    if expected_crc != crc16_ccitt(payload_bytes):
        return None
    try:
        return json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return None


def decode_strip_from_image(img, cell_size: int = 2) -> Optional[dict]:
    """Decodes v3 stealth hairline (642x1), v2 binary (128x5), or v1 RGB (128x1) optical strip."""
    if img is None:
        return None

    width, height = img.size
    if img.mode != "RGB":
        img = img.convert("RGB")
    pixels = img.load()

    # Try v3 stealth dark-charcoal hairline first (self-calibrating 2-cell preamble)
    v3 = _decode_v3_stealth(pixels, width, height, cell_size)
    if v3 is not None:
        return v3

    # Fallback to v2 high-contrast binary strip (128x5)
    v2 = _decode_v2_binary(pixels, width, height, cell_size)
    if v2 is not None:
        return v2

    required_width = CELL_COUNT * cell_size
    if width < required_width:
        return None

    buf = bytearray(BUFFER_BYTES)
    sample_y = min(cell_size // 2, height - 1)

    for k in range(CELL_COUNT):
        sample_x = k * cell_size + (cell_size // 2)
        if sample_x >= width:
            return None
        r, g, b = pixels[sample_x, sample_y][:3]
        buf[k * 3] = r
        buf[k * 3 + 1] = g
        buf[k * 3 + 2] = b

    # Validate header
    if buf[0] != MAGIC_0 or buf[1] != MAGIC_1:
        return None
    if buf[2] != PROTOCOL_VERSION:
        return None

    L = (buf[3] << 8) | buf[4]
    if L > MAX_PAYLOAD_BYTES or (7 + L) > BUFFER_BYTES:
        return None

    expected_crc = (buf[5] << 8) | buf[6]
    payload_bytes = bytes(buf[7 : 7 + L])
    actual_crc = crc16_ccitt(payload_bytes)

    if expected_crc != actual_crc:
        return None

    try:
        payload_str = payload_bytes.decode("utf-8")
        return json.loads(payload_str)
    except Exception:
        return None


# Windows Native Interop Helpers
GMEM_MOVEABLE = 0x0002
CF_UNICODETEXT = 13
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_F10 = 0x79
VK_F11 = 0x7A
VK_F12 = 0x7B
KEYEVENTF_KEYUP = 0x0002


def set_windows_clipboard(text: str) -> bool:
    """Sets Unicode text onto the Windows clipboard."""
    if sys.platform != "win32" or ctypes is None:
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        encoded = text.encode("utf-16le") + b"\x00\x00"
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not h_mem:
            return False
        p_mem = kernel32.GlobalLock(h_mem)
        if not p_mem:
            return False
        ctypes.memmove(p_mem, encoded, len(encoded))
        kernel32.GlobalUnlock(h_mem)
        user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        return True
    except Exception:
        return False
    finally:
        user32.CloseClipboard()


def send_ctrl_alt_key(vk_key: int) -> None:
    """Simulates pressing and releasing CTRL+ALT+{vk_key}."""
    if sys.platform != "win32" or ctypes is None:
        return
    user32 = ctypes.windll.user32
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(vk_key, 0, 0, 0)
    time.sleep(0.04)
    user32.keybd_event(vk_key, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def find_wow_window_and_origin(title_hint: str = "World of Warcraft"):
    """Finds the WoW window handle and top-left screen client origin."""
    if sys.platform != "win32" or ctypes is None:
        return None, (0, 0)

    user32 = ctypes.windll.user32
    found_hwnds = []

    def enum_windows_cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if title_hint.lower() in buf.value.lower():
                    found_hwnds.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(enum_windows_cb), 0)

    if not found_hwnds:
        return None, (0, 0)

    hwnd = found_hwnds[0]
    pt = POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return hwnd, (pt.x, pt.y)


def http_request(url: str, method: str = "GET", data: Optional[dict] = None, token: str = "") -> Optional[dict]:
    """Helper to issue HTTP requests with optional JSON body and bearer auth."""
    body_bytes = json.dumps(data).encode("utf-8") if data is not None else None
    headers = {"User-Agent": "AlfredBridgeRelay/1.0.0"}
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            content = resp.read()
            if content:
                return json.loads(content.decode("utf-8"))
            return {"ok": True}
    except Exception as e:
        return None


def write_pin_addon_file(wow_dir: Path, slot: int, seq: int, pin: dict) -> Path:
    """Writes the OnDemand pin.lua for the specified slot."""
    pin_dir = wow_dir / "Interface" / "AddOns" / f"AlfredBridge_Pin{slot}"
    pin_dir.mkdir(parents=True, exist_ok=True)
    pin_file = pin_dir / "pin.lua"

    name_clean = str(pin.get("label") or pin.get("name") or "Alfred Waypoint").replace('"', '\\"')
    zone_clean = str(pin.get("zone") or "").replace('"', '\\"')
    map_id = int(pin.get("mapID") or 0)
    x = float(pin.get("x") or 0.0)
    y = float(pin.get("y") or 0.0)

    content = (
        f'_G.AlfredBridgePendingPin = {{\n'
        f'    seq = {seq},\n'
        f'    name = "{name_clean}",\n'
        f'    zone = "{zone_clean}",\n'
        f'    mapID = {map_id},\n'
        f'    x = {x:.2f},\n'
        f'    y = {y:.2f}\n'
        f'}}\n'
    )
    pin_file.write_text(content, encoding="utf-8")
    return pin_file


def main():
    parser = argparse.ArgumentParser(description="AlfredBridge Windows Optical Relay")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL, help="Sheldon Bridge Daemon URL")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Bearer token for Daemon auth")
    parser.add_argument("--wow-dir", default=DEFAULT_WOW_FOREVER_DIR, help="Path to WoW Forever directory")
    parser.add_argument("--cell-size", type=int, default=2, help="Physical pixel size per strip cell (default 2)")
    parser.add_argument("--poll-interval", type=float, default=0.25, help="Daemon polling interval in seconds")
    parser.add_argument("--speaker", default=DEFAULT_SPEAKER_NAME, help="Optional Discord display name for speaker matching")
    parser.add_argument("--speaker-id", default=DEFAULT_SPEAKER_ID, help="Optional Discord user ID for speaker matching")
    parser.add_argument("--auto-keys", action="store_true", help="Send synthetic CTRL-ALT-F10/F11/F12 keys into WoW (disabled by default for Blizzard ToS safety)")
    args = parser.parse_args()

    wow_dir = Path(args.wow_dir)
    server_url = args.server.rstrip("/")
    if sys.platform == "win32" and ctypes is not None:
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    print(f"[Relay] Target daemon: {server_url}")
    print(f"[Relay] WoW client directory: {wow_dir}")

    last_pin_seq = 0
    next_pin_slot = 1
    last_speaking = False
    pending_pin_trigger = False
    last_telemetry_sent = 0.0
    last_telemetry_sig = ""
    logged_wow_origin = False
    user32 = ctypes.windll.user32 if (sys.platform == "win32" and ctypes is not None) else None

    while True:
        hwnd, origin = find_wow_window_and_origin("World of Warcraft")
        if not hwnd or ImageGrab is None:
            time.sleep(1.0)
            continue
        if not logged_wow_origin:
            print(f"[Relay] Attached to WoW window (hwnd={hwnd}, origin={origin})")
            logged_wow_origin = True
        wow_focused = bool(user32 and user32.GetForegroundWindow() == hwnd)
        if args.auto_keys and wow_focused and pending_pin_trigger:
            print("[Relay] WoW regained focus -> Triggering queued LOAD_PIN (CTRL-ALT-F10)")
            send_ctrl_alt_key(VK_F10)
            pending_pin_trigger = False

        # 1. Optical Strip Grab (642x1 v3 stealth hairline at cell_size, with 5-row height for v2 fallback)
        left, top = origin
        strip_width = V3_TOTAL_CELLS * args.cell_size
        strip_height = V2_ROWS * args.cell_size
        bbox = (left, top, left + strip_width, top + strip_height)

        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
            telemetry = decode_strip_from_image(img, cell_size=args.cell_size)
        except Exception:
            telemetry = None

        now = time.time()
        if telemetry:
            # Update tracked slot
            slot = telemetry.get("s")
            if isinstance(slot, int) and 1 <= slot <= 20:
                next_pin_slot = slot

            pty_sig = ",".join(f"{m.get('n','')}:{m.get('l',0)}" for m in (telemetry.get("pty") or []) if isinstance(m, dict))
            sig = f"{telemetry.get('z')}|{telemetry.get('sz')}|{telemetry.get('x')}|{telemetry.get('y')}|{telemetry.get('cb')}|{telemetry.get('t')}|{telemetry.get('ev')}|{pty_sig}"
            time_since_last = now - last_telemetry_sent

            # Post on state change (min interval 2.0s) or 15s heartbeat
            if (sig != last_telemetry_sig and time_since_last >= 2.0) or (time_since_last >= 15.0):
                if args.speaker:
                    telemetry["speaker"] = args.speaker
                if args.speaker_id:
                    telemetry["speaker_id"] = args.speaker_id
                resp = http_request(f"{server_url}/telemetry", method="POST", data=telemetry, token=args.token)
                if resp and resp.get("ok"):
                    if last_telemetry_sent == 0.0:
                        print(f"[Relay] Telemetry synced -> {telemetry.get('p', '?')} in {telemetry.get('z', '?')} ({telemetry.get('x', 0):.1f}, {telemetry.get('y', 0):.1f}) | party={len(telemetry.get('pty') or [])}")
                    last_telemetry_sent = now
                    last_telemetry_sig = sig

        # 2. Poll Daemon for TTS Speaking state & Waypoints
        poll_resp = http_request(
            f"{server_url}/poll?since_pin={last_pin_seq}",
            method="GET",
            token=args.token,
        )

        if poll_resp and poll_resp.get("ok"):
            speaking = bool(poll_resp.get("speaking", False))
            if args.auto_keys and speaking != last_speaking and wow_focused:
                if speaking:
                    print("[Relay] TTS started speaking -> Sending ALFREDBRIDGE_DUCK_ON (CTRL-ALT-F11)")
                    send_ctrl_alt_key(VK_F11)
                else:
                    print("[Relay] TTS finished speaking -> Sending ALFREDBRIDGE_DUCK_OFF (CTRL-ALT-F12)")
                    send_ctrl_alt_key(VK_F12)
                last_speaking = speaking

            pin_seq = poll_resp.get("pin_seq", 0)
            pin = poll_resp.get("pin")
            if pin_seq > last_pin_seq and pin:
                if next_pin_slot <= 20:
                    print(f"[Relay] New waypoint #{pin_seq} -> Writing to slot {next_pin_slot}")
                    write_pin_addon_file(wow_dir, next_pin_slot, pin_seq, pin)

                    way_cmd = pin.get("way_cmd")
                    if not way_cmd:
                        z = pin.get("zone", "")
                        lbl = pin.get("label") or pin.get("name") or "Alfred Waypoint"
                        x = pin.get("x", 0.0)
                        y = pin.get("y", 0.0)
                        way_cmd = f"/way {z} {x:.1f} {y:.1f} {lbl}".strip()

                    set_windows_clipboard(way_cmd)
                    if not args.auto_keys:
                        print(f"[Relay] Pin #{pin_seq} ready -> Press CTRL-ALT-F10 in WoW to load WaypointUI ({way_cmd})")
                    elif wow_focused:
                        print(f"[Relay] Copied '{way_cmd}' to clipboard -> Triggering LOAD_PIN (CTRL-ALT-F10)")
                        send_ctrl_alt_key(VK_F10)
                    else:
                        print(f"[Relay] Copied '{way_cmd}' to clipboard -> Queued LOAD_PIN until WoW is focused")
                        pending_pin_trigger = True
                else:
                    print(f"[Relay] Warning: Pin slot {next_pin_slot} exceeds limit (20). Reload UI (/reload) to reset.")
                last_pin_seq = pin_seq

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
