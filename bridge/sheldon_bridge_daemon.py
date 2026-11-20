#!/usr/bin/env python3
"""sheldon_bridge_daemon.py

Lightweight bridge daemon for Alfred Discord Voice.
Runs on Sheldon (or localhost during tests) to maintain live party state
and queue waypoint pins / duck events for the Windows relay.
"""

import argparse
import json
import os
import socket
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_HOST = os.environ.get("ALFRED_BRIDGE_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.environ.get("ALFRED_BRIDGE_PORT", "8098"))
DEFAULT_TOKEN = os.environ.get("ALFRED_BRIDGE_TOKEN", "")
VOICE_CTRL_SOCK = os.environ.get("VOICE_CTRL_SOCK", "/tmp/alfred_voice_ctrl.sock")
DEFAULT_PARTY_STATE_PATH = os.environ.get(
    "VOICE_PARTY_STATE",
    str(Path.home() / ".openclaw" / "workspace" / "party_state.json"),
)


def _query_voice_ctrl_playing() -> bool | None:
    """Query OpenClaw voice control socket if available; returns None when absent (e.g. unit tests)."""
    if not hasattr(socket, "AF_UNIX") or not os.path.exists(VOICE_CTRL_SOCK):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.15)
            s.connect(VOICE_CTRL_SOCK)
            s.sendall(b"status\n")
            line = s.makefile().readline()
            return bool(json.loads(line).get("playing", False)) if line else None
    except Exception:
        return None


class DaemonState:
    def __init__(self, party_state_path: Path, token: str = ""):
        self.lock = threading.Lock()
        self.party_state_path = party_state_path
        self.token = token
        self.speaking = False
        self.speaking_started = 0.0
        self.speaking_until = 0.0
        self.pin_seq = 0
        self.latest_pin = None
        self.last_moment_sig = ""
        self.last_moment_ts = 0.0

    def is_speaking(self) -> bool:
        with self.lock:
            return self._compute_speaking_locked()

    def _compute_speaking_locked(self) -> bool:
        now = time.time()
        sock_playing = _query_voice_ctrl_playing()
        if sock_playing is True:
            self.speaking = True
            self.speaking_until = max(self.speaking_until, now + 1.0)
            return True
        if not self.speaking:
            return False
        if now >= self.speaking_until:
            self.speaking = False
            self.speaking_until = 0.0
            return False
        # After 1.5s startup grace (while OpenClaw loads WAV), trust voice_ctrl stop/idle immediately
        if sock_playing is False and (now - self.speaking_started) > 1.5:
            self.speaking = False
            self.speaking_until = 0.0
            return False
        return True

    def set_tts_event(self, speaking: bool, waypoints: list, duration_s: float = 0.0) -> int:
        with self.lock:
            now = time.time()
            if speaking:
                self.speaking = True
                self.speaking_started = now
                dur = float(duration_s) if duration_s and duration_s > 0 else 8.0
                self.speaking_until = now + min(45.0, max(1.5, dur))
            else:
                self.speaking = False
                self.speaking_until = 0.0

            if waypoints:
                self.pin_seq += 1
                self.latest_pin = waypoints[0]
            return self.pin_seq

    def get_poll_data(self, since_pin: int) -> dict:
        with self.lock:
            active_speaking = self._compute_speaking_locked()
            pin = self.latest_pin if self.pin_seq > since_pin else None
            return {
                "ok": True,
                "speaking": active_speaking,
                "pin_seq": self.pin_seq,
                "pin": pin,
            }

    def load_party_state(self) -> dict:
        if self.party_state_path.exists():
            try:
                with open(self.party_state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[Daemon] Warning reading {self.party_state_path}: {e}", file=sys.stderr)
        return {"live": {}, "players": {}, "party": {}}

    def update_telemetry(self, data: dict) -> None:
        with self.lock:
            state = self.load_party_state()
            state.setdefault("live", {})
            state.setdefault("players", {})
            state.setdefault("party", {})

            # Extract fields handling both compact optical keys and verbose keys
            char = data.get("p") or data.get("char") or data.get("name") or "Player"
            realm = data.get("r") or data.get("realm") or ""
            cls = (data.get("c") or data.get("class") or "").upper()
            try:
                level = int(data.get("l") or data.get("level") or 1)
            except (ValueError, TypeError):
                level = 1
            try:
                map_id = int(data.get("m") or data.get("mapID") or 0)
            except (ValueError, TypeError):
                map_id = 0
            zone = data.get("z") or data.get("zone") or ""
            subzone = data.get("sz") or data.get("subzone") or ""
            try:
                x = float(data.get("x") or 0.0)
                y = float(data.get("y") or 0.0)
            except (ValueError, TypeError):
                x, y = 0.0, 0.0

            combat = bool(data.get("cb") or data.get("combat") or 0)
            target = data.get("t") or data.get("target") or ""
            target_npc_id = data.get("tn") or data.get("target_npc_id")
            quests = data.get("q") or data.get("quests") or []
            party = data.get("pty") or data.get("party") or []
            ev = str(data.get("ev") or "").strip()
            speaker_name = str(data.get("speaker") or "").strip()
            speaker_id = str(data.get("speaker_id") or "").strip()

            pkey = (
                speaker_id
                or (speaker_name.lower() if speaker_name else "")
                or (f"{char}-{realm}".lower() if realm else char.lower())
            )
            now_ts = int(time.time())

            state["live"][pkey] = {
                "name": speaker_name or char,
                "char": char,
                "realm": realm,
                "class": cls,
                "level": level,
                "mapID": map_id,
                "zone": zone,
                "subzone": subzone,
                "x": round(x, 2),
                "y": round(y, 2),
                "combat": combat,
                "target": target,
                "target_npc_id": target_npc_id,
                "quests": quests,
                "party": party,
                "ts_epoch": now_ts,
                "updated_ts": now_ts,
            }

            if pkey not in state["players"]:
                state["players"][pkey] = {}
            if speaker_name:
                state["players"][pkey]["name"] = speaker_name

            loc_str = subzone if subzone else zone
            state["players"][pkey]["summary"] = (
                f"level {level} {cls.title()} ({char}) in {loc_str} ({x:.1f}, {y:.1f})"
            )

            if ev and (ev != self.last_moment_sig or (now_ts - self.last_moment_ts) >= 300):
                self.last_moment_sig = ev
                self.last_moment_ts = now_ts
                state["party"]["last_moment"] = {"event": ev, "char": char, "ts": now_ts}

            if party:
                current_goal = state["party"].get("goal", "")
                if not current_goal:
                    state["party"]["goal"] = f"Exploring {loc_str or 'Azeroth'}"
                state["party"]["location"] = f"{loc_str} ({x:.1f}, {y:.1f})"

            # Atomic save
            self.party_state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self.party_state_path.with_suffix(f".tmp.{os.getpid()}")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            tmp_file.replace(self.party_state_path)


class BridgeRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Keep daemon logs clean unless in debug
        pass

    def check_auth(self) -> bool:
        expected = self.server.daemon_state.token
        if not expected:
            return True
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()
            return token == expected
        return False

    def send_json(self, status_code: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/status", "/health"):
            self.send_json(HTTPStatus.OK, {"ok": True, "service": "sheldon_bridge_daemon"})
            return

        if not self.check_auth():
            self.send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
            return

        if parsed.path == "/poll":
            params = parse_qs(parsed.query)
            try:
                since_pin = int(params.get("since_pin", [0])[0])
            except (ValueError, TypeError):
                since_pin = 0

            resp = self.server.daemon_state.get_poll_data(since_pin)
            self.send_json(HTTPStatus.OK, resp)
            return

        self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not Found"})

    def do_POST(self):
        if not self.check_auth():
            self.send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
            return

        parsed = urlparse(self.path)
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len > 1_000_000:
            self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "Payload too large"})
            return

        try:
            raw_body = self.rfile.read(content_len).decode("utf-8")
            payload = json.loads(raw_body) if raw_body else {}
        except Exception as e:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"Invalid JSON: {e}"})
            return

        if parsed.path == "/telemetry":
            try:
                self.server.daemon_state.update_telemetry(payload)
                self.send_json(HTTPStatus.OK, {"ok": True})
            except Exception as e:
                self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/tts-event":
            speaking = bool(payload.get("speaking", False))
            waypoints = payload.get("waypoints", [])
            try:
                duration_s = float(payload.get("duration_s") or 0.0)
            except (ValueError, TypeError):
                duration_s = 0.0
            new_seq = self.server.daemon_state.set_tts_event(speaking, waypoints, duration_s=duration_s)
            self.send_json(HTTPStatus.OK, {"ok": True, "pin_seq": new_seq})
            return

        self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not Found"})


def create_server(host: str, port: int, party_state_path: Path, token: str = "") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), BridgeRequestHandler)
    server.daemon_state = DaemonState(party_state_path=party_state_path, token=token)
    return server


def main():
    parser = argparse.ArgumentParser(description="Sheldon Bridge Daemon for Alfred Voice")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host to bind (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind (default 8098)")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Optional bearer token for auth")
    parser.add_argument(
        "--party-state",
        default=DEFAULT_PARTY_STATE_PATH,
        help="Path to party_state.json",
    )
    args = parser.parse_args()

    state_path = Path(args.party_state).resolve()
    server = create_server(args.host, args.port, state_path, args.token)
    print(f"[Daemon] Sheldon Bridge listening on {args.host}:{args.port}")
    print(f"[Daemon] Party state output: {state_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Daemon] Shutting down...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
