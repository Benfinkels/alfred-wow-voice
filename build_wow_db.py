#!/usr/bin/env python3
"""
Builds wow_forever.sqlite from QuestieDB_Forever.toc + Forever support tables.
Zero external dependencies (pure-Python CBOR decoder + sqlite3 FTS5).
"""
import base64
import os
import re
import sqlite3
import struct
import time
from collections import defaultdict

ADDON_DIR = os.environ.get("QUESTIE_ADDON_DIR", r"C:\Program Files (x86)\World of Warcraft\_forever_\Interface\AddOns\QuestieDB")
TOC_PATH = os.path.join(ADDON_DIR, "QuestieDB_Forever.toc")
ZONES_LUA = os.path.join(ADDON_DIR, r"src\corrections\enum\zones.lua")
QUESTS_LUA = os.path.join(ADDON_DIR, r"src\corrections\enum\quests.lua")
SUBZONES_LUA = os.path.join(ADDON_DIR, r"support\Forever\Zones\subZoneToParentZone.lua")
DROPS_LUA = os.path.join(ADDON_DIR, r"support\Forever\DropTables\classicItemDrops.lua")
DROP_CORR_LUA = os.path.join(ADDON_DIR, r"support\Forever\DropTables\itemDropCorrections.lua")
OUT_DB = os.environ.get("WOW_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "wow_forever.sqlite"))


# --- Pure-Python RFC 8949 CBOR Decoder ---
def cbor_decode(data: bytes, offset: int = 0):
    b = data[offset]
    offset += 1
    major = b >> 5
    info = b & 0x1F

    if info < 24:
        val = info
    elif info == 24:
        val = data[offset]
        offset += 1
    elif info == 25:
        val = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 2
    elif info == 26:
        val = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
    elif info == 27:
        val = struct.unpack(">Q", data[offset : offset + 8])[0]
        offset += 8
    elif info == 31:
        # Indefinite length
        if major == 4:
            arr = []
            while data[offset] != 0xFF:
                item, offset = cbor_decode(data, offset)
                arr.append(item)
            return arr, offset + 1
        elif major == 5:
            mp = {}
            while data[offset] != 0xFF:
                k, offset = cbor_decode(data, offset)
                v, offset = cbor_decode(data, offset)
                mp[k] = v
            return mp, offset + 1
        raise ValueError(f"Unsupported indefinite major {major}")
    else:
        raise ValueError(f"Unsupported CBOR info {info}")

    if major == 0:
        return val, offset
    elif major == 1:
        return -1 - val, offset
    elif major == 2:
        raw = data[offset : offset + val]
        try:
            return raw.decode("utf-8"), offset + val
        except UnicodeDecodeError:
            return raw.decode("latin-1", errors="replace"), offset + val
    elif major == 3:
        raw = data[offset : offset + val]
        return raw.decode("utf-8", errors="replace"), offset + val
    elif major == 4:
        arr = []
        for _ in range(val):
            item, offset = cbor_decode(data, offset)
            arr.append(item)
        return arr, offset
    elif major == 5:
        mp = {}
        for _ in range(val):
            k, offset = cbor_decode(data, offset)
            v, offset = cbor_decode(data, offset)
            mp[k] = v
        return mp, offset
    elif major == 6:
        # Semantic tag
        return cbor_decode(data, offset)
    elif major == 7:
        if info == 20:
            return False, offset
        elif info == 21:
            return True, offset
        elif info in (22, 23):
            return None, offset
        elif info == 25:
            return struct.unpack(">e", data[offset - 2 : offset])[0], offset
        elif info == 26:
            return struct.unpack(">f", data[offset - 4 : offset])[0], offset
        elif info == 27:
            return struct.unpack(">d", data[offset - 8 : offset])[0], offset
        return val, offset


def clean_symbol_name(sym: str) -> str:
    sym = re.sub(r"_FOREVER$", "", sym)
    words = sym.replace("_", " ").title().split()
    small = {"Of", "The", "In", "And", "To", "For", "On", "At"}
    out = [w if i == 0 or w not in small else w.lower() for i, w in enumerate(words)]
    return " ".join(out).replace("Un Goro", "Un'Goro").replace("Zul ", "Zul'").replace("Ahn Qiraj", "Ahn'Qiraj")


def load_zone_and_sort_names():
    zones = {}
    # 1. Parse zones.lua
    with open(ZONES_LUA, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(-?\d+)\s*,", line)
            if m:
                sym, zid = m.group(1), int(m.group(2))
                if zid not in zones:
                    zones[zid] = clean_symbol_name(sym)

    # 2. Parse quests.lua sortKeys (negative zoneOrSort values)
    with open(QUESTS_LUA, "r", encoding="utf-8") as f:
        in_sort = False
        for line in f:
            if "constants.sortKeys" in line:
                in_sort = True
            elif in_sort and line.startswith("}"):
                in_sort = False
            elif in_sort:
                m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(-\d+)\s*,", line)
                if m:
                    sym, sid = m.group(1), int(m.group(2))
                    zones[sid] = clean_symbol_name(sym)

    # 3. Parse subZoneToParentZone.lua comments for exact human names & parent mapping
    sub_to_parent = {}
    with open(SUBZONES_LUA, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*\[(\d+)\]\s*=\s*(\d+),\s*--\s*(.+?)(?:\s*->\s*(.+))?$", line)
            if m:
                sub_id = int(m.group(1))
                parent_id = int(m.group(2))
                sub_name = m.group(3).strip()
                parent_name = m.group(4).strip() if m.group(4) else None
                sub_to_parent[sub_id] = parent_id
                if parent_name and not parent_name.endswith("UNUSED"):
                    zones[parent_id] = parent_name
                if sub_name and not sub_name.endswith("UNUSED"):
                    zones[sub_id] = sub_name

    def resolve_zone(zid: int) -> str:
        if not zid:
            return "Unknown"
        name = zones.get(zid, f"Zone #{zid}")
        parent_id = sub_to_parent.get(zid)
        if parent_id and parent_id != zid:
            parent_name = zones.get(parent_id)
            if parent_name and parent_name != name:
                return f"{parent_name} ({name})"
        return name

    return resolve_zone


def load_drop_rates():
    # Parse classicItemDrops.lua and itemDropCorrections.lua: [itemId] = { [npcId] = rate, ... }
    drops = defaultdict(dict)
    for path in (DROPS_LUA, DROP_CORR_LUA):
        if not os.path.exists(path):
            continue
        cur_item = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                m_item = re.match(r"^\s*\[(\d+)\]\s*=\s*\{", line)
                if m_item:
                    cur_item = int(m_item.group(1))
                    continue
                if cur_item is not None:
                    if line.strip().startswith("}"):
                        cur_item = None
                    else:
                        m_npc = re.match(r"^\s*\[(\d+)\]\s*=\s*([0-9.]+)", line)
                        if m_npc:
                            drops[cur_item][int(m_npc.group(1))] = float(m_npc.group(2))
    return drops


def decode_faction(race_mask: int) -> str:
    if not race_mask or race_mask == 0:
        return "Both"
    # Forever Alliance bits: Human(1) + Dwarf(4) + NightElf(8) + Gnome(64) + Draenei(1024) + Worgen(2097152) + SkyborneA(4294967296)
    alliance_mask = 1 | 4 | 8 | 64 | 1024 | 2097152 | 16777216 | 4294967296
    # Forever Horde bits: Orc(2) + Undead(16) + Tauren(32) + Troll(128) + Goblin(256) + BloodElf(512) + SkyborneH(8589934592)
    horde_mask = 2 | 16 | 32 | 128 | 256 | 512 | 33554432 | 8589934592
    has_a = bool(race_mask & alliance_mask)
    has_h = bool(race_mask & horde_mask)
    if has_a and has_h:
        return "Both"
    if has_a:
        races = []
        for bit, rname in [(1, "Human"), (4, "Dwarf"), (8, "Night Elf"), (64, "Gnome"), (4294967296, "Skyborne")]:
            if race_mask & bit:
                races.append(rname)
        return "Alliance" if len(races) >= 4 else f"Alliance ({', '.join(races)})"
    if has_h:
        races = []
        for bit, rname in [(2, "Orc"), (16, "Undead"), (32, "Tauren"), (128, "Troll"), (256, "Goblin"), (8589934592, "Skyborne")]:
            if race_mask & bit:
                races.append(rname)
        return "Horde" if len(races) >= 4 else f"Horde ({', '.join(races)})"
    return "Both"


def decode_classes(class_mask: int) -> str:
    if not class_mask or class_mask in (0, 1503, 1535, 2047):
        return "All"
    cmap = [
        (1, "Warrior"), (2, "Paladin"), (4, "Hunter"), (8, "Rogue"),
        (16, "Priest"), (32, "Death Knight"), (64, "Shaman"), (128, "Mage"),
        (256, "Warlock"), (512, "Monk"), (1024, "Druid"),
    ]
    out = [name for bit, name in cmap if class_mask & bit]
    return ", ".join(out) if out else "All"


def parse_toc():
    t0 = time.time()
    raw_kv = {}
    pat = re.compile(r"^##\s+(X-(?:Quest|Npc|Item|Object)-\S+):\s*(\S+)\s*$")
    with open(TOC_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("## X-"):
                m = pat.match(line)
                if m:
                    raw_kv[m.group(1)] = m.group(2)

    print(f"Loaded {len(raw_kv):,} raw metadata lines in {time.time() - t0:.2f}s")

    entities = {
        "Quest": defaultdict(dict),
        "Npc": defaultdict(dict),
        "Item": defaultdict(dict),
        "Object": defaultdict(dict),
    }

    key_pat = re.compile(r"^X-(Quest|Npc|Item|Object)-(\d+)-(S|\d+)$")
    chunk_pat = re.compile(r"^~(\d+)~$")

    for k, v in raw_kv.items():
        m = key_pat.match(k)
        if not m:
            continue
        kind, eid_str, field = m.group(1), m.group(2), m.group(3)
        eid = int(eid_str)

        cm = chunk_pat.match(v)
        if cm:
            n_chunks = int(cm.group(1))
            b64_str = "".join(raw_kv.get(f"{k}-{i}", "") for i in range(1, n_chunks + 1))
        else:
            b64_str = v

        try:
            decoded, _ = cbor_decode(base64.b64decode(b64_str))
        except Exception:
            continue

        if field == "S" and isinstance(decoded, dict):
            for sk, sv in decoded.items():
                if isinstance(sk, int):
                    entities[kind][eid][sk] = sv
        elif field != "S":
            entities[kind][eid][int(field)] = decoded

    print(
        f"Decoded entities -> Quests: {len(entities['Quest']):,}, "
        f"NPCs: {len(entities['Npc']):,}, Items: {len(entities['Item']):,}, "
        f"Objects: {len(entities['Object']):,}"
    )
    return entities


def lua_idx(c, idx1):
    """Access 1-based Lua table index whether CBOR decoded it as a list or a sparse dict."""
    if isinstance(c, list):
        return c[idx1 - 1] if 0 <= idx1 - 1 < len(c) else None
    if isinstance(c, dict):
        return c.get(idx1)
    return None


def lua_list(c):
    """Iterate values of a Lua list/table whether CBOR decoded it as a list or a dict."""
    if isinstance(c, list):
        return c
    if isinstance(c, dict):
        return [v for _, v in sorted(c.items(), key=lambda kv: kv[0] if isinstance(kv[0], int) else 0)]
    return []


def format_spawns(spawns_dict, fallback_zid, resolve_zone) -> tuple[str, str]:
    """Returns (primary_zone_str, coords_str)"""
    if isinstance(spawns_dict, list):
        spawns_dict = {i + 1: v for i, v in enumerate(spawns_dict) if v}
    if not isinstance(spawns_dict, dict) or not spawns_dict:
        z = resolve_zone(fallback_zid) if fallback_zid else "Unknown"
        return z, ""
    parts = []
    primary_zone = None
    for zid, coords in spawns_dict.items():
        if not isinstance(zid, int):
            continue
        zname = resolve_zone(zid)
        if primary_zone is None:
            primary_zone = zname
        clist = lua_list(coords)
        if clist:
            first = lua_list(clist[0])
            if len(first) >= 2 and isinstance(first[0], (int, float)) and first[0] != -1:
                parts.append(f"{zname} ({first[0]:.1f}, {first[1]:.1f})")
            else:
                parts.append(zname)
        else:
            parts.append(zname)
    return primary_zone or (resolve_zone(fallback_zid) if fallback_zid else "Unknown"), "; ".join(parts[:3])


def build_sqlite():
    resolve_zone = load_zone_and_sort_names()
    drop_rates = load_drop_rates()
    entities = parse_toc()

    quests = entities["Quest"]
    npcs = entities["Npc"]
    items = entities["Item"]
    objects = entities["Object"]

    if os.path.exists(OUT_DB):
        os.remove(OUT_DB)

    conn = sqlite3.connect(OUT_DB)
    cur = conn.cursor()
    cur.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;

        CREATE TABLE quests (
            id INTEGER PRIMARY KEY,
            name TEXT,
            zone TEXT,
            req_level INTEGER,
            quest_level INTEGER,
            faction TEXT,
            classes TEXT,
            started_by TEXT,
            finished_by TEXT,
            objectives TEXT,
            prereqs TEXT,
            next_quest TEXT,
            summary TEXT
        );

        CREATE TABLE npcs (
            id INTEGER PRIMARY KEY,
            name TEXT,
            subname TEXT,
            zone TEXT,
            min_level INTEGER,
            max_level INTEGER,
            faction TEXT,
            coords TEXT,
            starts_quests TEXT,
            ends_quests TEXT,
            summary TEXT
        );

        CREATE TABLE items (
            id INTEGER PRIMARY KEY,
            name TEXT,
            req_level INTEGER,
            item_level INTEGER,
            starts_quest TEXT,
            dropped_by TEXT,
            sold_by TEXT,
            summary TEXT
        );

        CREATE VIRTUAL TABLE wow_fts USING fts5(
            kind,
            entity_id UNINDEXED,
            name,
            zone,
            summary,
            tokenize='porter unicode61'
        );
        """
    )

    # Pre-format NPCs
    npc_loc_str = {}
    npc_rows = []
    fts_rows = []

    for nid, d in sorted(npcs.items()):
        name = d.get(1) or f"NPC #{nid}"
        subname = d.get(14) or ""
        min_lvl = d.get(4) or 0
        max_lvl = d.get(5) or min_lvl
        faction = str(d.get(13) or "Both")
        primary_zone, coords_str = format_spawns(d.get(7), d.get(9), resolve_zone)
        npc_loc_str[nid] = f"{name} ({coords_str or primary_zone})"

        q_starts = [f"{quests[qid].get(1, f'Quest #{qid}')} (#{qid})" for qid in lua_list(d.get(10)) if qid in quests]
        q_ends = [f"{quests[qid].get(1, f'Quest #{qid}')} (#{qid})" for qid in lua_list(d.get(11)) if qid in quests]

        starts_s = ", ".join(q_starts[:8])
        ends_s = ", ".join(q_ends[:8])
        lvl_s = f"Lvl {min_lvl}" if min_lvl == max_lvl else f"Lvl {min_lvl}-{max_lvl}"
        tag_s = f" <{subname}>" if subname else ""
        summary = f"NPC: {name}{tag_s} (#{nid}) | {lvl_s} | {coords_str or primary_zone}"
        if starts_s:
            summary += f" | Starts Quests: {starts_s}"
        if ends_s:
            summary += f" | Ends Quests: {ends_s}"

        npc_rows.append((nid, name, subname, primary_zone, min_lvl, max_lvl, faction, coords_str, starts_s, ends_s, summary))
        fts_rows.append(("npc", nid, f"{name} {subname}".strip(), primary_zone, summary))

    cur.executemany("INSERT INTO npcs VALUES (?,?,?,?,?,?,?,?,?,?,?)", npc_rows)

    # Pre-format Objects
    obj_loc_str = {}
    for oid, d in objects.items():
        name = d.get(1) or f"Object #{oid}"
        primary_zone, coords_str = format_spawns(d.get(4), d.get(5), resolve_zone)
        obj_loc_str[oid] = f"Object '{name}' at {coords_str or primary_zone}"

    # Pre-format Items
    item_source_str = {}
    item_rows = []
    for iid, d in sorted(items.items()):
        name = d.get(1) or f"Item #{iid}"
        ilvl = d.get(9) or 0
        rlvl = d.get(10) or 0
        start_qid = d.get(5)
        start_q_str = f"{quests[start_qid].get(1, f'Quest #{start_qid}')} (#{start_qid})" if start_qid and start_qid in quests else ""
        q_zone = resolve_zone(quests[start_qid].get(17) or 0) if start_qid and start_qid in quests else ""

        # Resolve top NPC droppers (prioritizing quest zone + drop rate)
        npc_drop_ids = lua_list(d.get(2))
        rates = drop_rates.get(iid, {})
        sorted_npcs = sorted(
            npc_drop_ids,
            key=lambda n: (1 if q_zone and q_zone in npc_loc_str.get(n, "") else 0, rates.get(n, 0.0)),
            reverse=True,
        )
        drop_parts = []
        fallback_pct = ""
        if start_q_str and npc_drop_ids and not rates:
            # shortcut: Questie omits % for quest-starter drops; use 100% for single-boss starters, ~2-4% for multi-mob world starters
            fallback_pct = " (~100% drop)" if len(npc_drop_ids) == 1 else " (~2-4% drop)"
        for nid in sorted_npcs[:6]:
            nname = npcs.get(nid, {}).get(1, f"NPC #{nid}")
            nloc = npc_loc_str.get(nid, nname)
            pct = rates.get(nid)
            drop_parts.append(f"{nloc} ({pct:.1f}%)" if pct else f"{nloc}{fallback_pct}")

        # Object drops
        for oid in lua_list(d.get(3))[:3]:
            drop_parts.append(obj_loc_str.get(oid, f"Object #{oid}"))

        dropped_s = "; ".join(drop_parts)
        vendors_s = "; ".join(npc_loc_str.get(nid, f"NPC #{nid}") for nid in lua_list(d.get(14))[:4])

        src_desc = f"Item Drop '{name}' (#{iid})"
        if dropped_s:
            src_desc += f" from {dropped_s}"
        elif vendors_s:
            src_desc += f" sold by {vendors_s}"
        item_source_str[iid] = src_desc

        summary = f"Item: {name} (#{iid}, ReqLvl {rlvl}, iLvl {ilvl})"
        if start_q_str:
            summary += f" | Starts Quest: {start_q_str}"
        if dropped_s:
            summary += f" | Dropped by: {dropped_s}"
        if vendors_s:
            summary += f" | Sold by: {vendors_s}"

        item_rows.append((iid, name, rlvl, ilvl, start_q_str, dropped_s, vendors_s, summary))
        fts_rows.append(("item", iid, name, "", summary))

    cur.executemany("INSERT INTO items VALUES (?,?,?,?,?,?,?,?)", item_rows)

    # Pre-format Quests
    quest_rows = []
    for qid, d in sorted(quests.items()):
        name = d.get(1) or f"Quest #{qid}"
        if "UNUSE" in name or name.startswith("<TXT>") or name.startswith("[DEPRECATED"):
            continue
        rlvl = d.get(4) or 0
        qlvl = d.get(5) or rlvl
        faction = decode_faction(d.get(6) or 0)
        classes = decode_classes(d.get(7) or 0)
        z_or_s = d.get(17) or 0
        zone = resolve_zone(z_or_s)

        # startedBy = [npcIds, objIds, itemIds]
        sb = d.get(2)
        sb_npcs = lua_list(lua_idx(sb, 1))
        sb_objs = lua_list(lua_idx(sb, 2))
        sb_items = lua_list(lua_idx(sb, 3))
        sb_parts = []
        for nid in sb_npcs[:4]:
            sb_parts.append(f"NPC {npc_loc_str.get(nid, f'#{nid}')}")
        for oid in sb_objs[:3]:
            sb_parts.append(obj_loc_str.get(oid, f"Object #{oid}"))
        for iid in sb_items[:3]:
            sb_parts.append(item_source_str.get(iid, f"Item #{iid}"))
        if not sb_parts and d.get(11):
            # Fallback to sourceItemId if startedBy was empty
            siid = d.get(11)
            if siid in item_source_str and items.get(siid, {}).get(5) == qid:
                sb_parts.append(item_source_str[siid])
        started_by = "; ".join(sb_parts) or "Auto/Chain"

        # If zone was negative sortKey or Unknown, enrich with starter NPC's zone if possible
        if (z_or_s <= 0 or zone.startswith("Zone #")) and sb_npcs:
            first_nid = sb_npcs[0]
            if first_nid in npcs:
                nz, _ = format_spawns(npcs[first_nid].get(7), npcs[first_nid].get(9), resolve_zone)
                if nz and nz != "Unknown" and nz != zone:
                    zone = f"{zone} ({nz})" if z_or_s < 0 else nz

        # finishedBy = [npcIds, objIds]
        fb = d.get(3)
        fb_npcs = lua_list(lua_idx(fb, 1))
        fb_objs = lua_list(lua_idx(fb, 2))
        fb_parts = []
        for nid in fb_npcs[:4]:
            fb_parts.append(f"NPC {npc_loc_str.get(nid, f'#{nid}')}")
        for oid in fb_objs[:3]:
            fb_parts.append(obj_loc_str.get(oid, f"Object #{oid}"))
        finished_by = "; ".join(fb_parts) or "None"

        # Objectives text + structured targets
        obj_texts = lua_list(d.get(8))
        obj_str = " ".join(t.strip() for t in obj_texts if isinstance(t, str))
        # Also resolve objective NPCs/items from field 10: [creatureObjs, objObjs, itemObjs, repObj, killCredit]
        obj_struct = d.get(10)
        target_parts = []
        for entry in lua_list(lua_idx(obj_struct, 1))[:4]:
            elist = lua_list(entry)
            if elist:
                target_parts.append(npc_loc_str.get(elist[0], f"NPC #{elist[0]}"))
        for entry in lua_list(lua_idx(obj_struct, 3))[:4]:
            elist = lua_list(entry)
            if elist:
                target_parts.append(item_source_str.get(elist[0], f"Item #{elist[0]}"))
        if target_parts:
            obj_str = (obj_str + " [Targets: " + "; ".join(target_parts) + "]").strip()

        # Prereqs (field 12 preQuestGroup + field 13 preQuestSingle)
        pre_ids = lua_list(d.get(12)) + lua_list(d.get(13))
        prereqs = ", ".join(f"{quests[pid].get(1, f'#{pid}')} (#{pid})" for pid in pre_ids if pid in quests)

        # Next quest (field 22)
        next_qid = d.get(22)
        next_quest = f"{quests[next_qid].get(1, f'#{next_qid}')} (#{next_qid})" if next_qid and next_qid in quests else ""

        cls_tag = f" | Class: {classes}" if classes != "All" else ""
        summary = (
            f"Quest: {name} (#{qid}) | Zone: {zone} | ReqLvl {rlvl} (QuestLvl {qlvl}) | Faction: {faction}{cls_tag} "
            f"| Start: {started_by} | Turn-in: {finished_by}"
        )
        if obj_str:
            summary += f" | Objective: {obj_str}"
        if prereqs:
            summary += f" | Prereq: {prereqs}"
        if next_quest:
            summary += f" | Next: {next_quest}"

        quest_rows.append((qid, name, zone, rlvl, qlvl, faction, classes, started_by, finished_by, obj_str, prereqs, next_quest, summary))
        fts_rows.append(("quest", qid, name, zone, summary))

    cur.executemany("INSERT INTO quests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", quest_rows)
    cur.executemany("INSERT INTO wow_fts VALUES (?,?,?,?,?)", fts_rows)

    cur.execute("CREATE INDEX idx_quests_zone_lvl ON quests(zone, req_level, quest_level);")
    cur.execute("CREATE INDEX idx_quests_name ON quests(name COLLATE NOCASE);")
    cur.execute("CREATE INDEX idx_npcs_name ON npcs(name COLLATE NOCASE);")
    cur.execute("CREATE INDEX idx_items_name ON items(name COLLATE NOCASE);")
    conn.commit()
    conn.close()
    print(f"Built {OUT_DB} ({os.path.getsize(OUT_DB) / (1024*1024):.2f} MB) with {len(quest_rows):,} quests, {len(npc_rows):,} NPCs, {len(item_rows):,} items.")


if __name__ == "__main__":
    build_sqlite()
