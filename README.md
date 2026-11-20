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

## v3 Update: `AlfredBridge` In-Game AddOn, `LoreForever` 25 ADP & `alfred-v2` Gemma 4 QLoRA

Building on the v2 hybrid router, v3 introduces live in-game client integration, Year 25 After the Dark Portal lore grounding, and a clean QLoRA fine-tune on stock **Gemma 4 E4B IT**:

1. **`AlfredBridge` WoW Forever AddOn (`addon/AlfredBridge/` + `bridge/`):**
   - **Live Telemetry & Zero-Install Party Awareness:** Encodes player zone, subzone, `(x, y)` coordinates, target + NPC ID, active quest watches, and `party1..4` roster into a tiny `128`-cell (`256×2` physical px) top-left optical strip (`0xA1F2` header + `CRC16-CCITT`, hidden after `2.5s` and suppressed on `SCREENSHOT_STARTED`). Even if only one party member runs [`alfred_bridge_relay.py`](bridge/alfred_bridge_relay.py), Alfred sees all `party1..4` levels, classes, and zones. When a player asks *"Hey Alfred, what quests does this guy give?"* without naming the zone or NPC, `enrich_query_with_bridge()` resolves their live target and zone automatically.
   - **Direct `WaypointUI` 3D Arrow Injection:** Whenever Alfred speaks coordinates out loud, [`kokoro-tts`](kokoro-tts) notifies [`sheldon_bridge_daemon.py`](bridge/sheldon_bridge_daemon.py) (`:8098`). The Windows relay writes an on-demand pin slot (`AlfredBridge_Pin1..20`), copies a `/way` command to the clipboard, and triggers `WaypointUIAPI.Navigation.NewUserNavigation(...)` via `CTRL-ALT-F10` so a 3D arrow appears immediately in-game.
   - **Automatic `Spoken` Audio Pause / Resume:** When Alfred begins speaking in Discord, `AlfredBridge` pauses active `Spoken` NPC voiceover (`Spoken:Pause()` via `CTRL-ALT-F11`) and automatically resumes it (`Spoken:Resume()` via `CTRL-ALT-F12`) as soon as Alfred finishes or is interrupted (*"Alfred stop"*).
   - **Pull-Only by Default (`/ab moments`):** Proactive commentary on level-ups, boss kills, and dungeon wipes is strictly opt-in (`AlfredBridgeDB.moments = false`; toggle anytime with `/alfred moments` or `/ab status`).
2. **`alfred-v2` Stock Gemma 4 E4B QLoRA (`training/`):**
   - Replaces generic zero-shot prompting with a dedicated QLoRA fine-tune of **`unsloth/gemma-4-E4B-it-unsloth-bnb-4bit`** exported to `Q4_K_M` GGUF (`~5.3 GB`, `100% GPU` on an 8 GB RTX 2080) using Gemma 4's native `<|turn>...<turn|>` chat template ([`Modelfile.alfred-v2`](training/Modelfile.alfred-v2)).
   - Trained on `1,007` curated turns generated by [`build_alfred_v2_dataset.py`](training/build_alfred_v2_dataset.py) across four narrow local tasks: `local_answer_ok`-verified WoW Forever lookups, honest `ESCALATE` calibration on near-miss roles/quest chains, dry Michael Caine butler banter, and 1-line feature-request ticket titles.
3. **Speaker Attribution, Multimodal `#bot-commands` Reader & Shared Party Blackboard:**
   - [`patch_openclaw_voice.py`](patch_openclaw_voice.py) writes per-utterance speaker metadata (`<wav>.meta.json`) so Alfred addresses friends by name and maintains a `$0` shared [`party_state.json`](whisper-stt) blackboard (*"Remember we're running Shadowfang Keep tonight"*, *"Pin 37.5, 50.7 as Cozy Sleeping Bag"*).
   - Saying *"Hey Alfred, check the link/screenshot in chat"* pulls recent `#bot-commands` URLs and item tooltip screenshots directly into Gemini (`url_context` + `inline_data`).
4. **GPU Kokoro TTS (`6.6x` Faster) & `LoreForever` Year 25 ADP Corpus (`3,313` Lore Entries):**
   - Moving [`kokoro_daemon.py`](kokoro_daemon.py) onto GTX 1080 CUDA (`onnxruntime-gpu`) cut long-paragraph TTS synthesis from `5,770 ms` on CPU to **`598–876 ms`**.
   - [`ingest_loreforever.py`](ingest_loreforever.py) indexes the official **LoreForever** Year 25 ADP corpus (`2,290` new entries; `3,313` total lore rows) while scrubbing post-Classic Retail/Cataclysm/Shadowlands leaks.

---

## v2 Architecture Notes: Hybrid Router & Compass Math

In v1, every wake turn passed through the full OpenClaw agent running **`MiniMax-M2.7`**. Live party play drove four core architectural decisions retained in v3:

1. **Eliminating Reasoning-Token Latency (`2.8s` → `~750ms`):**
   - `MiniMax-M2.7` generates `1,000–2,200` internal `<think>` tokens before emitting a 25-word spoken reply, adding `2.2–3.2s` of dead air after STT.
   - Moving voice synthesis inside [`whisper-stt`](whisper-stt) (`<<FAST_LOCAL_REPLY>>`) and routing complex/grounded WoW turns to **`gemini-2.5-flash`** (with prompt-cached `STATIC_BUTLER_SYSTEM`) cut cloud LLM latency to **`~1.0s`** while keeping regular OpenClaw text chats on `MiniMax-M2.7`.
2. **Verify-Then-Escalate JEV Router (Local `alfred-v2` vs. Cloud Gemini):**
   - Narrow single-entity lookups (`is_simple_wow_lookup()`) and raid banter run locally on **`alfred-v2:latest`** (`100% GPU` on RTX 2080, `$0`), verified deterministically by `local_answer_ok()` before speaking, with automatic fallback to Gemini on `ESCALATE` or coordinate/role mismatch.
3. **Fixing "In-Town" Coordinate Hallucinations with Deterministic Compass Math:**
   - [`wow_db.py`](wow_db.py) includes `TOWN_HUB_COORDS` for 25 Classic town hubs and computes Euclidean distance + compass bearing for every returned NPC (`[INSIDE Dolanaar (56.3, 59.8)]` vs. `[OUTSIDE Dolanaar: ~18 map units northwest of Dolanaar (56.3, 59.8)]`).
4. **Multi-Addon Ingestion & Three-Layer Multiplayer Voice Prompt-Injection Defense:**
   - [`build_wow_db.py`](build_wow_db.py) compiles `QuestieDB`, `WhatsTraining`, `GatherLite`, `Spoken_Places`/`Spoken_Bosses`, `AtlasLootClassic`, and `wow-classic-items` (`21,435` item tooltips), while [`whisper-stt`](whisper-stt) enforces `INJECTION_GUARD`, `<voice_transcript>` XML sandboxing, and `[SECURITY BOUNDARY]` tool lockdown.

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
| **Wake / Interrupt Gate + SQLite FTS5 RAG (`wow_db.py`)** | CPU (in-process SQLite `mode=ro`, `3,313` 25 ADP lore + Questie/AddOns) | `4–25 ms` | `$0` |
| **Tier 0 Local Voice LLM (`call_local_fast_butler` / Banter)** | RTX 2080 8GB (`alfred-v2:latest` Gemma 4 E4B QLoRA `Q4_K_M`, `100% GPU`) | `450–700 ms` | `$0` |
| **Tier 1 Cloud Voice LLM (`call_gemini_butler`)** | `gemini-2.5-flash` (grounded strategy / multi-zone / `#bot-commands` vision) | `850–1,150 ms` | `~$0.0005 / turn` |
| **Voice Interrupt Cut-Off (`voice-ctrl stop`)** | Local Unix socket (`/tmp/alfred_voice_ctrl.sock`) + `Spoken:Resume()` | `<10 ms` after STT | `$0` |
| **TTS + `#bot-commands` `/way` + `AlfredBridge` Notify (`kokoro-tts`)** | GTX 1080 8GB CUDA (`kokoro-v1.0.onnx`, `bm_george`) | `180–870 ms` | `$0` |

## Files

* [`whisper-stt`](whisper-stt) & [`whisper_daemon.py`](whisper_daemon.py) — Warm `faster-whisper` socket daemon, speaker attribution, `$0` party blackboard, live `AlfredBridge` query enrichment, and JEV Verify-Then-Escalate router.
* [`addon/AlfredBridge/`](addon/AlfredBridge) & [`bridge/`](bridge) — **WoW Forever AddOn** (`Interface: 16001`), optical strip relay ([`alfred_bridge_relay.py`](bridge/alfred_bridge_relay.py)), Sheldon bridge daemon ([`sheldon_bridge_daemon.py`](bridge/sheldon_bridge_daemon.py)), installer/release packager ([`install_addon.py`](bridge/install_addon.py)), and deterministic test suite ([`test_bridge.py`](bridge/test_bridge.py)).
* [`training/`](training) — **Unsloth QLoRA pipeline for `alfred-v2`**: dataset builder ([`build_alfred_v2_dataset.py`](training/build_alfred_v2_dataset.py)), QLoRA + `Q4_K_M` GGUF trainer ([`train_alfred_v2_qlora.py`](training/train_alfred_v2_qlora.py)), and Ollama [`Modelfile.alfred-v2`](training/Modelfile.alfred-v2).
* [`wow_db.py`](wow_db.py), [`build_wow_db.py`](build_wow_db.py), [`build_wow_lore.py`](build_wow_lore.py) & [`ingest_loreforever.py`](ingest_loreforever.py) — Zero-dependency SQLite FTS5 lookup engine, multi-addon CBOR compiler, Classic wiki scraper, and Year 25 ADP `LoreForever` ingester.
* [`kokoro-tts`](kokoro-tts) & [`kokoro_daemon.py`](kokoro_daemon.py) — GPU-accelerated Kokoro-82M ONNX TTS daemon (`CUDAExecutionProvider`), speech text sanitizer, `#bot-commands` `/way` card poster, and `AlfredBridge` waypoint/`Spoken` notifier.
* [`patch_openclaw_voice.py`](patch_openclaw_voice.py) & [`voice-ctrl`](voice-ctrl) — Idempotent patcher for `@openclaw/discord` enabling speaker attribution (`<wav>.meta.json`), instant ack playback, capture during bot speech, and `/tmp/alfred_voice_ctrl.sock`.
* [`voice_replay.py`](voice_replay.py) & [`voice-audit`](voice-audit) — Offline regression suite (`<1s`, `$0` token spend) and live audit log viewer.

## Setup

### 1. Build the local WoW Forever SQLite database
```bash
QUESTIE_ADDON_DIR="/path/to/Interface/AddOns/QuestieDB" python3 build_wow_db.py
python3 build_wow_lore.py
python3 ingest_loreforever.py
python3 wow_db.py --self-check
python3 voice_replay.py
```

### 2. Start the warm STT, GPU TTS & `AlfredBridge` daemons
Copy [`systemd/whisper-stt.service`](systemd/whisper-stt.service), [`systemd/kokoro-tts.service`](systemd/kokoro-tts.service), and [`systemd/alfred-bridge.service`](systemd/alfred-bridge.service) to `~/.config/systemd/user/` and enable them:
```bash
systemctl --user daemon-reload
systemctl --user enable --now whisper-stt.service kokoro-tts.service alfred-bridge.service
```

### 3. Install the `AlfredBridge` WoW Forever AddOn & Start the Windows Relay (Optional)
```powershell
# Installs AlfredBridge + 20 on-demand WaypointUI pin slots into _forever_\Interface\AddOns\
python bridge\install_addon.py --build-zip

# Run alongside World of Warcraft: Forever for live telemetry, WaypointUI 3D arrows & Spoken pause/resume
python bridge\alfred_bridge_relay.py --server http://<your-voice-host>:8098
```
In-game slash commands: `/alfred status` (`/ab status`), `/ab testpin`, `/ab moments` (toggle opt-in level-up/boss/wipe commentary).

### 4. Fine-Tune or Load `alfred-v2` (`Gemma 4 E4B IT` QLoRA)
```bash
python3 training/build_alfred_v2_dataset.py
python3 training/train_alfred_v2_qlora.py \
    --train-file ./data/alfred_v2_train.jsonl \
    --eval-file  ./data/alfred_v2_eval.jsonl \
    --out-dir    ./alfred_v2_build
ollama create alfred-v2:latest -f training/Modelfile.alfred-v2
```

### 5. Patch `@openclaw/discord` & Control Voice Channels
```bash
python3 patch_openclaw_voice.py
./kokoro-tts --gen-acks
./voice-ctrl status
./voice-audit 30
```

## Production Reference vs. Untested Extension Hooks

### How we run this in production
We run this stack on a dedicated Linux home server (**Ubuntu 24.04**, dual Pascal/Turing GPUs) connected to a live Discord voice channel alongside the `AlfredBridge` relay on our Windows gaming workstation:
* **STT & TTS (`GPU 1 — GTX 1080 8 GB`):** `whisper_daemon.py` (`distil-large-v3` `int8`) + `kokoro_daemon.py` (`kokoro-v1.0.onnx` `bm_george` on `CUDAExecutionProvider`).
* **Local Butler Model (`GPU 0 — RTX 2080 8 GB`):** `alfred-v2:latest` (Unsloth QLoRA fine-tune of `Gemma 4 E4B IT` in `Q4_K_M`, `100% GPU`) handling narrow lookups, banter, and ticket summaries, with `gemini-2.5-flash` handling open-ended strategy, multi-zone navigation, and `#bot-commands` vision/URL checks.
* **Knowledge Base & Live Telemetry:** `wow_forever.sqlite` (`12,000+` Questie/AddOn records + `3,313` Year 25 ADP `LoreForever` & Classic lore articles) queried in `<5 ms`, enriched in real time by `sheldon_bridge_daemon.py` (`:8098`) receiving optical telemetry from `AlfredBridge`.

### Adapting for your own bot or non-OpenClaw agent (untested live)
Because `whisper-stt` (`wav -> stdout`) and `kokoro-tts` (`text -> wav`) are plain Unix CLI filters, we exposed three environment variable overrides so you can connect a differently named bot, a non-WoW knowledge base, or a non-OpenClaw voice framework. **Note:** These overrides are verified via offline unit tests (`voice_replay.py`), not live-tested against external agent frameworks:
* `VOICE_WAKE_NAME` / `VOICE_WAKE_PATTERN` — Change the wake word from `"Alfred"` (e.g. `VOICE_WAKE_NAME="Jarvis"`).
* `VOICE_CONTEXT_CMD` — Replace `wow_db.auto_context()` with any shell command that reads the user transcript on `stdin` and prints ground-truth context on `stdout` (or leave `wow_forever.sqlite` absent to run with zero RAG).
* `VOICE_INTERRUPT_CMD` — Replace OpenClaw's `/tmp/alfred_voice_ctrl.sock` stop command with a custom shell command (e.g. `pkill -f ffplay` or an HTTP webhook) when a user says `"<Bot> stop"`.

## License
MIT
