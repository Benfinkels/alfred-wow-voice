#!/usr/bin/env python3
"""Warm faster-whisper STT daemon over a local Unix socket (CUDA_VISIBLE_DEVICES=1 on GTX 1080)."""
import json, os, socket, time
from faster_whisper import WhisperModel

SOCKET_PATH = os.environ.get("WHISPER_SOCK", "/tmp/whisper_stt.sock")
MODEL = os.environ.get("WHISPER_MODEL", "distil-large-v3")
DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
HOTWORDS = os.environ.get(
    "WHISPER_HOTWORDS",
    "Alfred, Furlbrow, Blanchy, Stoutmantle, Deadmines, VanCleef, Westfall, Riverglades, Zephras, "
    "Defias, Murloc, Gnoll, Goretusk, Thrall, Sylvanas, Arthas, Illidan, Malfurion, Tyrande, "
    "Azshara, Onyxia, Nefarian, Ragnaros, Medivh, Grommash, Cairne, Jaina, Uther, Kel'Thuzad, "
    "Redridge, Duskwood, Elwynn, Stormwind, Orgrimmar, Ironforge, Undercity, "
    "Southshore, Hillsbrad, Tarren Mill, Menethil, Wetlands, Dalaran, Silverpine, Arugal, "
    "Decurse, Dispel, Polymorph, Scorch, Fireball, Frostbolt",
)

model = WhisperModel(MODEL, device=DEVICE, device_index=0, compute_type=COMPUTE_TYPE)
if os.path.exists("/tmp/test_opt1.wav"):
    list(model.transcribe("/tmp/test_opt1.wav")[0])  # warm CUDA kernels on startup
print(f"whisper daemon ready ({MODEL} on {DEVICE})", flush=True)

if os.path.exists(SOCKET_PATH):
    os.unlink(SOCKET_PATH)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(SOCKET_PATH)
os.chmod(SOCKET_PATH, 0o660)
srv.listen(8)

while True:
    conn, _ = srv.accept()
    with conn:
        fatal_cuda = False
        try:
            req = json.loads(conn.makefile().readline())
            t0 = time.perf_counter()
            segs, _ = model.transcribe(
                req["path"],
                language="en",
                beam_size=1,
                vad_filter=True,
                hotwords=HOTWORDS,
            )
            text = " ".join(s.text.strip() for s in segs).strip()
            res = {"text": text, "ms": round((time.perf_counter() - t0) * 1000)}
        except Exception as e:
            err_s = str(e)
            res = {"error": err_s}
            if any(k in err_s.lower() for k in ("cuda", "out of memory", "device ordinal", "cublas", "cudnn")):
                fatal_cuda = True
        try:
            conn.sendall((json.dumps(res) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError):
            pass
        if fatal_cuda:
            print(f"Fatal CUDA error in whisper_daemon, exiting for systemd restart: {res}", flush=True)
            os._exit(1)
