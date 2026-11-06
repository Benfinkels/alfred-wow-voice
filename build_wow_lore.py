#!/usr/bin/env python3
"""
Fetches ~350 core Warcraft lore & history articles from warcraft.wiki.gg's official
MediaWiki API and indexes them into `lore` and `lore_fts` inside `wow_forever.sqlite`.
"""
import concurrent.futures
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request

DB_PATH = os.environ.get("WOW_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "wow_forever.sqlite"))

LORE_TOPICS = [
    # Races, Creatures & Monster Families
    "Murloc", "Gorloc", "Naga", "Gnoll", "Kobold", "Trogg", "Quilboar", "Centaur",
    "Furbolg", "Satyr", "Ogre", "Troll", "Dark troll", "Jungle troll", "Forest troll",
    "Ice troll", "Sand troll", "Zandalari troll", "Night elf", "Highborne", "High elf",
    "Blood elf", "Dwarf", "Bronzebeard clan", "Wildhammer clan", "Dark Iron clan",
    "Earthen", "Gnome", "Leper gnome", "Mechagnome", "Human", "Orc", "Tauren",
    "Forsaken", "Undead", "Scourge", "Burning Legion", "Dragonflight", "Red dragonflight",
    "Blue dragonflight", "Bronze dragonflight", "Green dragonflight", "Black dragonflight",
    "Chromatic dragonflight", "Old God", "Titan", "Titan-forged", "Curse of Flesh",
    "Elemental Lord", "Worgen", "Nerubian", "Qiraji", "Silithid", "Goblin", "Harpy",
    "Dryad", "Keeper of the Grove", "Wild God", "Ancient Guardian", "Makrura",
    "Sea giant", "Mountain giant", "Gargoyle", "Abomination", "Lich", "Dreadlord",
    "Nathrezim", "Pit lord", "Doomguard", "Felhunter", "Imp", "Succubus", "Voidwalker",
    "Infernal", "Moonkin", "Wildkin", "Hippogryph", "Gryphon", "Wyvern", "Chimaera",
    "Faerie dragon", "Treant", "Anubisath", "Raptor", "Kodo", "Wind serpent",
    # Major Lore Figures
    "Queen Azshara", "Edwin VanCleef", "Arthas Menethil", "Lich King", "Ner'zhul",
    "Kel'Thuzad", "Sylvanas Windrunner", "Illidan Stormrage", "Malfurion Stormrage",
    "Tyrande Whisperwind", "Cenarius", "Fandral Staghelm", "Shandris Feathermoon",
    "Maiev Shadowsong", "Thrall", "Grommash Hellscream", "Orgrim Doomhammer", "Durotan",
    "Gul'dan", "Blackhand", "Kil'jaeden", "Archimonde", "Sargeras", "Medivh", "Aegwynn",
    "Khadgar", "Antonidas", "Jaina Proudmoore", "Daelin Proudmoore", "Anduin Lothar",
    "Llane Wrynn I", "Varian Wrynn", "Bolvar Fordragon", "Katrana Prestor", "Onyxia",
    "Nefarian", "Deathwing", "Alexstrasza", "Ysera", "Malygos", "Nozdormu", "Ragnaros",
    "Neptulon", "Al'Akir", "Therazane", "C'Thun", "Yogg-Saron", "N'Zoth",
    "Uther the Lightbringer", "Tirion Fordring", "Alexandros Mograine", "Renault Mograine",
    "Sally Whitemane", "Herod", "Balnazzar", "Varimathras", "Detheroc", "Othmar Garithos",
    "Terenas Menethil II", "Genn Greymane", "Archmage Arugal", "Thoradin",
    "Magni Bronzebeard", "Muradin Bronzebeard", "Brann Bronzebeard", "Moira Thaurissan",
    "Dagran Thaurissan", "Gelbin Mekkatorque", "Sicco Thermaplugg", "Cairne Bloodhoof",
    "Vol'jin", "Sen'jin", "Zalazane", "Rexxar", "Nathanos Blightcaller", "Morbent Fel",
    "Stalvan Mistmantle", "Morgan Ladimore", "Gryan Stoutmantle", "Farmer Furlbrow",
    "Old Blanchy", "Hogger", "Bellygrub", "Rhahk'Zor", "Sneed", "Gilnid", "Mr. Smite",
    "Captain Greenskin", "Cookie", "Lord Cobrahn", "Lady Anacondra", "Lord Pythas",
    "Lord Serpentis", "Mutanus the Devourer", "Hemet Nesingwary", "King Bangalash",
    "Hakkar the Soulflayer", "Jin'do the Hexxer", "Bloodlord Mandokir", "Shade of Eranikus",
    "Princess Theradras", "Archaedas", "Ironaya", "Chief Ukorz Sandscalp",
    "Baron Rivendare", "Ras Frostwhisper", "Darkmaster Gandling", "Jandice Barov",
    "Vaelastrasz", "Broodlord Lashlayer", "Chromaggus", "Majordomo Executus",
    "Baron Geddon", "Garr", "Sulfuron Harbinger", "Golemagg the Incinerator",
    "Magmadar", "Lucifron", "Gehennas", "Anub'Rekhan", "Grand Widow Faerlina",
    "Maexxna", "Noth the Plaguebringer", "Heigan the Unclean", "Loatheb",
    "Instructor Razuvious", "Gothik the Harvester", "Four Horsemen", "Patchwerk",
    "Grobbulus", "Gluth", "Thaddius", "Sapphiron", "Xavius", "Mannoroth", "Tichondrius",
    "Mal'Ganis", "Anub'arak", "Kael'thas Sunstrider", "Lady Vashj", "Akama",
    # Factions, Empires & Organizations
    "Alliance", "Horde", "Defias Brotherhood", "Scarlet Crusade", "Argent Dawn",
    "Knights of the Silver Hand", "Syndicate", "Ravenholdt", "Shadow Council",
    "Twilight's Hammer", "Venture Company", "Bloodsail Buccaneers", "Blackwater Raiders",
    "Steamwheedle Cartel", "Cenarion Circle", "Earthen Ring", "Thorium Brotherhood",
    "Hydraxian Waterlords", "Zandalar Tribe", "Brood of Nozdormu", "Timbermaw Hold",
    "Frostwolf clan", "Stormpike Guard", "Warsong Outriders", "Silverwing Sentinels",
    "The Defilers", "League of Arathor", "SI:7", "Kirin Tor", "Cult of the Damned",
    "Grimtotem tribe", "Burning Blade clan", "Shattered Hand clan", "Blackrock clan",
    "Dragonmaw clan", "Bleeding Hollow clan", "Warsong clan", "People's Militia",
    "Night's Watch", "Gurubashi Empire", "Amani Empire", "Kaldorei Empire", "Arathor",
    "Kingdom of Stormwind", "Kingdom of Lordaeron", "Kingdom of Gilneas",
    "Kingdom of Kul Tiras", "Kingdom of Dalaran", "Kingdom of Alterac",
    "Kingdom of Stromgarde", "Quel'Thalas", "Stonemasons Guild", "House of Nobles",
    # Zones & Cities
    "Stormwind City", "Ironforge", "Darnassus", "Orgrimmar", "Thunder Bluff", "Undercity",
    "Dalaran", "Elwynn Forest", "Westfall", "Redridge Mountains", "Duskwood", "Loch Modan",
    "Dun Morogh", "Wetlands", "Arathi Highlands", "Hillsbrad Foothills",
    "Alterac Mountains", "The Hinterlands", "Tirisfal Glades", "Silverpine Forest",
    "Western Plaguelands", "Eastern Plaguelands", "Stranglethorn Vale", "Swamp of Sorrows",
    "Blasted Lands", "Badlands", "Searing Gorge", "Burning Steppes", "Deadwind Pass",
    "Teldrassil", "Darkshore", "Ashenvale", "Stonetalon Mountains", "Desolace", "Feralas",
    "Thousand Needles", "The Barrens", "Durotar", "Mulgore", "Dustwallow Marsh", "Tanaris",
    "Un'Goro Crater", "Silithus", "Felwood", "Winterspring", "Moonglade", "Azshara",
    "Mount Hyjal", "Goldshire", "Sentinel Hill", "Moonbrook", "Lakeshire", "Darkshire",
    "Thelsamar", "Menethil Harbor", "Southshore", "Tarren Mill", "Booty Bay", "Gadgetzan",
    "Ratchet", "Everlook", "Crossroads", "Razor Hill", "Brill", "astranaar", "Auberdine",
    # Dungeons & Raids
    "Deadmines", "Wailing Caverns", "Shadowfang Keep", "Blackfathom Deeps", "The Stockade",
    "Gnomeregan", "Razorfen Kraul", "Scarlet Monastery", "Razorfen Downs", "Uldaman",
    "Zul'Farrak", "Maraudon", "Temple of Atal'Hakkar", "Blackrock Depths",
    "Blackrock Spire", "Dire Maul", "Scholomance", "Stratholme", "Molten Core",
    "Onyxia's Lair", "Blackwing Lair", "Zul'Gurub", "Ruins of Ahn'Qiraj",
    "Temple of Ahn'Qiraj", "Naxxramas", "Karazhan", "Blackrock Mountain",
    # Wars, Artifacts, & Cosmology
    "War of the Ancients", "Great Sundering", "Well of Eternity", "First War",
    "Second War", "Third War", "War of the Three Hammers", "War of the Shifting Sands",
    "Troll Wars", "Gurubashi War", "Gnoll War", "Battle of Mount Hyjal",
    "Opening of the Dark Portal", "Plague of Undeath", "Culling of Stratholme",
    "Ashbringer", "Frostmourne", "Sulfuras, Hand of Ragnaros",
    "Thunderfury, Blessed Blade of the Windseeker", "Atiesh, Greatstaff of the Guardian",
    "Scepter of the Shifting Sands", "Dark Portal", "Emerald Dream", "Twisting Nether",
    "Light", "Void", "Arcane", "Fel", "Necromancy", "Shaman", "Druid", "Paladin",
    "Warlock", "Priest", "Mage", "Rogue", "Hunter", "Warrior",
    # Classic In-Game Lore Books
    "The Old Gods and the Ordering of Azeroth", "Charge of the Dragonflights",
    "Mount Hyjal and Illidan's Gift", "The World Tree and the Emerald Dream",
    "Exile of the High Elves", "The Sentinels and the Long Vigil",
    "The Founding of Quel'Thalas", "Arathor and the Troll Wars",
    "The Guardians of Tirisfal", "Ironforge - the Awakening of the Dwarves",
    "The Last Guardian", "Beyond the Dark Portal", "The Tomb of Sargeras",
    "Rise of the Horde", "The Dark Portal and the Fall of Stormwind",
    "The Alliance of Lordaeron", "The Battle of Grim Batol", "Lethargy of the Orcs",
    "The New Horde", "The Birth of the Lich King", "Icecrown and the Frozen Throne",
    "Civil War in the Plaguelands", "The Lich King Triumphant",
    "Kel'Thuzad and the Forming of the Scourge", "Kil'jaeden and the Shadow Pact",
    "Archimonde's Return and the Flight to Kalimdor", "The Betrayer Ascendant",
    "Rise of the Blood Elves", "Old Hatreds - The Colonization of Kalimdor",
    "The Empire of Zul", "Wrath of the Soulflayer", "The Twin Empires",
    "The Fall of the Gurubashi Empire", "Mythology of the Titans",
    "Aegwynn and the Dragon Hunt", "Aftermath of the Second War",
    "The Kaldorei and the Well of Eternity", "Sargeras and the Betrayal",
    "The Seven Kingdoms",
]


def compress_extract(title: str, raw: str) -> str:
    """Keep the intro + key History/Background/Origin/Biology/Culture sections (~2,200 chars max)."""
    if not raw:
        return ""
    # Clean audio/media markers and excessive blank lines
    text = re.sub(r"\(MP3 sound\)", "", raw)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    # Split into sections by == Header ==
    parts = re.split(r"(^==+\s*[^=]+?\s*==+$)", text, flags=re.M)
    intro = parts[0].strip()
    out = [intro] if intro else []
    cur_len = len(intro)

    PRIORITY_HEADERS = (
        "history", "background", "origin", "introduction", "biography",
        "ancient", "war of the ancients", "sundering", "first war", "second war",
        "third war", "world of warcraft", "classic", "biology", "culture",
        "organization", "members", "overview", "in-game", "text", "content",
    )
    SKIP_HEADERS = (
        "patch changes", "external links", "references", "see also", "gallery",
        "notes", "trivia", "hearthstone", "heroes of the storm", "warcraft iii",
        "the war within", "dragonflight", "shadowlands", "battle for azeroth",
        "legion", "warlords of draenor", "mists of pandaria", "cataclysm",
    )

    for i in range(1, len(parts) - 1, 2):
        hdr = parts[i].strip()
        body = parts[i + 1].strip()
        hdr_clean = hdr.strip("=").strip().lower()
        if not body or any(s in hdr_clean for s in SKIP_HEADERS):
            continue
        if any(p in hdr_clean for p in PRIORITY_HEADERS) or cur_len < 900:
            chunk = f"[{hdr.strip('=').strip()}] " + re.sub(r"\s+", " ", body)
            if cur_len + len(chunk) > 2400:
                rem = max(0, 2400 - cur_len)
                if rem > 200:
                    out.append(chunk[:rem].rsplit(".", 1)[0] + ".")
                break
            out.append(chunk)
            cur_len += len(chunk)

    res = re.sub(r"\s+", " ", " | ".join(out)).strip()
    # Strip post-Classic / Cataclysm / Retail clauses and sentences so Classic lore stays pure
    res = re.sub(r",?\s*until the cataclysm,", "", res, flags=re.I)
    res = re.sub(r"\bnow broken\s+(?=Stonewrought Dam)", "", res, flags=re.I)
    # Remove individual sentences referencing post-Classic expansions/events
    sentences = re.split(r"(?<=[.!?])\s+", res)
    clean_sentences = [
        s for s in sentences
        if not re.search(
            r"\b(?:Cataclysm|Deathwing burst|Fourth War|War Against the Jailer|the Jailer|Battle for Azeroth|Shadowlands|Dragonflight|The War Within|Siege of Orgrimmar|Pandaria Campaign|alternate\s+(?:version\s+of\s+)?Draenor|Garrosh(?:'s)?\s+(?:capture|Hellscream\s+as\s+Warchief)|Northrend\s+campaign)\b",
            s,
            re.I,
        )
    ]
    res = " ".join(clean_sentences).strip(" |")
    return res[:2500]


def fetch_topic(topic: str) -> tuple[str, str, str]:
    url = (
        "https://warcraft.wiki.gg/api.php?action=query&prop=extracts"
        "&explaintext=1&redirects=1&format=json&titles="
        + urllib.parse.quote(topic)
    )
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WoWForeverLoreBuilder/1.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            pages = data.get("query", {}).get("pages", {})
            for pid, p in pages.items():
                if pid == "-1" or "extract" not in p:
                    return (topic, "", "")
                actual_title = p.get("title", topic)
                summary = compress_extract(actual_title, p.get("extract", ""))
                return (topic, actual_title, summary)
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return (topic, "", "")


def fetch_batch_intros(topics: list[str]) -> dict[str, tuple[str, str]]:
    """Fetch up to 20 pages in a single MediaWiki API request (avoids 429 rate limits)."""
    joined = "|".join(topics)
    url = (
        "https://warcraft.wiki.gg/api.php?action=query&prop=extracts"
        "&exintro=1&explaintext=1&exlimit=max&redirects=1&format=json&titles="
        + urllib.parse.quote(joined, safe="|")
    )
    out = {}
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WoWForeverLoreBuilder/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            q = data.get("query", {})
            # Map redirects/normalized back to requested topic
            norm_map = {n["to"]: n["from"] for n in q.get("normalized", [])}
            redir_map = {r["to"]: norm_map.get(r["from"], r["from"]) for r in q.get("redirects", [])}
            for pid, p in q.get("pages", {}).items():
                if pid == "-1" or not p.get("extract"):
                    continue
                actual_title = p.get("title", "")
                req_topic = redir_map.get(actual_title, norm_map.get(actual_title, actual_title))
                summary = compress_extract(actual_title, p["extract"])
                if summary:
                    out[actual_title] = (req_topic, summary)
            return out
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return out


def main():
    t0 = time.time()
    unique_topics = list(dict.fromkeys(LORE_TOPICS))
    conn = sqlite3.connect(DB_PATH)
    # Keep any existing rich extracts already in `lore`
    existing: dict[str, dict] = {}
    try:
        for title, aliases, summary in conn.execute("SELECT title, aliases, summary FROM lore"):
            existing[title] = {
                "aliases": set(a.strip() for a in aliases.split(",") if a.strip()),
                "summary": compress_extract(title, summary),
            }
    except Exception:
        pass

    missing = [t for t in unique_topics if t not in existing]
    print(f"Have {len(existing)} full-section articles; batch-fetching {len(missing)} remaining topics in 20-title chunks...")

    for i in range(0, len(missing), 20):
        batch = missing[i : i + 20]
        fetched = fetch_batch_intros(batch)
        for actual_title, (req_topic, summary) in fetched.items():
            if actual_title not in existing or len(summary) > len(existing[actual_title]["summary"]):
                existing.setdefault(actual_title, {"aliases": set(), "summary": summary})
                existing[actual_title]["summary"] = summary
            if req_topic.lower() != actual_title.lower():
                existing[actual_title]["aliases"].add(req_topic)
        time.sleep(0.25)

    conn.execute("DROP TABLE IF EXISTS lore")
    conn.execute("DROP TABLE IF EXISTS lore_fts")
    conn.execute(
        """
        CREATE TABLE lore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT UNIQUE,
            aliases TEXT,
            summary TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE lore_fts USING fts5(
            title,
            aliases,
            summary,
            content='lore',
            content_rowid='id'
        )
        """
    )

    rows = [
        (title, ", ".join(sorted(info["aliases"])), info["summary"])
        for title, info in sorted(existing.items())
    ]
    conn.executemany("INSERT INTO lore (title, aliases, summary) VALUES (?, ?, ?)", rows)
    conn.execute("INSERT INTO lore_fts(lore_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()

    size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    print(
        f"Indexed {len(rows)} unique lore articles into {DB_PATH} ({size_mb:.2f} MB) in {time.time() - t0:.1f}s!"
    )


if __name__ == "__main__":
    main()
