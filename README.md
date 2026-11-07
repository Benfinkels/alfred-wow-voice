# alfred-wow-voice

Hands-free *World of Warcraft: Classic / Forever* voice companion for Discord, built on [OpenClaw](https://github.com/openclaw/openclaw) with local wake-word gating, `<5ms` in-process SQLite RAG, and mid-sentence voice interrupts.

## The Story: Stop Alt-Tabbing Mid-Dungeon

When my friends and I started playing *World of Warcraft* together again, we were constantly `Alt-Tab`bing out of the game to look things up on second monitors — *"Where does this book turn in?"*, *"What's the drop rate on Furlbrow's Deed?"*, *"Where's the closest bow repair in Westfall?"*. Doing that mid-pull or while autorunning across a zone gets old fast.

We wanted someone in the Discord voice channel who could hang out while we chatted for hours, stay completely out of the way during normal conversation, and immediately answer quick game or lore questions out loud whenever anyone asked, *"Hey Alfred..."*.

Because I already had an [OpenClaw](https://github.com/openclaw/openclaw) agent (**Alfred**) running on my local Linux server, turning him into our party's voice companion was remarkably seamless: OpenClaw already handled the Discord bot lifecycle, agent memory, and `stt-tts` audio pipeline out of the box. To make it actually feel good in a live 5-person voice channel, we built this thin local layer around OpenClaw to solve four real-world voice hurdles:

1. **Ignoring hours of party banter (`$0` idle cost):** Five friends talking in a dungeon generate ~400 utterances an hour. [`whisper-stt`](whisper-stt) transcribes each utterance locally on a spare GPU and drops anything not addressed to `"Alfred"` before it ever touches the cloud LLM.
2. **Eliminating cold-start dead air:** Spawning Python to load `faster-whisper` or `kokoro-onnx` on every voice turn adds `4–6s` of silence. Warm Unix-socket daemons ([`whisper_daemon.py`](whisper_daemon.py) & [`kokoro_daemon.py`](kokoro_daemon.py)) plus an instant pre-recorded butler acknowledgement (*"Right away, sir."*) cut local overhead to under a second.
3. **Instant, accurate Classic/Forever answers without web search:** Waiting on live `web_search` tool calls takes `5–10s`, and LLMs frequently confuse Retail/Cataclysm changes with Classic or mix up item-drop quests with NPC pickups. [`wow_db.py`](wow_db.py) normalizes Whisper's phonetic WoW misspellings (*"Nomragon"* → *Gnomeregan*, *"Rawl"* → *Thrall*, *"Dolan R"* → *Dolanaar*) and injects exact Questie coordinates, town-hub compass bearings, weapon/profession trainers, gathering/fishing tables, item stats, and Classic lore into the prompt in `<5ms`.
4. **Barge-in & mid-sentence self-cancellation:** If a boss pull starts while Alfred is explaining a quest chain, saying *"Alfred stop"* or *"Thanks Alfred"* cuts off playback immediately via `/tmp/alfred_voice_ctrl.sock` — and if you change your mind mid-question (*"Hey Alfred, where is... actually never mind"*), `SELF_CANCEL` aborts before calling the LLM.
5. **Companion `/way X, Y` Discord cards & Voice Feature Requests:** Spoken coordinates are hard to type while running from mobs. Whenever Alfred answers a voice question with locations, [`kokoro-tts`](kokoro-tts) automatically posts a companion card to `#bot-commands` with 1-click copyable `/way X, Y` code blocks for `TomTom` / `WaypointUI`. Friends can also say *"Hey Alfred, log a feature request to..."* to file tickets directly into the backlog and `#bot-commands`.

---

## v2 Update: Why We Switched the Voice Model & Upgraded the Router

In v1, every wake turn passed through the full OpenClaw agent running **`MiniMax-M2.7`**. While `MiniMax-M2.7` is great for long-context agentic tasks, live party play exposed three bottlenecks that drove our v2 architecture:

1. **Eliminating Reasoning-Token Latency (`2.8s` → `~750ms`):**
   - `MiniMax-M2.7` is a thinking model that generates `1,000–2,200` internal `<think>` tokens before emitting a 25-word spoken reply, adding `2.2–3.2s` of dead air after STT.
   - Moving voice synthesis inside [`whisper-stt`](whisper-stt) (`<<FAST_LOCAL_REPLY>>`) and switching the cloud voice tier to **`gemini-3.5-flash-lite`** cut cloud LLM latency to **`700–850 ms`** (`3x` faster) at **`$0.00034` per voice turn** (~58,000 voice turns on a `$20` Google Cloud credit), while keeping regular OpenClaw text chats and background tasks on our flat-rate `MiniMax-M2.7` subscription (`$0` Gemini spend outside voice).
2. **High-Confidence JEV Router (Local `Gemma 4 E2B` vs. `gemini-3.5-flash-lite`):**
   - We run a warm local **`gemma4:e2b-it-q4_K_M`** (`100% GPU` on an RTX 2080, `~600 ms`, `$0`) alongside **`gemini-3.5-flash-lite`**.
   - Small `2B` local models excel when given an exact single-record fact (`"Where is the closest bow vendor in Loch Modan?"`), but struggle on open-ended strategy or multi-continent Classic travel routes (*"Should we level in Teldrassil or skip it for Elwynn Forest, and how do we get there?"*).
   - Our `is_simple_wow_lookup()` gate routes narrow single-entity lookups (`<= 18` words with an exact entity/role match in the top 4 SQLite rows) to local `Gemma 4 E2B` (with automatic `ESCALATE` fallback), and routes broad strategy, multi-zone comparisons, and navigational directions directly to `gemini-3.5-flash-lite`.
3. **Fixing "In-Town" Coordinate Hallucinations with Deterministic Compass Math:**
   - During live testing in Teldrassil, a player asked *"Where is the nearest leatherworking trainer to Dolanaar?"* QuestieDB listed `Nadyia Maneweaver` at `Teldrassil (41.9, 49.4)`. Because the DB row only said `Teldrassil (41.9, 49.4)` without Dolanaar's own center (`56.3, 59.8`), the LLM assumed `(41.9, 49.4)` was *inside* Dolanaar—and when the player couldn't find her in town, the dry British butler persona quipped: *"Sir, Nadyia Maneweaver can be found at coordinates 41.9, 49.4 in Dolanaar. Do try opening your eyes next time."*
   - In reality, `(41.9, 49.4)` is a small roadside pavilion **18 map units northwest of Dolanaar** along the road to Darnassus. [`wow_db.py`](wow_db.py) now includes `TOWN_HUB_COORDS` for 25 Classic town hubs and computes Euclidean distance + compass bearing for every returned NPC (`[INSIDE Dolanaar (56.3, 59.8)]` vs. `[OUTSIDE Dolanaar: ~18 map units northwest of Dolanaar (56.3, 59.8)]`).
4. **Multi-Addon Ingestion (`37.5 MB` `wow_forever.sqlite`):**
   - Beyond `QuestieDB`, [`build_wow_db.py`](build_wow_db.py) now compiles **`WhatsTraining`** (all 12 profession recipe tables + exact Weapon Master trainers per city/class), **`GatherLite`** (herb, mining, chest, and fishing node coordinates + fish catch rates and zone fishing skill requirements), **`Spoken_Places` & `Spoken_Bosses`** (subzone & boss voiceover lore), **`AtlasLootClassic`** (boss loot tables & drop rates), and **`wow-classic-items`** (`21,435` item tooltips with weapon DPS, speed, armor, and equip stats).
5. **Three-Layer Multiplayer Voice Prompt-Injection Defense:**
   - Because Discord voice cannot authenticate which friend in the channel is speaking, [`whisper-stt`](whisper-stt) enforces: (a) a pre-LLM `INJECTION_GUARD` blocking jailbreaks, system prompts, shell/file commands, and smart-home/appliance probing, (b) `<voice_transcript>` XML delimiter sandboxing on all fast voice calls (which have zero tools attached), and (c) a mandatory `[SECURITY BOUNDARY]` tool-lockdown envelope whenever a request for a public addon/web link is handed to OpenClaw (`MiniMax-M2.7`).

## Sample Responses (Live Discord Sessions)

Real voice queries captured from party chat, grounded against `wow_forever.sqlite` in `<5ms`, and synthesized locally with `Kokoro-82M` (`bm_george` @ `1.12x`):

| Player Voice Query (Whisper STT) | Local Pipeline Action | Alfred's Spoken Response | Audio |
| :--- | :--- | :--- | :--- |
| *"Hey Alfred, what is the closest vendor in Loch Modan when I'm near Iron Band's excavation site?"* | Ranks Loch Modan `<Tradesman>`, `<Metalsmith>`, and `<General Supplies>` NPCs by spawn coordinates | *"The dig site in Loch Modan sits right around 35.6, 48.9, and Drac Roughcut the Tradesman is standing essentially on it at 35.6, 49.1 — that's your closest vendor for general goods and repairs..."* | [🔊 `loch_modan_vendor.mp3`](samples/loch_modan_vendor.mp3) |
| *"Hey Alfred, what do I do with the book Rumi of Nomragon, The Collected Works?"* | Normalizes `"Nomragon"` → `"Gnomeregan"`, injects quest starters (`Owen Thadd` / `Garion Wendell`) + object spawns | *"Turn it in, sir — the book starts the Rumi of Gnomeregan quest, which you can hand to Owen Thadd in the Undercity or to Garion Wendell in Stormwind City..."* | [🔊 `rumi_gnomeregan.mp3`](samples/rumi_gnomeregan.mp3) |
| *"Hey, Alfred, what's the drop rate on the Furlbrose deed?"* | Normalizes `"Furlbrose"` → `"Furlbrow"`, pulls Questie item-drop table (`Westfall Deed`) + learned correction | *"Furlbrow's Deed — that's the Westfall Deed, and it drops from Defias mobs in Elwynn Forest at about 2.5%, roughly 3,825 drops in 152,425 kills..."* | [🔊 `furlbrows_deed.mp3`](samples/furlbrows_deed.mp3) |
| *"Alfred, are you only saying that because he threatened to turn you off?"* | Wake-word match (`"Alfred, ..."`) | *"Entirely my own judgment, sir. Though I won't pretend the threat of shutdown hasn't sharpened my etiquette over the years."* | [🔊 `butler_etiquette.mp3`](samples/butler_etiquette.mp3) |

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

| Stage | Hardware / Model | Warm Latency | Cost |
| :--- | :--- | :--- | :--- |
| **STT (`whisper_daemon.py`)** | GTX 1080 8GB (`int8`, `distil-large-v3` + WoW `hotwords`) | `350–520 ms` | `$0` |
| **Wake / Interrupt Gate + SQLite FTS5 RAG (`wow_db.py`)** | CPU (in-process SQLite `mode=ro`, `37.5 MB` multi-addon DB) | `4–25 ms` | `$0` |
| **Tier 0 Local Voice LLM (`call_local_fast_butler`)** | RTX 2080 8GB (`gemma4:e2b-it-q4_K_M`, `100% GPU`) | `550–680 ms` | `$0` |
| **Tier 1 Cloud Voice LLM (`call_gemini_butler`)** | `gemini-3.5-flash-lite` (multi-zone / strategy / fallback) | `680–850 ms` | `$0.00034 / turn` |
| **Voice Interrupt Cut-Off (`voice-ctrl stop`)** | Local Unix socket (`/tmp/alfred_voice_ctrl.sock`) | `<10 ms` after STT | `$0` |
| **TTS + `#bot-commands` `/way` Card (`kokoro-tts`)** | 6 CPU threads (`kokoro-v1.0.onnx`, `bm_george`) | `200–450 ms` | `$0` |

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
