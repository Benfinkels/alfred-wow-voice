#!/usr/bin/env python3
"""
Idempotent patcher for @openclaw/discord's voice-runtime bundle:
1. Instant pre-recorded butler acknowledgement WAV while the LLM researches.
2. Unblocks user audio capture during bot playback (safe because whisper-stt gates wake/interrupts).
3. Exposes a mode-0600 Unix control socket (/tmp/alfred_voice_ctrl.sock) for <10ms voice interrupts and CLI channel joins.
"""
import glob, os, shutil, sys

SEARCH_GLOBS = [
    os.path.expanduser("~/.openclaw/npm/projects/*/node_modules/@openclaw/discord/dist/.setup"),
    os.path.expanduser("~/.npm-global/lib/node_modules/openclaw/node_modules/@openclaw/discord/dist/.setup"),
]

CTRL_SERVER_SNIPPET = """var DiscordVoiceManager = class {
\tconstructor(params) {
\t\tglobalThis.__alfredVoiceManager = this;
\t\tif (!globalThis.__alfredVoiceCtrlServer) {
\t\t\tglobalThis.__alfredVoiceCtrlServer = true;
\t\t\tPromise.all([import("node:net"), import("node:fs")]).then(([{ createServer }, { unlinkSync, chmodSync }]) => {
\t\t\t\ttry { unlinkSync("/tmp/alfred_voice_ctrl.sock"); } catch {}
\t\t\t\tconst srv = createServer((sock) => {
\t\t\t\t\tsock.on("data", async (buf) => {
\t\t\t\t\t\tconst cmd = buf.toString().trim();
\t\t\t\t\t\tif (cmd.startsWith("join ")) {
\t\t\t\t\t\t\tconst [, guildId, channelId] = cmd.split(/\\s+/);
\t\t\t\t\t\t\tconst res = await globalThis.__alfredVoiceManager?.join({ guildId, channelId }).catch((e) => ({ ok: false, error: String(e) }));
\t\t\t\t\t\t\tsock.end(JSON.stringify(res ?? { ok: false }) + "\\n");
\t\t\t\t\t\t\treturn;
\t\t\t\t\t\t}
\t\t\t\t\t\tlet playing = false;
\t\t\t\t\t\tfor (const t of globalThis.__alfredTransports ?? []) {
\t\t\t\t\t\t\tif (!t.terminal && (t.playerStatus === "playing" || t.files.size > 0)) {
\t\t\t\t\t\t\t\tplaying = true;
\t\t\t\t\t\t\t\tif (cmd === "stop") {
\t\t\t\t\t\t\t\t\tt.send({ type: "player-stop" });
\t\t\t\t\t\t\t\t\tfor (const p of t.files.values()) { p.abort.abort(); p.drain?.(); p.resolve(); }
\t\t\t\t\t\t\t\t\tt.files.clear();
\t\t\t\t\t\t\t\t}
\t\t\t\t\t\t\t}
\t\t\t\t\t\t}
\t\t\t\t\t\tif (cmd === "stop") globalThis.__alfredLastInterruptTs = Date.now();
\t\t\t\t\t\tsock.end(JSON.stringify({ ok: true, playing, sessions: globalThis.__alfredVoiceManager?.status() ?? [] }) + "\\n");
\t\t\t\t\t});
\t\t\t\t});
\t\t\t\tsrv.on("error", () => {});
\t\t\t\tsrv.listen("/tmp/alfred_voice_ctrl.sock", () => { try { chmodSync("/tmp/alfred_voice_ctrl.sock", 0o600); } catch {} });
\t\t\t}).catch(() => {});
\t\t}
\t\tthis.sessions = /* @__PURE__ */ new Map();"""


def find_setup_dir() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    for pattern in SEARCH_GLOBS:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    sys.exit("Could not locate @openclaw/discord/dist/.setup; pass its path as argv[1].")


def patch():
    setup_dir = find_setup_dir()
    vr_matches = [p for p in glob.glob(os.path.join(setup_dir, "voice-runtime-*.mjs")) if not p.endswith(".orig")]
    rt_matches = [p for p in glob.glob(os.path.join(setup_dir, "runtime-*.mjs")) if not p.endswith(".orig")]
    if not vr_matches or not rt_matches:
        sys.exit(f"Missing voice-runtime-*.mjs or runtime-*.mjs in {setup_dir}")
    vr_path, rt_path = vr_matches[0], rt_matches[0]

    for p in (vr_path, rt_path):
        if not os.path.exists(p + ".orig"):
            shutil.copy2(p, p + ".orig")

    # 1. Patch runtime-*.mjs so getDiscordRuntime() works from Unix socket callbacks
    rt_src = open(rt_path, encoding="utf-8").read()
    if "__discordRuntimeFallback" not in rt_src:
        open(rt_path, "w", encoding="utf-8").write(
            'import { createPluginRuntimeStore } from "openclaw/plugin-sdk/runtime-store";\n'
            'const { setRuntime: _set, tryGetRuntime: _tryGet, getRuntime: _get } = createPluginRuntimeStore({\n'
            '\tpluginId: "discord",\n'
            '\terrorMessage: "Discord runtime not initialized"\n'
            '});\n'
            'const setDiscordRuntime = (r) => { globalThis.__discordRuntimeFallback = r; return _set(r); };\n'
            'const getOptionalDiscordRuntime = () => _tryGet() ?? globalThis.__discordRuntimeFallback ?? null;\n'
            'const getDiscordRuntime = () => _tryGet() ?? globalThis.__discordRuntimeFallback ?? _get();\n'
            'export { getOptionalDiscordRuntime as n, setDiscordRuntime as r, getDiscordRuntime as t };\n'
        )

    # 2. Patch voice-runtime-*.mjs from clean .orig base for idempotency
    vr_src = open(vr_path + ".orig", encoding="utf-8").read()
    vr_src = vr_src.replace(
        "async function respondToDiscordVoiceTranscript(params) {\n\tconst { entry, ingress, transcript, userId } = params;",
        "async function respondToDiscordVoiceTranscript(params) {\n\tconst { entry, ingress, transcript, userId } = params;\n\tconst __turnStartedAt = Date.now();",
    )
    if "ALFRED_ACK_PATCH" not in vr_src:
        ack_hook = (
            '\t} else {\n'
            '\t\t/* ALFRED_ACK_PATCH */\n'
            '\t\ttry {\n'
            '\t\t\tconst ackDir = (process.env.HOME || "/tmp") + "/.openclaw/voice-acks";\n'
            '\t\t\tconst acks = (await import("node:fs")).readdirSync(ackDir).filter((f) => f.endsWith(".wav"));\n'
            '\t\t\tif (acks.length && entry.sessionLifecycle.status !== "stopped") {\n'
            '\t\t\t\tconst recent = (globalThis.__recentAcks ??= []); const pool = acks.filter((a) => !recent.includes(a));\n'
            '\t\t\t\tconst chosen = (pool.length ? pool : acks)[Math.floor(Math.random() * (pool.length || acks.length))];\n'
            '\t\t\t\trecent.push(chosen); if (recent.length > 6) recent.shift();\n'
            '\t\t\t\tparams.enqueuePlayback(entry, async () => { if (entry.sessionLifecycle.status !== "stopped") await entry.audio.play(ackDir + "/" + chosen); });\n'
            '\t\t\t}\n'
            '\t\t} catch {}\n'
        )
        vr_src = vr_src.replace("\t} else {\n\t\tconst prompt = formatVoiceIngressPrompt", ack_hook + "\t\tconst prompt = formatVoiceIngressPrompt")
    vr_src = vr_src.replace(
        "\tparams.enqueuePlayback(entry, async () => {\n\t\ttry {\n\t\t\tif (entry.sessionLifecycle.status === \"stopped\") return;",
        "\tparams.enqueuePlayback(entry, async () => {\n\t\ttry {\n\t\t\tif (entry.sessionLifecycle.status === \"stopped\") return;\n\t\t\tif ((globalThis.__alfredLastInterruptTs ?? 0) > __turnStartedAt) return;",
    )
    vr_src = vr_src.replace(
        'const playing = entry.audio.playerStatus === "playing";',
        'const playing = false; /* ALFRED_INTERRUPT_PATCH */',
    )
    vr_src = vr_src.replace(
        'const protectedPlayback = () => entry.audio.playerStatus === "playing" && !realtime?.canReceiveDuringPlayback();',
        'const protectedPlayback = () => false; /* ALFRED_INTERRUPT_PATCH */',
    )
    vr_src = vr_src.replace(
        "var DiscordAudioTransport = class extends EventEmitter {\n\tconstructor(options, adapterCreator) {\n\t\tsuper();\n\t\tthis.speakingUsers",
        "var DiscordAudioTransport = class extends EventEmitter {\n\tconstructor(options, adapterCreator) {\n\t\tsuper();\n\t\t(globalThis.__alfredTransports ??= new Set()).add(this);\n\t\tthis.speakingUsers",
    )
    vr_src = vr_src.replace(
        "\tfinish(error, drained = false) {\n\t\tif (this.terminal) return;",
        "\tfinish(error, drained = false) {\n\t\tglobalThis.__alfredTransports?.delete(this);\n\t\tif (this.terminal) return;",
    )
    vr_src = vr_src.replace(
        "var DiscordVoiceManager = class {\n\tconstructor(params) {\n\t\tthis.sessions = /* @__PURE__ */ new Map();",
        CTRL_SERVER_SNIPPET,
    )

    required = [
        "ALFRED_ACK_PATCH",
        "const __turnStartedAt = Date.now();",
        "__alfredLastInterruptTs",
        "ALFRED_INTERRUPT_PATCH",
        "__alfredTransports",
        "/tmp/alfred_voice_ctrl.sock",
    ]
    missing = [m for m in required if m not in vr_src]
    if missing:
        sys.exit(f"ERROR: patch target mismatch in {vr_path}; missing markers: {missing}")

    open(vr_path, "w", encoding="utf-8").write(vr_src)
    print(f"Patched {vr_path} and {rt_path}")


if __name__ == "__main__":
    patch()
