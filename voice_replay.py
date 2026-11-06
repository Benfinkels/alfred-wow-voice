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
    ("Hey Alfred, where can I buy arrows and repair in Loch Modan?", "WAKE", "<Bowyer>"),
    ("Hey Alfred, I'm in Westfall at level 10, what quests can I get?", "WAKE", "Poor Old Blanchy"),
    ("Hey Alfred, how do I get Furlbrose Deed?", "WAKE", "Furlbrow's Deed (#184)"),
    ("Hey Alfred, what quests are in Riverglades?", "WAKE", "Riverglades"),
    ("Hey Alfred, how do I make West Falls 2?", "WAKE", "Westfall Stew"),
    ("Hey Alfred, where do I get Gortusk Liver for Red Ridge?", "WAKE", "Goretusk Liver Pie"),
    ("Hey Alfred, where can Palidans train for two-handed axes?", "WAKE", "Lore (Paladin):"),
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
    print("\nALL REGRESSION & EXTENSION HOOK CHECKS PASSED!")


if __name__ == "__main__":
    main()
