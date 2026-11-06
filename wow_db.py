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
    "astranaar": "Ashenvale",
    "booty bay": "Stranglethorn Vale",
    "gadgetzan": "Tanaris",
}

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
    "City of Dalaran", "Scarlet Enclave", "The Deadmines", "Wailing Caverns",
    "Shadowfang Keep", "Blackfathom Deeps", "The Stockade", "Gnomeregan", "Razorfen Kraul",
    "Scarlet Monastery", "Razorfen Downs", "Uldaman", "Zul'Farrak", "Maraudon",
    "Blackrock Depths", "Blackrock Spire", "Scholomance", "Stratholme",
    "Blacksmithing", "Alchemy", "Engineering", "Leatherworking", "Tailoring",
    "Cooking", "First Aid", "Fishing", "Herbalism", "Camping",
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
    if level is not None:
        rows = cur.execute(
            """
            SELECT summary FROM quests
            WHERE zone LIKE ? AND req_level <= ? AND quest_level >= ?
            ORDER BY (prereqs != ''), ABS(quest_level - ?), req_level, id
            LIMIT ?
            """,
            (f"%{zone}%", level + 1, max(1, level - 3), level, limit),
        ).fetchall()
        if rows:
            return [r["summary"] for r in rows]
    rows = cur.execute(
        """
        SELECT summary FROM quests
        WHERE zone LIKE ?
        ORDER BY (prereqs != ''), quest_level, req_level, id
        LIMIT ?
        """,
        (f"%{zone}%", limit),
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
        or_q = " OR ".join(f'name : "{t[:5] if len(t) >= 6 else t}"*' for t in fts_terms[:5])
        for r in cur.execute(
            "SELECT summary FROM wow_fts WHERE wow_fts MATCH ? ORDER BY rank LIMIT ?",
            (or_q, limit),
        ):
            if r["summary"] not in seen:
                seen.add(r["summary"])
                results.append(r["summary"])

    return results[:limit]


LORE_STOPWORDS = STOPWORDS | {
    "history", "lore", "story", "origin", "origins", "background", "entire",
    "unabridged", "describe", "explain", "detail", "much", "possible", "everything",
    "interesting", "piece", "something", "most", "people", "anyone", "nobody",
    "knows", "fact", "facts", "cool", "fun", "random", "weird", "obscure", "classic",
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
    r"\bloch\s+m[ao]d[ao]n(?:'s)?\b": "Loch Modan",
    r"\bred\s+ridge\b": "Redridge Mountains",
    r"\bdun\s+m[ao]r[ao]w?\b": "Dun Morogh",
    r"\bpal[ai]d[ai]ns?\b": "Paladin",
    r"\biron\s+forge\b": "Ironforge",
    r"\belw[yi]nn?\s+forest\b": "Elwynn Forest",
    r"\b(?:nomragon|no\s+morrigan|gnomragon)\b": "Gnomeregan",
    r"\b(?:archmaid\s+)?theocrat[iu]s\b": "Theocritus",
    r"\bsentinel\s+tower\b": "Sentinel Hill",
}


def normalize_stt(text: str) -> str:
    out = text
    for pat, repl in STT_ALIASES.items():
        out = re.sub(pat, repl, out, flags=re.I)
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
        # 0. Multi-token phrase match first (e.g. "Queen Azshara", "Loch Modan", "Sentinel Hill")
        if len(tokens) >= 2:
            phrase = " ".join(tokens)
            for r in cur.execute(
                "SELECT title, summary FROM lore WHERE lower(title) = ? OR lower(title) LIKE ? OR lower(aliases) LIKE ? ORDER BY (lower(title) != ?), length(title) LIMIT 2",
                (phrase, f"%{phrase}%", f"%{phrase}%", phrase),
            ):
                if r["title"] not in seen:
                    seen.add(r["title"])
                    results.append(f"Lore ({r['title']}): {r['summary']}")
        # 1. Single-token exact title or word-boundary match
        for t in tokens:
            if len(results) >= limit:
                break
            stem = t[:-1] if t.endswith("s") and len(t) > 4 else t
            if len(stem) < 4:
                continue
            for r in cur.execute(
                """
                SELECT title, summary FROM lore
                WHERE lower(title) = ?
                   OR lower(title) = ?
                   OR lower(title) LIKE ?
                   OR lower(title) LIKE ?
                   OR lower(aliases) LIKE ?
                ORDER BY (lower(title) != ? AND lower(title) != ?), length(title)
                LIMIT 2
                """,
                (t, stem, f"{stem} %", f"% {stem}%", f"%{stem}%", t, stem),
            ):
                if r["title"] not in seen:
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
        # 3. FTS5 fallback on lore_fts (only for tokens >= 5 chars to avoid 4-char noise)
        if len(results) < limit:
            fts_terms = [re.sub(r"[^a-z0-9]", "", t) for t in tokens if len(re.sub(r"[^a-z0-9]", "", t)) >= 5]
            if fts_terms:
                stem_q = " OR ".join(f'title : "{t[:5]}"*' for t in fts_terms[:4])
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

    # Detect level mention ("level 10", "lvl 10", "10 in westfall")
    lvl_match = re.search(r"\b(?:level|lvl)\s*(\d{1,2})\b", q_lower)
    level = int(lvl_match.group(1)) if lvl_match else None

    # Detect known zone/profession mention (excluding "Queen Azshara" from triggering Azshara zone)
    q_for_zone = re.sub(r"\bqueen\s+azshara\b", "", q_lower)
    matched_zone = None
    for z in sorted(KNOWN_ZONES, key=len, reverse=True):
        short_z = z.lower().replace("the ", "").split(" mountains")[0].split(" vale")[0].split(" forest")[0].split(" glades")[0].split(" foothills")[0]
        if z.lower() in q_for_zone or (len(short_z) >= 6 and re.search(rf"\b{re.escape(short_z)}\b", q_for_zone)):
            matched_zone = z
            break
    if not matched_zone:
        for town, z in TOWN_TO_ZONE.items():
            if town in q_for_zone:
                matched_zone = z
                break

    try:
        conn = get_conn(db_path)
    except Exception:
        return ""

    lines = query_corrections(question)
    lore_rows = query_lore(conn, question, limit=2)
    lines.extend(lore_rows)

    if matched_zone:
        if re.search(r"\b(repair|vendor|ammo|arrows?|bullets?|buy|sell|merchant|inn|innkeeper|flight|gryphon|wyvern|stable|trainer|smith|bowyer|armorer|weaponsmith|quartermaster|supplies|goods)\b", q_lower):
            for r in conn.execute(
                """
                SELECT summary FROM npcs
                WHERE zone LIKE ?
                  AND (
                    subname LIKE '%Bowyer%' OR subname LIKE '%Gun%' OR subname LIKE '%Ammo%'
                    OR subname LIKE '%Armor%' OR subname LIKE '%Weapon%' OR subname LIKE '%Smith%'
                    OR subname LIKE '%General%' OR subname LIKE '%Trade%'
                    OR subname LIKE '%Quartermaster%' OR subname LIKE '%Vendor%' OR subname LIKE '%Innkeeper%'
                    OR subname LIKE '%Gryphon%' OR subname LIKE '%Flight%' OR subname LIKE '%Wyvern%'
                    OR subname LIKE '%Bat%' OR subname LIKE '%Trainer%' OR subname LIKE '%Stable%'
                  )
                ORDER BY (subname LIKE '%Bowyer%' OR subname LIKE '%Gun%' OR subname LIKE '%Armor%' OR subname LIKE '%Smith%' OR subname LIKE '%General%' OR subname LIKE '%Trade%' OR subname LIKE '%Innkeeper%' OR subname LIKE '%Gryphon%') DESC, id
                LIMIT 10
                """,
                (f"%{matched_zone}%",),
            ):
                if r["summary"] not in lines:
                    lines.append(r["summary"])
        z_rows = query_zone_quests(conn, matched_zone, level=level, limit=12)
        for r in z_rows:
            if r not in lines:
                lines.append(r)

    # Also check specific entity keywords (removing the matched zone name and level numbers)
    remainder = re.sub(r"\b(?:level|lvl)?\s*\d+\b", " ", q_lower)
    if matched_zone:
        for part in matched_zone.lower().split():
            remainder = re.sub(rf"\b{re.escape(part)}\b", " ", remainder)
    fts_rows = query_fts(conn, remainder, limit=6)
    for r in fts_rows:
        if r not in lines:
            lines.append(r)

    conn.close()
    if not lines:
        return ""
    header = (
        "[Verified Local WoW Forever QuestieDB Data — Ground Truth Attached. "
        "Answer directly from these facts in ONE single turn WITHOUT calling web_search "
        "(unless a specific detail is missing below). NEVER output partial answer text "
        "before a tool_call, as Discord voice only speaks your final message after all tool calls finish]:"
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
    # 3. Verify custom Forever zone (Riverglades) works
    rg = auto_context("Hey Alfred, what quests are in Riverglades?")
    assert "Riverglades" in rg, f"Failed Riverglades check: {rg}"
    # 4. Verify offline lore corpus (Murloc & Queen Azshara) works
    ml = auto_context("Hey Alfred, describe the history of the Murlocks")
    assert "Lore (Murloc):" in ml and "gorlocs" in ml.lower(), f"Failed Murloc lore check: {ml}"
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
