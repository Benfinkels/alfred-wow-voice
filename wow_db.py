#!/usr/bin/env python3
"""
Fast local lookup + natural-language auto-context extractor for wow_forever.sqlite.
Used both as a standalone CLI (`wow-db`) and in-process inside `whisper-stt` (<5ms).
"""
import argparse
import os
import re
import sqlite3
import sys

DEFAULT_DB = os.environ.get(
    "WOW_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "wow_forever.sqlite")
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "wow_forever.sqlite"))
    else os.path.expanduser("~/.openclaw/workspace/wow_forever.sqlite"),
)

STOPWORDS = {
    "hey", "alfred", "al", "please", "can", "you", "tell", "me", "what", "whats", "where", "who",
    "how", "do", "i", "get", "find", "start", "turn", "in", "the", "a", "an", "for",
    "to", "of", "in", "on", "at", "is", "are", "there", "any", "quests", "quest",
    "level", "lvl", "im", "am", "around", "about", "right", "now", "should", "pick",
    "up", "grab", "drops", "drop", "rate", "rates", "chance", "percent", "percentage",
    "from", "npc", "item", "zone", "wow", "forever", "short", "answer", "more",
    "details", "info", "give", "brief", "quick", "does", "with", "my", "and", "or",
    "go", "going", "need", "help", "looking", "that", "this", "these", "those", "have",
    "has", "had", "done", "doing", "know", "think", "thought", "just", "like", "want",
    "kick", "muted", "mute", "deafened", "deafen", "unmute", "bot", "channel", "still",
    "gonna", "well", "good", "some", "into", "over", "only", "even", "back", "after",
    "before", "because", "dont", "didnt", "wasnt", "isnt", "arent", "would", "could",
    "see", "later", "will", "here", "when", "then", "they", "them", "their", "your",
    "morning", "afternoon", "evening", "night", "hello", "little", "bit", "us", "we",
    "our", "talk", "say", "said", "ask", "asked", "hear", "heard", "history", "lore",
    "story", "origin", "origins", "background", "entire", "unabridged", "describe",
    "explain", "detail", "much", "possible", "everything", "interesting", "piece",
    "something", "most", "people", "anyone", "nobody", "knows", "fact", "facts",
    "cool", "fun", "random", "weird", "obscure", "classic", "not", "yes", "no", "sir",
    "maam", "name", "names", "tried", "pronounce", "things", "thing", "stuff", "guy",
    "guys", "man", "woman", "boy", "girl", "best", "worst", "first", "last", "many",
    "steps", "step", "way", "ways", "make", "made", "use", "used", "using", "work",
    "works", "working", "drop", "dropping", "dropped", "more", "less", "very", "really",
    "also", "too", "two", "one", "three", "four", "five", "specific", "specifically",
    "roleplay", "rollplay", "server", "blind", "impression", "complete", "story",
    "brother", "bro", "dude", "mate", "buddy", "pal", "chief", "homie", "folks",
    "doing", "hows", "whats", "sup", "hello", "hi", "yo", "today", "tonight",
    "keep", "keeping", "kept", "bag", "bags", "inventory", "bank", "order", "able",
    "use", "used", "using", "need", "needed", "power", "microwave", "rotisserie",
    "spell", "spells", "cast", "casting", "learn", "learned", "slot", "slots",
}

TOWN_TO_ZONE = {
    "goldshire": "Elwynn Forest",
    "northshire": "Elwynn Forest",
    "sentinel hill": "Westfall",
    "sentinel tower": "Westfall",
    "moonbrook": "Westfall",
    "lakeshire": "Redridge Mountains",
    "darkshire": "Duskwood",
    "thelsamar": "Loch Modan",
    "kharanos": "Dun Morogh",
    "menethil": "Wetlands",
    "southshore": "Hillsbrad Foothills",
    "tarren mill": "Hillsbrad Foothills",
    "brill": "Tirisfal Glades",
    "sepulcher": "Silverpine Forest",
    "crossroads": "The Barrens",
    "ratchet": "The Barrens",
    "camp taurajo": "The Barrens",
    "razor hill": "Durotar",
    "bloodhoof": "Mulgore",
    "auberdine": "Darkshore",
    "dolanaar": "Teldrassil",
    "shadowglen": "Teldrassil",
    "starbreeze": "Teldrassil",
    "ban'ethil": "Teldrassil",
    "rut'theran": "Teldrassil",
    "astranaar": "Ashenvale",
    "booty bay": "Stranglethorn Vale",
    "gadgetzan": "Tanaris",
}

TOWN_HUB_COORDS = {
    "goldshire": ("Elwynn Forest", 42.1, 65.9, "Goldshire Lion's Pride Inn & Blacksmith"),
    "northshire": ("Elwynn Forest", 48.2, 42.9, "Northshire Abbey"),
    "sentinel hill": ("Westfall", 56.5, 52.6, "Sentinel Hill tower"),
    "moonbrook": ("Westfall", 44.5, 69.5, "Moonbrook"),
    "lakeshire": ("Redridge Mountains", 26.5, 45.3, "Lakeshire inn & bridge"),
    "darkshire": ("Duskwood", 73.9, 44.4, "Darkshire Scarlet Raven Tavern"),
    "thelsamar": ("Loch Modan", 34.8, 49.3, "Thelsamar Stoutlager Inn"),
    "kharanos": ("Dun Morogh", 47.2, 52.2, "Kharanos Thunderbrew Distillery"),
    "menethil": ("Wetlands", 10.7, 60.9, "Menethil Harbor Deepwater Tavern"),
    "southshore": ("Hillsbrad Foothills", 51.1, 58.9, "Southshore inn"),
    "tarren mill": ("Hillsbrad Foothills", 62.4, 19.8, "Tarren Mill inn"),
    "brill": ("Tirisfal Glades", 61.7, 52.0, "Brill Gallows' End Tavern"),
    "sepulcher": ("Silverpine Forest", 43.4, 41.3, "The Sepulcher"),
    "crossroads": ("The Barrens", 52.0, 29.9, "The Crossroads"),
    "ratchet": ("The Barrens", 62.4, 37.6, "Ratchet port"),
    "camp taurajo": ("The Barrens", 44.8, 59.1, "Camp Taurajo"),
    "razor hill": ("Durotar", 51.9, 41.6, "Razor Hill"),
    "bloodhoof": ("Mulgore", 47.5, 60.2, "Bloodhoof Village"),
    "auberdine": ("Darkshore", 37.0, 44.1, "Auberdine inn & dock"),
    "dolanaar": ("Teldrassil", 56.3, 59.8, "Dolanaar town center; note that Nadyia Maneweaver & Radnaal Maneweaver at (41.9, 49.4) are NOT inside Dolanaar, they stand in a small roadside wooden pavilion west-northwest of Dolanaar along the main road toward Darnassus, south of the Pools of Arlithrien"),
    "shadowglen": ("Teldrassil", 58.7, 41.5, "Shadowglen Aldrassil tree"),
    "starbreeze": ("Teldrassil", 64.5, 51.0, "Starbreeze Village"),
    "rut'theran": ("Teldrassil", 55.6, 92.0, "Rut'theran Village below Darnassus"),
    "astranaar": ("Ashenvale", 36.6, 49.6, "Astranaar inn"),
    "booty bay": ("Stranglethorn Vale", 27.1, 77.2, "Booty Bay Salty Sailor Tavern"),
    "gadgetzan": ("Tanaris", 51.6, 27.9, "Gadgetzan town"),
}


def annotate_hub_direction(row: str, hub_title: str, zone: str, hx: float, hy: float) -> str:
    """Append exact INSIDE/OUTSIDE + compass direction from a named town hub when coordinates are in the same zone."""
    import math
    m = re.search(rf"{re.escape(zone)}\s*\((\d+\.\d+),\s*(\d+\.\d+)\)", row)
    if not m or not row.startswith("NPC:"):
        return row
    nx, ny = float(m.group(1)), float(m.group(2))
    dx, dy = nx - hx, ny - hy
    dist = math.hypot(dx, dy)
    if dist <= 3.5:
        return f"{row} [INSIDE {hub_title} ({hx}, {hy})]"
    dirs = ["east", "southeast", "south", "southwest", "west", "northwest", "north", "northeast"]
    idx = round(((math.degrees(math.atan2(dy, dx)) + 360) % 360) / 45) % 8
    return f"{row} [OUTSIDE {hub_title}: ~{dist:.0f} map units {dirs[idx]} of {hub_title} ({hx}, {hy})]"

KNOWN_ZONES = [
    "Westfall", "Elwynn Forest", "Dun Morogh", "Loch Modan", "Redridge Mountains",
    "Duskwood", "Wetlands", "Hillsbrad Foothills", "Arathi Highlands", "Alterac Mountains",
    "Stranglethorn Vale", "Swamp of Sorrows", "Badlands", "Searing Gorge", "Blasted Lands",
    "Burning Steppes", "Western Plaguelands", "Eastern Plaguelands", "The Hinterlands",
    "Tirisfal Glades", "Silverpine Forest", "Teldrassil", "Darkshore", "Ashenvale",
    "Stonetalon Mountains", "Desolace", "Feralas", "Dustwallow Marsh", "The Barrens",
    "Durotar", "Mulgore", "Thousand Needles", "Tanaris", "Un'Goro Crater", "Felwood",
    "Winterspring", "Azshara", "Silithus", "Moonglade", "Stormwind City", "Ironforge",
    "Darnassus", "Orgrimmar", "Thunder Bluff", "Undercity", "Riverglades", "Sunnyglade",
    "Zephras Isle", "Darkspear Islands", "Shen'dralas", "Mount Hyjal", "Gilneas",
    "City of Dalaran", "Scarlet Enclave", "The Hall of Thanes", "Ruins of Lordaeron",
    "Excavation Site: Wetlands", "The Drowned City", "Krol'dok Stronghold",
    "Alcaz Prison", "Blackmaw Hold", "Shaper's Terrace", "Ragefire Chasm",
    "The Deadmines", "Wailing Caverns",
    "Shadowfang Keep", "Blackfathom Deeps", "The Stockade", "Gnomeregan", "Razorfen Kraul",
    "Scarlet Monastery", "Razorfen Downs", "Uldaman", "Zul'Farrak", "Maraudon",
    "Blackrock Depths", "Blackrock Spire", "Scholomance", "Stratholme",
    "Blacksmithing", "Alchemy", "Engineering", "Leatherworking", "Tailoring",
    "Cooking", "First Aid", "Fishing", "Herbalism", "Camping",
    "Paladin", "Warrior", "Hunter", "Rogue", "Priest", "Mage", "Warlock", "Druid", "Shaman",
]

CORRECTIONS_PATH = os.path.expanduser("~/.openclaw/logs/corrections.jsonl")


def query_corrections(text: str) -> list[str]:
    if not os.path.exists(CORRECTIONS_PATH):
        return []
    import json
    ignore = STOPWORDS | {"train", "trained", "trainer", "skill", "skills", "learn", "learned", "craft", "crafted"}
    tokens = {
        w.replace("'", "") for w in re.findall(r"[a-zA-Z0-9']+", text.lower())
        if w.replace("'", "") not in ignore and len(w.replace("'", "")) > 3
    }
    if not tokens:
        return []
    out = []
    try:
        for line in open(CORRECTIONS_PATH, encoding="utf-8"):
            if not line.strip():
                continue
            c = json.loads(line)
            q_blob = f"{c.get('question', '')} {c.get('user_correction', '')}".lower()
            if any(re.search(rf"\b{re.escape(t[:6])}", q_blob) for t in tokens):
                out.append(f"[Learned Past Correction]: {c.get('corrected_answer', '')}")
    except Exception:
        pass
    return out[-3:]


def get_conn(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def query_zone_quests(conn: sqlite3.Connection, zone: str, level: int | None = None, limit: int = 14) -> list[str]:
    cur = conn.cursor()
    excl = "" if zone.lower() == "camping" else "AND zone NOT LIKE 'Camping%' AND zone NOT LIKE 'Midsummer%' AND zone NOT LIKE 'Lunar%'"
    if level is not None:
        rows = cur.execute(
            f"""
            SELECT summary FROM quests
            WHERE zone LIKE ? {excl} AND req_level <= ? AND quest_level >= ?
            ORDER BY (zone = ?) DESC, (prereqs != ''), ABS(quest_level - ?), req_level, id
            LIMIT ?
            """,
            (f"%{zone}%", level + 3, max(1, level - 2), zone, level, limit),
        ).fetchall()
        if rows:
            return [r["summary"] for r in rows]
    rows = cur.execute(
        f"""
        SELECT summary FROM quests
        WHERE zone LIKE ? {excl}
        ORDER BY (zone = ?) DESC, quest_level, req_level, id
        LIMIT ?
        """,
        (f"%{zone}%", zone, limit),
    ).fetchall()
    return [r["summary"] for r in rows]


def query_fts(conn: sqlite3.Connection, text: str, limit: int = 8) -> list[str]:
    tokens = [
        w for w in re.findall(r"[a-zA-Z0-9']+", text.lower())
        if w.replace("'", "") not in STOPWORDS and len(w.replace("'", "")) > 2
    ]
    if not tokens:
        return []
    cur = conn.cursor()
    clean_phrase = " ".join(tokens)

    # 1. Try exact/substring match on quest/item/npc names first
    results = []
    seen = set()
    for table in ("quests", "items", "npcs"):
        for r in cur.execute(
            f"SELECT summary FROM {table} WHERE name LIKE ? LIMIT 4",
            (f"%{clean_phrase}%",),
        ):
            if r["summary"] not in seen:
                seen.add(r["summary"])
                results.append(r["summary"])

    # 2. FTS5 AND query, then 5-char stem AND query (handles STT phonetic endings like Furlbrose -> Furlbrow), then OR query
    fts_terms = [re.sub(r"[^a-z0-9]", "", t) for t in tokens if len(re.sub(r"[^a-z0-9]", "", t)) > 2]
    if fts_terms and len(results) < limit:
        and_q = " AND ".join(f'"{t}"*' for t in fts_terms[:5])
        for r in cur.execute(
            "SELECT summary FROM wow_fts WHERE wow_fts MATCH ? ORDER BY rank LIMIT ?",
            (and_q, limit),
        ):
            if r["summary"] not in seen:
                seen.add(r["summary"])
                results.append(r["summary"])

    if fts_terms and not results:
        stem_q = " AND ".join(f'"{t[:5] if len(t) >= 6 else t}"*' for t in fts_terms[:5])
        for r in cur.execute(
            "SELECT summary FROM wow_fts WHERE wow_fts MATCH ? ORDER BY rank LIMIT ?",
            (stem_q, limit),
        ):
            if r["summary"] not in seen:
                seen.add(r["summary"])
                results.append(r["summary"])

    if fts_terms and len(results) < 3:
        or_q = " OR ".join(f'name : "{t}"' for t in fts_terms[:6] if len(t) >= 4)
        if or_q:
            cand_rows = cur.execute(
                "SELECT name, summary FROM wow_fts WHERE wow_fts MATCH ? LIMIT 40",
                (or_q,),
            ).fetchall()
            scored = []
            for r in cand_rows:
                n_low, s_low = r["name"].lower(), r["summary"].lower()
                if "monster - item" in n_low or "icoke" in s_low:
                    continue
                n_hits = sum(1 for t in fts_terms if re.search(rf"\b{re.escape(t)}\b", n_low))
                s_hits = sum(1 for t in fts_terms if t[:5] in s_low)
                if len(fts_terms) == 1 or n_hits >= 2 or s_hits >= 2 or any(re.search(rf"\b{re.escape(t)}\b", n_low) for t in fts_terms if len(t) >= 6):
                    scored.append((n_hits, s_hits, -len(n_low), r["summary"]))
            scored.sort(reverse=True)
            for _, _, _, rsum in scored[:limit]:
                if rsum not in seen:
                    seen.add(rsum)
                    results.append(rsum)

    # 3. Fast difflib fuzzy proper-noun fallback when exact/stem FTS finds nothing
    if fts_terms and not results:
        import difflib
        vocab_rows = cur.execute("SELECT name FROM quests UNION ALL SELECT name FROM items UNION ALL SELECT name FROM npcs").fetchall()
        vocab = {w for r in vocab_rows for w in re.findall(r"[a-z0-9]+", r[0].lower()) if len(w) >= 5 and w not in STOPWORDS}
        vocab_list = list(vocab)
        fuzzy_terms = []
        for t in fts_terms[:4]:
            if len(t) >= 5:
                m = difflib.get_close_matches(t, vocab_list, n=1, cutoff=0.78)
                if m:
                    fuzzy_terms.append(m[0])
        if fuzzy_terms:
            fq = " OR ".join(f'name : "{ft}"*' for ft in fuzzy_terms)
            for r in cur.execute("SELECT summary FROM wow_fts WHERE wow_fts MATCH ? ORDER BY rank LIMIT ?", (fq, limit)):
                if r["summary"] not in seen:
                    seen.add(r["summary"])
                    results.append(r["summary"])

    return results[:limit]


LORE_STOPWORDS = STOPWORDS | {
    "history", "lore", "story", "origin", "origins", "background", "entire",
    "unabridged", "describe", "explain", "detail", "much", "possible", "everything",
    "interesting", "piece", "something", "most", "people", "anyone", "nobody",
    "knows", "fact", "facts", "cool", "fun", "random", "weird", "obscure", "classic",
    "level", "levels",
}

STT_ALIASES = {
    r"\brawl\b": "Thrall",
    r"\bthrawl\b": "Thrall",
    r"\bthrol\b": "Thrall",
    r"\btrall\b": "Thrall",
    r"\bkvinajara\b": "Queen Azshara",
    r"\bashara\b": "Azshara",
    r"\bazhara\b": "Azshara",
    r"\bmurlocks?\b": "Murloc",
    r"\bmerlocks?\b": "Murloc",
    r"\bgortusk\b": "Goretusk",
    r"\bfurlbrose\b": "Furlbrow",
    r"\bvancleaf\b": "VanCleef",
    r"\bs[iy]lvan[auoi]s\b": "Sylvanas",
    r"\bs?cenari[auoi]s\b": "Cenarius",
    r"\barthus\b": "Arthas",
    r"\billiden\b": "Illidan",
    r"\bmalphurion\b": "Malfurion",
    r"\bonexia\b": "Onyxia",
    r"\bragneros\b": "Ragnaros",
    r"\bwest\s+falls?\s+(?:2|too|two)\b": "Westfall Stew",
    r"\bl[ao]ch\s*m[ao]d[ao]n(?:'s)?\b": "Loch Modan",
    r"\bred\s+ridge\b": "Redridge Mountains",
    r"\bdun\s+m[ao]r[ao]w?\b": "Dun Morogh",
    r"\bpal[ai]d[ai]ns?\b": "Paladin",
    r"\biron\s+forge\b": "Ironforge",
    r"\belm?w[yi]nn?\s+forest\b": "Elwynn Forest",
    r"\bintel\s+dr[ao]ss?il\b": "in Teldrassil",
    r"\btel\s*dr[ao]ss?il\b": "Teldrassil",
    r"\bdol[ai]n\s*(?:r|ar|aar)(?:\s+village)?\b": "Dolanaar",
    r"\bdellen(?:'s)?\s+earth\b": "Denalan's Earth",
    r"\bstar\s*bree(?:'?s|ze)?\s+village\b": "Starbreeze Village",
    r"\bdar\s*nass?us\b": "Darnassus",
    r"\bleather[\s\-]+working\b": "Leatherworking",
    r"\bfirst[\s\-]+aid\b": "First Aid",
    r"\bauber\s*dine\b": "Auberdine",
    r"\b[ck]al\s*dor[iey]+\b": "Kaldorei",
    r"\bsin\s*dor[iey]+\b": "Sin'dorei",
    r"\bren\s*dor[iey]+\b": "Ren'dorei",
    r"\bquel\s*dor[iey]+\b": "Quel'dorei",
    r"\b(?:nine|night)\s+savers?\b": "Nightsabers",
    r"\b(?:nomragon|no\s+morrigan|gnomragon)\b": "Gnomeregan",
    r"\b(?:archmaid\s+)?theocrat[iu]s\b": "Theocritus",
    r"\bsentinel\s+tower\b": "Sentinel Hill",
    r"\b(?:north\s+thane(?:\s+iron\s*forge)?|(?:the\s+)?halls?\s+of\s+th(?:ane|ain)s?)\b": "The Hall of Thanes",
    r"\bruins\s+of\s+lo(?:r?d|rd)[ae]?r[oa]n\b": "Ruins of Lordaeron",
    r"\blo(?:r?d|rd)[ae]?r[oa]n\b": "Lordaeron",
    r"\bexcavation\s+site(?:\s*:?\s*wetlands)?\b": "Excavation Site: Wetlands",
    r"\bkrol'?dok(?:\s+stronghold)?\b": "Krol'dok Stronghold",
    r"\bshaper'?s?\s+terrace\b": "Shaper's Terrace",
    r"\bshen'?dral[ao]s\b": "Shen'dralas",
    r"\bzephr[au]s\s+is(?:le|land)\b": "Zephras Isle",
    r"\bun\s*goro(?:\s+crater)?\b": "Un'Goro Crater",
    r"\bzul\s*farr?ak\b": "Zul'Farrak",
    r"\briver\s+glades\b": "Riverglades",
    r"\bfar\s*holde?\s+keep\b": "Farholde Keep",
    r"\b(?:rumi|rummy)\s+of\s+(?:nomragon|no\s+morrigan|gnomeregan)\b": "Gnomeregan",
    r"\barchmaid\b": "Archmage",
    r"\btheocratus(?:'s)?\b": "Theocritus",
    r"\bdry\s+times\b": "Dry Times",
    r"\bsymbol\s+of\s+hope\b": "Symbol of Life",
}


WORD_NUMS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20",
}


def normalize_stt(text: str) -> str:
    out = text
    for pat, repl in STT_ALIASES.items():
        out = re.sub(pat, repl, out, flags=re.I)
    out = re.sub(
        r"\b(level|lvl)\s+(" + "|".join(WORD_NUMS.keys()) + r")\b",
        lambda m: f"{m.group(1)} {WORD_NUMS[m.group(2).lower()]}",
        out,
        flags=re.I,
    )
    return out


def query_lore(conn: sqlite3.Connection, text: str, limit: int = 2) -> list[str]:
    try:
        import difflib
        cur = conn.cursor()
        norm_text = normalize_stt(text)
        tokens = [
            w for w in re.findall(r"[a-zA-Z0-9']+", norm_text.lower())
            if w.replace("'", "") not in LORE_STOPWORDS and len(w.replace("'", "")) > 2
        ]
        if not tokens:
            return []
        results = []
        seen = set()
        # 0. Multi-token overlap match (prioritizes guides/lore matching 2+ tokens in title/aliases, e.g. "fishing" + "loch modan" or "train" + "axes")
        if len(tokens) >= 2:
            phrase = " ".join(tokens)
            for r in cur.execute(
                "SELECT title, summary FROM lore WHERE lower(title) = ? OR lower(title) LIKE ? ORDER BY (lower(title) != ?), length(title) LIMIT 2",
                (phrase, f"%{phrase}%", phrase),
            ):
                if r["title"] not in seen:
                    seen.add(r["title"])
                    results.append(f"Lore ({r['title']}): {r['summary']}")
            stems = [t[:-1] if t.endswith("s") and len(t) > 4 else t for t in tokens if len(t) >= 4]
            if len(stems) >= 2 and len(results) < limit:
                scored = []
                for r in cur.execute("SELECT title, aliases, summary FROM lore"):
                    t_low = r["title"].lower()
                    a_low = (r["aliases"] or "").lower()
                    t_hits = sum(1 for s in stems if s in t_low)
                    a_hits = sum(1 for s in stems if s in a_low or s in t_low)
                    if a_hits >= 2:
                        scored.append((a_hits, t_hits, -len(t_low), r["title"], r["summary"]))
                scored.sort(reverse=True)
                for _, _, _, rtitle, rsum in scored[:limit]:
                    if rtitle not in seen and len(results) < limit:
                        seen.add(rtitle)
                        results.append(f"Lore ({rtitle}): {rsum}")
        # 1. Single-token exact title or word-boundary match
        for t in tokens:
            if len(results) >= limit:
                break
            stem = t[:-1] if t.endswith("s") and len(t) > 4 else t
            if len(stem) < 4:
                continue
            for r in cur.execute(
                """
                SELECT title, aliases, summary FROM lore
                WHERE lower(title) = ?
                   OR lower(title) = ?
                   OR lower(title) LIKE ?
                   OR lower(title) LIKE ?
                   OR lower(aliases) LIKE ?
                ORDER BY (lower(title) != ? AND lower(title) != ?), length(title)
                LIMIT 6
                """,
                (t, stem, f"{stem} %", f"% {stem}%", f"%{stem}%", t, stem),
            ):
                blob_w = f"{r['title']} {r['aliases'] or ''}".lower()
                if r["title"] not in seen and re.search(rf"\b{re.escape(stem)}s?\b", blob_w) and len(results) < limit:
                    seen.add(r["title"])
                    results.append(f"Lore ({r['title']}): {r['summary']}")
        # 2. Fuzzy title word match for STT phonetic spellings (e.g. "Silvanus" -> "Sylvanas")
        if not results:
            all_lore = cur.execute("SELECT title, summary FROM lore").fetchall()
            word_to_rows: dict[str, list] = {}
            for r in all_lore:
                for w in re.findall(r"[a-z0-9']+", r["title"].lower()):
                    if len(w) >= 5 and w not in LORE_STOPWORDS:
                        word_to_rows.setdefault(w, []).append(r)
            lore_vocab = list(word_to_rows.keys())
            for t in tokens:
                if len(t) >= 5:
                    matches = difflib.get_close_matches(t, lore_vocab, n=1, cutoff=0.78)
                    if matches:
                        for r in word_to_rows[matches[0]][:2]:
                            if r["title"] not in seen:
                                seen.add(r["title"])
                                results.append(f"Lore ({r['title']}): {r['summary']}")
        # 3. FTS5 fallback on lore_fts (only for full tokens >= 5 chars to avoid prefix noise)
        if len(results) < limit:
            fts_terms = [re.sub(r"[^a-z0-9]", "", t) for t in tokens if len(re.sub(r"[^a-z0-9]", "", t)) >= 5]
            if fts_terms:
                stem_q = " OR ".join(f'title : "{t}"' for t in fts_terms[:4])
                for r in cur.execute(
                    "SELECT title, summary FROM lore_fts WHERE lore_fts MATCH ? ORDER BY rank LIMIT ?",
                    (stem_q, limit),
                ):
                    if r["title"] not in seen:
                        seen.add(r["title"])
                        results.append(f"Lore ({r['title']}): {r['summary']}")
        return results[:limit]
    except Exception:
        return []


def auto_context(question: str, db_path: str = DEFAULT_DB) -> str:
    """
    Given a spoken or typed user query, returns a compact block of verified QuestieDB_Forever
    facts in <5ms, or '' if no relevant entities/zones match.
    """
    if not os.path.exists(db_path):
        return ""
    question = normalize_stt(question)
    q_lower = question.lower()

    # Detect level mention (prefer current character level when multiple levels are mentioned)
    curr_lvl = re.search(r"\b(?:currently|i'?m|i\s+am|we'?re|we\s+are|at)\s+(?:level|lvl)\s*(\d{1,2})\b", q_lower)
    all_lvls = [int(m.group(1)) for m in re.finditer(r"\b(?:level|lvl)\s*(\d{1,2})\b", q_lower)]
    level = int(curr_lvl.group(1)) if curr_lvl else (all_lvls[0] if all_lvls else None)

    # Detect all known zones/towns ordered by where they appear in the question
    q_for_zone = re.sub(r"\bqueen\s+azshara\b", "", q_lower)
    zone_hits: list[tuple[int, str]] = []
    for z in sorted(KNOWN_ZONES, key=len, reverse=True):
        short_z = z.lower().replace("the ", "").split(" mountains")[0].split(" vale")[0].split(" forest")[0].split(" glades")[0].split(" foothills")[0]
        m = re.search(rf"\b{re.escape(z.lower())}\b", q_for_zone) or (len(short_z) >= 6 and re.search(rf"\b{re.escape(short_z)}\b", q_for_zone))
        if m and all(z != ez for _, ez in zone_hits):
            zone_hits.append((m.start(), z))
    for town, z in TOWN_TO_ZONE.items():
        m = re.search(rf"\b{re.escape(town)}\b", q_for_zone)
        if m and all(z != ez for _, ez in zone_hits):
            zone_hits.append((m.start(), z))
    PROF_ZONES = {
        "Blacksmithing", "Alchemy", "Engineering", "Leatherworking", "Tailoring",
        "Cooking", "First Aid", "Fishing", "Herbalism", "Camping",
        "Paladin", "Warrior", "Hunter", "Rogue", "Priest", "Mage", "Warlock", "Druid", "Shaman",
    }
    zone_hits.sort(key=lambda x: (x[1] in PROF_ZONES, x[0]))
    matched_zones = [z for _, z in zone_hits]
    matched_zone = matched_zones[0] if matched_zones else None
    matched_hub = next((t for t in TOWN_HUB_COORDS if re.search(rf"\b{re.escape(t)}\b", q_for_zone)), None)

    try:
        conn = get_conn(db_path)
    except Exception:
        return ""

    lines = query_corrections(question)
    if matched_hub:
        hz, hx, hy, hdesc = TOWN_HUB_COORDS[matched_hub]
        lines.append(f"Landmark ({matched_hub.title()}): Located in {hz} at ({hx}, {hy}) \u2014 {hdesc}.")
    is_lore_q = bool(re.search(r"\b(lore|history|story|who\s+is|origin|origins|background|describe|explain|tell\s+me\s+about)\b", q_lower))
    lore_rows = query_lore(conn, question, limit=2 if is_lore_q or not matched_zones else 1)
    lines.extend(lore_rows)

    remainder = re.sub(r"\b(?:level|lvl)?\s*\d+\b", " ", q_lower)
    if matched_zone:
        for part in matched_zone.lower().split():
            remainder = re.sub(rf"\b{re.escape(part)}\b", " ", remainder)

    # If matched_zone is a class/profession category (not a geographic zone), prioritize specific FTS entity matches first
    if matched_zone in PROF_ZONES:
        for r in query_fts(conn, remainder, limit=6):
            if r not in lines:
                lines.append(r)

    if matched_zone:
        if re.search(r"\b(repair|vendor|ammo|arrows?|bullets?|buy|sell|merchant|inn|innkeeper|flight|gryphon|wyvern|stable|trainer|smith|bowyer|armorer|weaponsmith|quartermaster|supplies|goods|leatherwork|skinning|cooking|alchemy|herbalism|mining|tailoring|enchanting|first\s+aid|fishing|warrior|rogue|priest|hunter|druid|mage|warlock|paladin|shaman)\b", q_lower):
            prof_m = re.search(r"\b(leatherwork|skin|cook|alchem|herb|min|tailor|enchant|first\s+aid|fish|warrior|rogue|priest|hunter|druid|mage|warlock|paladin|shaman)", q_lower)
            prof_stem = prof_m.group(1) if prof_m else "___none___"
            if not prof_m:
                # Rank the requested service role first so LIMIT 18 can't drop it (e.g. Innkeeper Farley in Goldshire).
                for pat, role in ((r"\binn(?:keeper)?\b", "Innkeeper"), (r"\b(?:flight|gryphon|wyvern|hippogryph)\b", "Flight Master"),
                                  (r"\b(?:arrows?|bullets?|ammo|bowyer)\b", "Bowyer"), (r"\brepair\b", "Repair"), (r"\bstable\b", "Stable")):
                    if re.search(pat, q_lower):
                        prof_stem = role
                        break
            want_trainer = 1 if re.search(r"\b(trainer|train|learn|leatherwork|skin|cook|alchem|herb|min|tailor|enchant|first\s+aid)\b", q_lower) else 0
            npc_rows = conn.execute(
                """
                SELECT summary, subname, coords FROM npcs
                WHERE zone LIKE ? AND coords NOT LIKE '%(0.0, 0.0)%' AND name NOT LIKE 'World %'
                  AND (
                    subname LIKE '%Bowyer%' OR subname LIKE '%Gun%' OR subname LIKE '%Ammo%'
                    OR subname LIKE '%Armor%' OR subname LIKE '%Weapon%' OR subname LIKE '%Smith%'
                    OR subname LIKE '%General%' OR subname LIKE '%Trade%'
                    OR subname LIKE '%Quartermaster%' OR subname LIKE '%Vendor%' OR subname LIKE '%Innkeeper%'
                    OR subname LIKE '%Gryphon%' OR subname LIKE '%Flight%' OR subname LIKE '%Wyvern%'
                    OR subname LIKE '%Bat%' OR subname LIKE '%Trainer%' OR subname LIKE '%Stable%'
                  )
                ORDER BY (subname LIKE ?) DESC, (? AND subname LIKE '%Trainer%') DESC, (subname LIKE '%[Repair Vendor]%' OR subname LIKE '%[Flight Master]%' OR subname LIKE '%[Innkeeper]%') DESC, (subname LIKE '%Bowyer%' OR subname LIKE '%Gun%' OR subname LIKE '%Armor%' OR subname LIKE '%Smith%' OR subname LIKE '%General%' OR subname LIKE '%Trade%') DESC, id
                LIMIT 18
                """,
                (f"%{matched_zone}%", f"%{prof_stem}%", want_trainer),
            ).fetchall()
            if matched_hub and TOWN_HUB_COORDS[matched_hub][0] == matched_zone:
                import math
                _, hx, hy, _ = TOWN_HUB_COORDS[matched_hub]
                def _hub_sort_key(r):
                    sub_match = 0 if prof_stem.lower() in (r["subname"] or "").lower() else 1
                    m = re.search(r"\((\d+\.\d+),\s*(\d+\.\d+)\)", r["coords"] or "")
                    dist = math.hypot(float(m.group(1)) - hx, float(m.group(2)) - hy) if m else 999.0
                    return (sub_match, dist)
                npc_rows = sorted(npc_rows, key=_hub_sort_key)
            for r in npc_rows[:10]:
                s = r["summary"]
                if matched_hub and TOWN_HUB_COORDS[matched_hub][0] == matched_zone:
                    _, hx, hy, _ = TOWN_HUB_COORDS[matched_hub]
                    s = annotate_hub_direction(s, matched_hub.title(), matched_zone, hx, hy)
                if s not in lines:
                    lines.append(s)
        primary_limit = 10 if len(matched_zones) > 1 else 12
        z_rows = query_zone_quests(conn, matched_zone, level=level, limit=primary_limit)
        for r in z_rows:
            if r not in lines:
                lines.append(r)
        if len(matched_zones) > 1:
            sec_lvl = all_lvls[-1] if len(all_lvls) > 1 else level
            for r in query_zone_quests(conn, matched_zones[1], level=sec_lvl, limit=4):
                if r not in lines:
                    lines.append(r)

    # Also check specific entity keywords (removing the matched zone name and level numbers)
    fts_rows = query_fts(conn, remainder, limit=6)
    for r in fts_rows:
        if r not in lines:
            lines.append(r)

    conn.close()
    if not lines:
        return ""
    header = (
        "[Verified Local WoW Forever QuestieDB Data \u2014 Ground Truth Attached. "
        "Answer directly from these facts in ONE single turn WITHOUT calling web_search. "
        "Keep spoken replies concise (2-3 short sentences max, under 60 words) and answer ONLY for the zone in THIS question (ignore zones from earlier turns). "
        "NEVER output partial answer text before a tool_call]:"
    )
    return header + "\n- " + "\n- ".join(lines[:15])


def self_check():
    conn = get_conn()
    # 1. Verify Furlbrow's Deed (#184) is started by Item Drop Westfall Deed (#1972)
    deed = query_fts(conn, "Furlbrow's Deed", limit=3)
    assert any("Furlbrow's Deed (#184)" in r and "Item Drop 'Westfall Deed' (#1972)" in r for r in deed), f"Failed deed check: {deed}"
    # 2. Verify Westfall level 10 zone lookup returns both NPC and Item-Drop quests accurately
    wf10 = auto_context("Hey Alfred, I'm in Westfall at level 10, what quests can I get?")
    assert "Poor Old Blanchy" in wf10 and "The Forgotten Heirloom" in wf10 and "Furlbrow's Deed" in wf10, f"Failed wf10 check: {wf10}"
    # 3. Verify custom Forever zones & dungeons (Riverglades, Hall of Thanes, Excavation Site, Feralas)
    rg = auto_context("Hey Alfred, what quests are in Riverglades?")
    assert "Riverglades" in rg, f"Failed Riverglades check: {rg}"
    hot = auto_context("What about Halls of Thain?")
    assert "The Hall of Thanes" in hot and "Durgen Dirgehammer" in hot, f"Failed Hall of Thanes check: {hot}"
    esw = auto_context("What are the bosses and quests in Excavation Site Wetlands?")
    assert "Saltspine" in esw and "Zone: Excavation Site: Wetlands" in esw, f"Failed Excavation Site check: {esw}"
    fer = auto_context("What quests are in Feralas at level 42?")
    assert "Zone: Feralas |" in fer, f"Failed Feralas check: {fer}"
    # 4. Verify offline lore corpus & Class Quest Spells
    ml = auto_context("Hey Alfred, describe the history of the Murlocks")
    assert "Lore (Murloc):" in ml and "gorlocs" in ml.lower(), f"Failed Murloc lore check: {ml}"
    dr = auto_context("How do I get Cat Form as a Druid in WoW Forever?")
    assert "Class Quest Spells: Druid" in dr and "#98397" in dr, f"Failed Druid Class Quest Spells check: {dr}"
    conn.close()
    print("SELF-CHECK PASSED!")
    print("\n--- Sample Auto-Context Output for 'Westfall at level 10' ---")
    print(wf10)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser()
    p.add_argument("query", nargs="*", help="Search query or natural language question")
    p.add_argument("--zone", help="Filter by zone name")
    p.add_argument("--level", type=int, help="Filter by character level")
    p.add_argument("--self-check", action="store_true", help="Run verification assertions")
    args = p.parse_args()

    if args.self_check:
        self_check()
        sys.exit(0)

    conn = get_conn()
    if args.zone:
        for line in query_zone_quests(conn, args.zone, args.level, limit=15):
            print(line)
    elif args.query:
        q = " ".join(args.query)
        ctx = auto_context(q)
        if ctx:
            print(ctx)
        else:
            for line in query_fts(conn, q, limit=10):
                print(line)
    conn.close()
