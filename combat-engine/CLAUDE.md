# Claude Code / Antigravity Context — `Alfred.CombatEngine` (`.NET 10 LTS`)

You are pair-programming with **Jimmy (`jhrabchak`, Lead Software Engineer)** and **Ben (`Benfinkels`)** on `Alfred.CombatEngine` inside `combat-engine/` on branch `feat/combat-engine`.

1. Always read [`SPEC.md`](SPEC.md) before scaffolding or editing code.
2. Target **`.NET 10 LTS`** (`net10.0`) — both `.NET 8` and `.NET 9` hit End of Support on Nov 10, 2026.
3. Build incrementally following **Ring 1 -> Ring 4** in `SPEC.md` (small, clean PRs with `xUnit` tests so every slice earns green CI commits).
4. Whenever introducing modern `.NET 10` idioms (`ReadOnlySpan<char>`, `System.Threading.Channels`, `Program.cs` DI lifetimes, `IHttpClientFactory` resilience, `SignalR` hubs, `EF Core` async + `CancellationToken`, `record` DTOs), include a brief 2-bullet note mapping them from `.NET Framework 4.x` / WinForms / Oracle-Flyway patterns.
5. Never touch WoW process memory or restricted in-game Lua Secret Values — `Alfred.CombatEngine.Shipper` strictly tails on-disk `WoWCombatLog.txt` with `FileShare.ReadWrite`.
