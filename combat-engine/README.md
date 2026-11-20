# `Alfred.CombatEngine` (`.NET 10 LTS`)

Real-time *World of Warcraft* (`Midnight` Retail & `Classic/Forever`) combat-log telemetry engine, `<50ms` `SignalR` second-screen Raid HUD, and `<100ms` deterministic post-wipe verdict generator for `alfred-wow-voice`.

## Quick Start for Co-Authors (`jhrabchak` & `Benfinkels`)

1. Open this repo on the `feat/combat-engine` branch in **Google Antigravity** or **Claude Code** (`cd combat-engine && claude`).
2. Read [`SPEC.md`](SPEC.md) for the full architecture diagram, `.NET 10` project layout, `WipeVerdict` JSON contract with `bridge/sheldon_bridge_daemon.py` (`:8098`), and the **4-Ring Build Roadmap**.
3. Start **Ring 1** by scaffolding `Alfred.CombatEngine.sln` inside this `combat-engine/` directory!
