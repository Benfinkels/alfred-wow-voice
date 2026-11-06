# alfred-wow-voice

Local wake-word gating, `<5ms` in-process SQLite RAG, and mid-sentence voice interrupts for [OpenClaw](https://github.com/openclaw/openclaw) Discord voice channels.

Built to run an always-present *World of Warcraft: Classic / Forever* butler (**Alfred**) inside a live Discord raid channel without burning cloud LLM tokens on background party chatter or hallucinating quest/lore facts.

## Sample Responses (Live Discord Sessions)

Real voice queries captured from party chat, grounded against `wow_forever.sqlite` in `<5ms`, and synthesized locally with `Kokoro-82M` (`bm_george` @ `1.12x`):

| Player Voice Query (Whisper STT) | Local Pipeline Action | Alfred's Spoken Response | Audio |
| :--- | :--- | :--- | :--- |
| *"Hey Alfred, what is the closest vendor in Loch Modan when I'm near Iron Band's excavation site?"* | Ranks Loch Modan `<Tradesman>`, `<Metalsmith>`, and `<General Supplies>` NPCs by spawn coordinates | *"The dig site in Loch Modan sits right around 35.6, 48.9, and Drac Roughcut the Tradesman is standing essentially on it at 35.6, 49.1 — that's your closest vendor for general goods and repairs..."* | [🔊 `loch_modan_vendor.mp3`](samples/loch_modan_vendor.mp3) |
| *"Hey Alfred, what do I do with the book Rumi of Nomragon, The Collected Works?"* | Normalizes `"Nomragon"` → `"Gnomeregan"`, injects quest starters (`Owen Thadd` / `Garion Wendell`) + object spawns | *"Turn it in, sir — the book starts the Rumi of Gnomeregan quest, which you can hand to Owen Thadd in the Undercity or to Garion Wendell in Stormwind City..."* | [🔊 `rumi_gnomeregan.mp3`](samples/rumi_gnomeregan.mp3) |
| *"Hey, Alfred, what's the drop rate on the Furlbrose deed?"* | Normalizes `"Furlbrose"` → `"Furlbrow"`, pulls Questie item-drop table (`Westfall Deed`) + learned correction | *"Furlbrow's Deed — that's the Westfall Deed, and it drops from Defias mobs in Elwynn Forest at about 2.5%, roughly 3,825 drops in 152,425 kills..."* | [🔊 `furlbrows_deed.mp3`](samples/furlbrows_deed.mp3) |
| *"Alfred, are you only saying that because he threatened to turn you off?"* | Wake-word match (`"Alfred, ..."`) | *"Entirely my own judgment, sir. Though I won't pretend the threat of shutdown hasn't sharpened my etiquette over the years."* | [🔊 `butler_etiquette.mp3`](samples/butler_etiquette.mp3) |

## Why this exists

Out of the box, hooking a Discord voice channel up to a cloud LLM in `stt-tts` mode has four failure modes:
1. **Every cough and callout hits the LLM:** 5 players talking in a dungeon generate ~400 utterances/hour. Without a local wake-word gate, 95% of turns are wasted tokens.
2. **Cold-start CLI latency:** Spawning Python to load `faster-whisper` or `kokoro-onnx` per utterance adds `4–6s` of dead air before the LLM even starts.
3. **Slow & hallucinated game lookups:** Waiting on `web_search` tool calls adds `5–10s` per voice turn, and frontier models still confuse NPC quest-starters with item-drop starters (e.g. *Furlbrow's Deed*) or mix Retail/Cataclysm lore into Classic.
4. **Unstoppable monologues:** OpenClaw's default `protectedPlayback` guard deafens the bot while its audio player is active, making it impossible to tell the bot to stop talking mid-paragraph.

## Architecture

```text
Discord Voice Frame (Opus -> PCM WAV)
  │
  ▼
whisper-stt --wake <wav>
  ├─► /tmp/whisper_stt.sock (warm faster-whisper distil-large-v3 on GTX 1080, ~400ms)
  ├─► classify(text):
  │     • IGNORE (no wake word / 3rd-person mention) ──► exit("")  [$0, 0 LLM tokens]
  │     • INTERRUPT ("Alfred stop" / "thanks Alfred") ─► /tmp/alfred_voice_ctrl.sock ("stop") + exit("")
  │     • WAKE ("Hey Alfred, ...") ────────────────────► normalize_stt() + wow_db.auto_context() (<5ms)
  │
  ▼
OpenClaw Discord Voice Runtime (patched via patch_openclaw_voice.py)
  ├─► Plays instant pre-recorded butler acknowledgement ("Right away, sir.")
  ├─► Sends user transcript + inline [Verified Local WoW Forever QuestieDB Data] to LLM
  │
  ▼
kokoro-tts --output <wav>
  ├─► speakable(text): strips URLs/citations/markdown, fixes decimal/comma phoneme splits
  ├─► Logs automatic self-corrections to ~/.openclaw/logs/corrections.jsonl
  └─► /tmp/kokoro_tts.sock (warm Kokoro-82M ONNX bm_george, ~250ms)
```

## Measured Latency & Footprint

| Stage | Hardware | Warm Latency | Cost |
| :--- | :--- | :--- | :--- |
| **STT (`whisper_daemon.py`)** | GTX 1080 8GB (`int8`, `distil-large-v3` + WoW `hotwords`) | `350–520 ms` | `$0` |
| **Wake / Interrupt Gate + SQLite FTS5 RAG (`wow_db.py`)** | CPU (in-process SQLite `mode=ro`) | `4–15 ms` | `$0` |
| **Voice Interrupt Cut-Off (`voice-ctrl stop`)** | Local Unix socket (`/tmp/alfred_voice_ctrl.sock`) | `<10 ms` after STT | `$0` |
| **TTS (`kokoro_daemon.py`)** | 6 CPU threads (`kokoro-v1.0.onnx`, `bm_george`) | `200–450 ms` | `$0` |

## Files

* [`whisper-stt`](whisper-stt) & [`whisper_daemon.py`](whisper_daemon.py) — Warm `faster-whisper` socket daemon and wake/interrupt/quota gate.
* [`wow_db.py`](wow_db.py) — Zero-dependency SQLite FTS5 lookup engine with Whisper phonetic normalization (`Rawl -> Thrall`, `Silvanus -> Sylvanas`, `Furlbrose -> Furlbrow`), town-to-zone mapping (`Goldshire -> Elwynn Forest`, `Thelsamar -> Loch Modan`), service/vendor NPC ranking, and learned-correction injection.
* [`build_wow_db.py`](build_wow_db.py) — Pure-Python RFC 8949 CBOR decoder that compiles Questie's Lua/CBOR tables (`quests`, `npcs`, `items`, `objects`, and drop rates) into `wow_forever.sqlite`.
* [`build_wow_lore.py`](build_wow_lore.py) — Pulls 444 Warcraft Wiki (`warcraft.wiki.gg`) articles + 42 Classic in-game lore books into `lore` / `lore_fts`, scrubbing post-Classic/Cataclysm sentences.
* [`kokoro-tts`](kokoro-tts) & [`kokoro_daemon.py`](kokoro_daemon.py) — Warm Kokoro-82M ONNX TTS daemon, speech text sanitizer, and automatic self-correction logger.
* [`patch_openclaw_voice.py`](patch_openclaw_voice.py) & [`voice-ctrl`](voice-ctrl) — Idempotent patcher for `@openclaw/discord` enabling instant ack playback, capture during bot speech, and `/tmp/alfred_voice_ctrl.sock` (`status`, `stop`, `join <guildId> <channelId>`).
* [`voice_replay.py`](voice_replay.py) & [`voice-audit`](voice-audit) — Offline regression suite (`<1s`, `$0` token spend) and live audit log viewer.

## Setup

### 1. Build the local Questie + Lore SQLite database
```bash
# Compile Questie Lua/CBOR tables into wow_forever.sqlite
QUESTIE_ADDON_DIR="/path/to/Interface/AddOns/QuestieDB" python3 build_wow_db.py

# Fetch & index 444 Classic-filtered Warcraft Wiki lore articles
python3 build_wow_lore.py

# Run self-check and offline voice regression suite
python3 wow_db.py --self-check
python3 voice_replay.py
```

### 2. Start the warm STT & TTS daemons
Copy [`systemd/whisper-stt.service`](systemd/whisper-stt.service) and [`systemd/kokoro-tts.service`](systemd/kokoro-tts.service) to `~/.config/systemd/user/` and enable them:
```bash
systemctl --user daemon-reload
systemctl --user enable --now whisper-stt.service kokoro-tts.service
```

### 3. Patch `@openclaw/discord` and configure OpenClaw
```bash
python3 patch_openclaw_voice.py
./kokoro-tts --gen-acks
```
Merge [`examples/openclaw.voice.json`](examples/openclaw.voice.json) into `~/.openclaw/openclaw.json` and copy [`examples/SOUL.md`](examples/SOUL.md) into your OpenClaw workspace.

### 4. Control voice channels & inspect logs from CLI
```bash
./voice-ctrl status
./voice-ctrl join <guildId> <channelId>
./voice-ctrl stop
./voice-audit 30
./voice-audit --corrections
```

## Production Reference vs. Untested Extension Hooks

### How we run this in production
We run this stack on a dedicated Linux home server (**Ubuntu 24.04**, dual Pascal/Turing GPUs + 6 CPU threads) connected to a live Discord voice channel:
* **STT:** `whisper_daemon.py` running `distil-large-v3` (`int8`) pinned to an **NVIDIA GTX 1080 (8 GB)** via `CUDA_VISIBLE_DEVICES=1`.
* **TTS:** `kokoro_daemon.py` running `kokoro-v1.0.onnx` (`bm_george` British butler voice) on **6 CPU threads** (`ORT_SEQUENTIAL`).
* **Knowledge Base:** `wow_forever.sqlite` (`~30 MB`, 12,000+ Questie quests/NPCs/items/objects + 444 Classic-filtered Warcraft Wiki lore articles) queried in-process by `whisper-stt` in `<5 ms`.
* **Agent Runtime:** OpenClaw (`@openclaw/discord` in `stt-tts` mode) calling **MiniMax-M2.7** with the inline `[Verified Local WoW Forever QuestieDB Data]` block attached to each wake turn.

### Adapting for your own bot or non-OpenClaw agent (untested live)
Because `whisper-stt` (`wav -> stdout`) and `kokoro-tts` (`text -> wav`) are plain Unix CLI filters, we exposed three environment variable overrides so you can connect a differently named bot, a non-WoW knowledge base, or a non-OpenClaw voice framework. **Note:** These overrides are verified via offline unit tests (`voice_replay.py`), not live-tested against external agent frameworks:
* `VOICE_WAKE_NAME` / `VOICE_WAKE_PATTERN` — Change the wake word from `"Alfred"` (e.g. `VOICE_WAKE_NAME="Jarvis"`).
* `VOICE_CONTEXT_CMD` — Replace `wow_db.auto_context()` with any shell command that reads the user transcript on `stdin` and prints ground-truth context on `stdout` (or leave `wow_forever.sqlite` absent to run with zero RAG).
* `VOICE_INTERRUPT_CMD` — Replace OpenClaw's `/tmp/alfred_voice_ctrl.sock` stop command with a custom shell command (e.g. `pkill -f ffplay` or an HTTP webhook) when a user says `"<Bot> stop"`.

## License
MIT
