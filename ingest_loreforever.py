#!/usr/bin/env python3
"""Ingest CC BY-SA 4.0 Year 25 ADP lore & spoiler guardrails from mliudev/LoreForever into wow_forever.sqlite."""
import io, json, os, re, sqlite3, ssl, sys, time, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "wow_forever.sqlite")
if not os.path.exists(DEFAULT_DB) and os.path.exists(os.path.join(os.path.dirname(HERE), "wow_forever.sqlite")):
    DEFAULT_DB = os.path.join(os.path.dirname(HERE), "wow_forever.sqlite")
DB_PATH = os.environ.get("WOW_DB_PATH", sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB)
CACHE_ZIP = os.environ.get(
    "LOREFOREVER_ZIP",
    os.path.join(os.path.dirname(HERE), "cache", "loreforever_main.zip"),
)
ZIP_URL = "https://github.com/mliudev/LoreForever/archive/refs/heads/main.zip"

POST_VANILLA_LEAK_RE = re.compile(
    r"\b(?:Knights\s+of\s+the\s+Ebon\s+Blade|Deathwing\s+burst|until\s+the\s+Cataclysm|now\s+broken\s+Stonewrought\s+Dam|"
    r"Shadowlands|the\s+Jailer|Fourth\s+War|Siege\s+of\s+Orgrimmar|Warlords\s+of\s+Draenor|Dragon\s+Isles|Battle\s+for\s+Azeroth)\b",
    re.I,
)
PROTECTED_FOREVER_RE = re.compile(
    r"\[WoW Forever|WoW Forever|Zone Lore:|Class Quest Spells:|Hall of Thanes|Ruins of Lordaeron|Excavation Site|Drowned City|Krol'dok|Alcaz Prison|Blackmaw Hold|Shaper's Terrace",
    re.I,
)
INGEST_TYPES = ("topic", "zone", "subzone", "npc", "item")


def ensure_zip(path: str) -> str:
    if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ctx = ssl.create_default_context()
    req = urllib.request.Request(ZIP_URL, headers={"User-Agent": "AlfredWoWForeverLore/1.0"})
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=90)
    except Exception:
        ctx = ssl._create_unverified_context()
        resp = urllib.request.urlopen(req, context=ctx, timeout=90)
    with resp, open(path, "wb") as out:
        while chunk := resp.read(1024 * 1024):
            out.write(chunk)
    return path


def build_entry_text(doc: dict, spoiler_rule: dict) -> tuple[str, str]:
    key_rule = spoiler_rule or {}
    removed_kw = {k.lower() for k in key_rule.get("remove_keywords", [])}
    name_low = (doc.get("name") or "").strip().lower()
    aliases_list = []
    meta = doc.get("meta") or {}
    for a in (meta.get("aliases") or []):
        if isinstance(a, str) and a.strip() and a.strip().lower() != name_low and a.strip().lower() not in removed_kw and a.strip() not in aliases_list:
            aliases_list.append(a.strip())
    wiki_field = meta.get("wiki")
    if isinstance(wiki_field, str) and wiki_field.strip() and wiki_field.strip().lower() != name_low and wiki_field.strip().lower() not in removed_kw and wiki_field.strip() not in aliases_list:
        aliases_list.append(wiki_field.strip())

    # If spoilers.json provides a spoiler-safe 25 ADP summary override, strictly use it and skip sections.
    if key_rule.get("summary"):
        base = key_rule["summary"].strip()
    else:
        base = (doc.get("summary") or "").strip()
        # Append first section body when entry has zero spoiler flags and fits cleanly under 650 chars
        if not key_rule and doc.get("sections"):
            sec0 = (doc["sections"][0].get("body") or "").strip()
            if sec0 and len(base) + len(sec0) + 3 <= 640:
                base = f"{base} {sec0}"

    base = re.sub(r"\s+", " ", base).strip()
    summary = f"[25 ADP Lore] {base}"[:680]
    return ", ".join(aliases_list[:8]), summary


def main():
    t0 = time.perf_counter()
    zpath = ensure_zip(CACHE_ZIP)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Idempotency: remove previously inserted LoreForever rows (not healed legacy titles) before fresh pass
    cur.execute("DELETE FROM lore WHERE summary LIKE '[25 ADP Lore]%' AND id > 1050")
    before_count = cur.execute("SELECT COUNT(*) FROM lore").fetchone()[0]
    existing = {
        row[0].lower(): (row[0], row[1] or "", row[2] or "")
        for row in cur.execute("SELECT title, aliases, summary FROM lore")
    }

    with zipfile.ZipFile(zpath) as z:
        spoilers = {}
        for n in z.namelist():
            if n.endswith("data/spoilers.json"):
                spoilers = json.loads(z.read(n).decode("utf-8"))
                break

        docs_by_type = {t: [] for t in INGEST_TYPES}
        for n in z.namelist():
            if "/data/lore/" not in n or not n.endswith(".json"):
                continue
            prefix = os.path.basename(n).split("_", 1)[0]
            if prefix in docs_by_type:
                docs_by_type[prefix].append(n)

        inserted = 0
        healed_leaks = 0
        for t in INGEST_TYPES:
            for n in sorted(docs_by_type[t]):
                doc = json.loads(z.read(n).decode("utf-8"))
                name = (doc.get("name") or "").strip()
                key = doc.get("key") or ""
                if not name:
                    continue
                aliases, summary = build_entry_text(doc, spoilers.get(key, {}))
                if POST_VANILLA_LEAK_RE.search(summary):
                    continue
                low = name.lower()
                low_bare = re.sub(r"^the\s+", "", low)
                match_key = low if low in existing else (low_bare if low_bare in existing else None)
                if match_key is not None:
                    orig_title, orig_aliases, orig_sum = existing[match_key]
                    if not PROTECTED_FOREVER_RE.search(orig_sum) and POST_VANILLA_LEAK_RE.search(orig_sum):
                        merged_aliases = ", ".join(dict.fromkeys(x.strip() for x in f"{orig_aliases}, {aliases}".split(",") if x.strip()))
                        cur.execute("UPDATE lore SET aliases = ?, summary = ? WHERE title = ?", (merged_aliases, summary, orig_title))
                        existing[match_key] = (orig_title, merged_aliases, summary)
                        healed_leaks += 1
                    continue

                title = name if t != "item" or low not in existing else f"{name} (Item)"
                cur.execute("INSERT OR IGNORE INTO lore (title, aliases, summary) VALUES (?, ?, ?)", (title, aliases, summary))
                if cur.rowcount:
                    existing[title.lower()] = (title, aliases, summary)
                    inserted += 1

    # Also scrub any remaining post-Vanilla expansion sentences in legacy wiki rows
    scrubbed_legacy = 0
    for title, orig_sum in list(cur.execute("SELECT title, summary FROM lore")):
        if not PROTECTED_FOREVER_RE.search(orig_sum or "") and POST_VANILLA_LEAK_RE.search(orig_sum or ""):
            cleaned_sents = [s for s in re.split(r"(?<=[.!?])\s+", orig_sum) if not POST_VANILLA_LEAK_RE.search(s)]
            if cleaned_sents:
                cur.execute("UPDATE lore SET summary = ? WHERE title = ?", (" ".join(cleaned_sents), title))
                scrubbed_legacy += 1

    cur.execute("INSERT INTO lore_fts(lore_fts) VALUES('rebuild')")
    conn.commit()
    after_count = cur.execute("SELECT COUNT(*) FROM lore").fetchone()[0]
    fts_count = cur.execute("SELECT COUNT(*) FROM lore_fts").fetchone()[0]
    sample_hits = [
        cur.execute("SELECT title FROM lore_fts WHERE lore_fts MATCH ? LIMIT 3", (q,)).fetchall()
        for q in ('"Abercrombie"', '"Addle"', '"Bolvar"')
    ]
    conn.close()
    print(
        f"LoreForever 25 ADP ingest complete in {time.perf_counter() - t0:.2f}s: "
        f"before={before_count}, inserted={inserted}, healed_leaks={healed_leaks}, "
        f"scrubbed_legacy={scrubbed_legacy}, after={after_count} (fts={fts_count})"
    )
    print("Sample FTS hits:", sample_hits)


if __name__ == "__main__":
    main()
