#!/usr/bin/env python3
"""Offline Voice Replay & Regression Suite for whisper-stt + wow_db.py (<1s, $0 token spend)."""
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wow_db

_stt_path = os.path.join(HERE, "whisper-stt")
_spec = spec_from_loader("whisper_stt", SourceFileLoader("whisper_stt", _stt_path))
whisper_stt = module_from_spec(_spec)
_spec.loader.exec_module(whisper_stt)

AUDIT_PATH = os.environ.get("VOICE_AUDIT_LOG", os.path.expanduser("~/.openclaw/logs/voice-audit.jsonl"))
RETAIL_LEAK_RE = re.compile(
    r"deathwing\s+burst|until\s+the\s+cataclysm|now\s+broken\s+stonewrought\s+dam|shadowlands|the\s+jailer|fourth\s+war|siege\s+of\s+orgrimmar",
    re.I,
)

TORTURE_TESTS = [
    ("Hey Alfred, tell me about Rawl", "WAKE", "Lore (Thrall):"),
    ("Hey Alfred, tell me about Silvanus", "WAKE", "Lore (Sylvanas Windrunner):"),
    ("Hey Alfred, who is Scenarias?", "WAKE", "Lore (Cenarius):"),
    ("Hey Alfred, describe the history of the Murlocks", "WAKE", "Lore (Murloc):"),
    ("Hey Alfred, tell me about Kvinajara", "WAKE", "Lore (Queen Azshara):"),
    ("Hey Alfred, tell me about Loch Madon", "WAKE", "Lore (Loch Modan):"),
    ("Hey Alfred, where can I buy arrows and repair in Loch Modan?", "WAKE", "Bowyer"),
    ("Hey Alfred, I'm in Westfall at level 10, what quests can I get?", "WAKE", "Poor Old Blanchy"),
    ("Hey Alfred, how do I get Furlbrose Deed?", "WAKE", "Furlbrow's Deed (#184)"),
    ("Hey Alfred, what quests are in Riverglades?", "WAKE", "Riverglades"),
    ("Hey Alfred, how do I make West Falls 2?", "WAKE", "Westfall Stew"),
    ("Hey Alfred, where do I get Gortusk Liver for Red Ridge?", "WAKE", "Goretusk Liver Pie"),
    ("Hey Alfred, where can Palidans train for two-handed axes?", "WAKE", "Buliwyf Stonehand"),
    ("Hey Alfred, what do I do with Rumi of Nomragon?", "WAKE", "Lore (Gnomeregan):"),
    ("Alfred stop", "INTERRUPT", ""),
    ("Stop Alfred", "INTERRUPT", ""),
    ("Hey Alfred, stop talking please", "INTERRUPT", ""),
    ("Enough Alfred", "INTERRUPT", ""),
    ("Thank you, Alfred.", "INTERRUPT", ""),
    ("Got it Alfred", "INTERRUPT", ""),
    ("Shut up, Alfred!", "INTERRUPT", ""),
    ("Never mind Alfred", "INTERRUPT", ""),
    ("That's enough", "BARE_INTERRUPT", ""),
    ("Stop talking", "BARE_INTERRUPT", ""),
    ("I have Alfred muted right now", "IGNORE", ""),
    ("Don't kick Alfred from the channel", "IGNORE", ""),
    ("What does Alfred do?", "IGNORE", ""),
    ("Yes, his name's Alfred.", "IGNORE", ""),
    ("things that Alfred tried to pronounce for us.", "IGNORE", ""),
    ("He has a separate computer for Alfred that he keeps on.", "IGNORE", ""),
    ("Never be reading Alfred's output right now on a different monitor.", "IGNORE", ""),
]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=== PART 1: CURATED TORTURE & REGRESSION SUITE ===")
    failures = []
    for q, expected, must_contain in TORTURE_TESTS:
        t0 = time.perf_counter()
        action = whisper_stt.classify(q, playing=(expected == "BARE_INTERRUPT"))
        norm = wow_db.normalize_stt(q)
        ctx = wow_db.auto_context(norm) if action == "WAKE" else ""
        ms = (time.perf_counter() - t0) * 1000
        rows = max(0, len(ctx.splitlines()) - 1) if ctx else 0
        ok = (action == expected) and (not must_contain or must_contain.lower() in ctx.lower())
        if RETAIL_LEAK_RE.search(ctx):
            ok = False
            failures.append(f"RETAIL LEAK in '{q}': {ctx}")
        elif not ok:
            failures.append(f"FAIL on '{q}': got action={action} (expected {expected}), must_contain='{must_contain}' in {rows} rows")
        first_line = ctx.splitlines()[1] if rows > 0 else "(no db context)"
        print(f"[{'PASS' if ok else 'FAIL'}] ({ms:4.1f}ms | {action:14s} | {rows:2d} rows) {q!r} -> {first_line[:95]}")

    if os.path.exists(AUDIT_PATH):
        print(f"\n=== PART 2: HISTORICAL VOICE-AUDIT REPLAY ({AUDIT_PATH}) ===")
        seen, hist = set(), []
        for line in open(AUDIT_PATH, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("kind") == "stt":
                txt = r.get("text", "").strip()
                if (r.get("woke") or "alfred" in txt.lower()) and txt.lower() not in seen:
                    seen.add(txt.lower())
                    hist.append(txt)
        for i, txt in enumerate(hist, 1):
            t0 = time.perf_counter()
            action = whisper_stt.classify(txt)
            ctx = wow_db.auto_context(wow_db.normalize_stt(txt)) if action == "WAKE" else ""
            ms = (time.perf_counter() - t0) * 1000
            rows = max(0, len(ctx.splitlines()) - 1) if ctx else 0
            if RETAIL_LEAK_RE.search(ctx):
                failures.append(f"HISTORICAL RETAIL LEAK in '{txt}'")
            first_line = ctx.splitlines()[1][:85] if rows > 0 else "(no db rows)"
            print(f"  {i:2d}. [{action:9s} | {ms:4.1f}ms | {rows:2d} rows] {txt[:65]!r:67s} -> {first_line}")

    if failures:
        print("\n=== FAILURES DETECTED ===")
        for f in failures:
            print("  *", f)
        sys.exit(1)

    # Verify custom wake-word override (VOICE_WAKE_NAME) and speakable() "about ~" fix
    os.environ["VOICE_WAKE_NAME"] = "Jarvis"
    _spec_j = spec_from_loader("whisper_stt_j", SourceFileLoader("whisper_stt_j", _stt_path))
    stt_jarvis = module_from_spec(_spec_j)
    _spec_j.loader.exec_module(stt_jarvis)
    os.environ.pop("VOICE_WAKE_NAME", None)
    assert stt_jarvis.classify("Hey Jarvis, what is the weather?") == "WAKE", "VOICE_WAKE_NAME WAKE failed"
    assert stt_jarvis.classify("Jarvis stop") == "INTERRUPT", "VOICE_WAKE_NAME INTERRUPT failed"
    assert stt_jarvis.classify("I have Jarvis muted") == "IGNORE", "VOICE_WAKE_NAME IGNORE failed"

    _tts_path = os.path.join(HERE, "kokoro-tts")
    _spec_t = spec_from_loader("kokoro_tts", SourceFileLoader("kokoro_tts", _tts_path))
    kokoro_tts = module_from_spec(_spec_t)
    _spec_t.loader.exec_module(kokoro_tts)
    assert kokoro_tts.speakable("About ~2-4% drop") == "About 2 to 4 percent drop", "speakable 'about ~' check failed"
    assert kokoro_tts.speakable("~2-4% drop") == "about 2 to 4 percent drop", "speakable '~' check failed"
    assert not whisper_stt.is_casual_or_random("Hey Alfred, read the link in chat", "", 0), "chat link should not route to banter"
    assert not whisper_stt.is_casual_or_random("Hey Alfred, check the screenshot I just posted in chat", "", 0), "chat screenshot should not route to banter"

    # Verify Phase 3 Shared Party Blackboard (character profile, party zone, pins, readback, clear)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        os.environ["VOICE_PARTY_STATE"] = os.path.join(td, "party_state.json")
        try:
            assert whisper_stt.handle_party_command("Hey Alfred, I'm in Westfall at level 10, what quests can I get?", "Tom", "111") is None
            r1 = whisper_stt.handle_party_command("Hey Alfred, remember I'm a level 22 Human Paladin", "Tom", "111")
            assert r1 and "level 22 Human Paladin" in r1[0], f"char save failed: {r1}"
            r2 = whisper_stt.handle_party_command("Hey Alfred, remember we're running Shadowfang Keep tonight", "Ben", "222")
            assert r2 and "Shadowfang Keep" in r2[0], f"party goal failed: {r2}"
            r3 = whisper_stt.handle_party_command("Hey Alfred, pin 37.5, 50.7 as Cozy Sleeping Bag start", "Ben", "222")
            assert r3 and "37.5, 50.7" in r3[0], f"pin failed: {r3}"
            pctx = whisper_stt.format_party_context("Tom", "111")
            assert "Tom (level 22 Human Paladin)" in pctx and "Shadowfang Keep" in pctx and "37.5, 50.7" in pctx, f"pctx failed: {pctx}"
            r4 = whisper_stt.handle_party_command("Hey Alfred, read back our notes", "Tom", "111")
            assert r4 and "Cozy Sleeping Bag start" in r4[0], f"readback failed: {r4}"
            r5 = whisper_stt.handle_party_command("Hey Alfred, clear party notes", "Ben", "222")
            assert r5 and "Cleared" in r5[0] and not whisper_stt.load_party_state()["notes"], f"clear failed: {r5}"

            # Phase 6 AlfredBridge live telemetry + enrich_query_with_bridge + kokoro-tts pin extraction checks
            st = whisper_stt.load_party_state()
            st["live"] = {
                "111": {
                    "name": "Tom",
                    "char": "Uther",
                    "realm": "Forever",
                    "class": "PALADIN",
                    "level": 24,
                    "zone": "Duskwood",
                    "subzone": "Darkshire",
                    "x": 75.8,
                    "y": 45.3,
                    "target": "Madame Eva",
                    "quests": ["The Hermit"],
                    "party": [{"n": "Jaina", "c": "MAGE", "l": 23, "z": "Duskwood"}],
                    "ts_epoch": time.time(),
                }
            }
            whisper_stt.save_party_state(st)
            enr = whisper_stt.enrich_query_with_bridge("Hey Alfred, what quests does this guy give?", "Tom", "111")
            assert "Madame Eva" in enr and "Duskwood" in enr, f"enrich_query_with_bridge failed: {enr}"
            pctx_live = whisper_stt.format_party_context("Tom", "111")
            assert "Darkshire, Duskwood (75.8, 45.3)" in pctx_live and "Madame Eva" in pctx_live and "Jaina" in pctx_live, f"live pctx failed: {pctx_live}"

            _kspec = spec_from_loader("kokoro_tts", SourceFileLoader("kokoro_tts", os.path.join(HERE, "kokoro-tts")))
            kokoro_mod = module_from_spec(_kspec)
            _kspec.loader.exec_module(kokoro_mod)
            pins = kokoro_mod.extract_waypoint_pins("Hey Alfred, where is Marshal Dughan in Elwynn Forest?", "Marshal Dughan is in Goldshire, Elwynn Forest at 42.1, 65.9, sir.")
            assert len(pins) == 1 and pins[0]["x"] == 42.1 and pins[0]["y"] == 65.9 and pins[0]["zone"] == "Elwynn Forest" and pins[0]["mapID"] == 1429, f"kokoro pins failed: {pins}"
            pins_custom = kokoro_mod.extract_waypoint_pins("Hey Alfred, where is Farholde Keep?", "Farholde Keep sits in Riverglades at 48.5, 36.2, sir.")
            assert len(pins_custom) == 1 and pins_custom[0]["zone"] == "Riverglades" and pins_custom[0]["mapID"] == 2548, f"custom forever zone pin failed: {pins_custom}"
        finally:
            os.environ.pop("VOICE_PARTY_STATE", None)
    print("\nALL REGRESSION & EXTENSION HOOK CHECKS PASSED!")


if __name__ == "__main__":
    main()
