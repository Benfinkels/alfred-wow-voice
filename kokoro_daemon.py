#!/usr/bin/env python3
"""Warm Kokoro-82M ONNX TTS daemon over a local Unix socket."""
import json, os, signal, socket, sys, time
import onnxruntime as rt
import soundfile as sf
from kokoro_onnx import Kokoro

SOCKET_PATH = os.environ.get("KOKORO_SOCK", "/tmp/kokoro_tts.sock")
KOKORO_DIR = os.environ.get("KOKORO_DIR", os.path.expanduser("~/.openclaw/workspace/kokoro"))
MODEL_PATH = os.path.join(KOKORO_DIR, "kokoro-v1.0.onnx")
VOICES_PATH = os.path.join(KOKORO_DIR, "voices-v1.0.bin")
THREADS = int(os.environ.get("KOKORO_THREADS", "6"))


def cleanup(*_):
    try:
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
    except OSError:
        pass
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    opts = rt.SessionOptions()
    opts.intra_op_num_threads = THREADS
    opts.inter_op_num_threads = 1
    opts.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL

    sess = rt.InferenceSession(MODEL_PATH, sess_options=opts, providers=["CPUExecutionProvider"])
    kokoro = Kokoro(MODEL_PATH, VOICES_PATH)
    kokoro.sess = sess
    kokoro.create("Warmup.", voice="bm_george", speed=1.05)
    print(f"kokoro daemon ready ({THREADS} threads)", flush=True)

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o600)
    server.listen(10)

    try:
        while True:
            conn, _ = server.accept()
            with conn:
                try:
                    line = conn.makefile().readline()
                    if not line:
                        continue
                    req = json.loads(line)
                    text = req.get("text", "").strip()
                    output_path = req.get("output", "")
                    if not text or not output_path:
                        conn.sendall(b'{"error":"Missing text or output"}\n')
                        continue
                    t0 = time.perf_counter()
                    samples, sr = kokoro.create(text, voice=req.get("voice", "bm_george"), speed=float(req.get("speed", 1.05)), lang="en-us")
                    sf.write(output_path, samples, sr)
                    res = {"status": "ok", "inference_ms": round((time.perf_counter() - t0) * 1000, 1), "duration": round(len(samples) / sr, 2)}
                    conn.sendall((json.dumps(res) + "\n").encode())
                except Exception as e:
                    conn.sendall((json.dumps({"error": str(e)}) + "\n").encode())
    finally:
        cleanup()


if __name__ == "__main__":
    main()
